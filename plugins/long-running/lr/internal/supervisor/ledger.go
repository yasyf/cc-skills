package supervisor

import (
	"context"
	"errors"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	ledgerEvery  = 2 * time.Minute
	ledgerLimit  = 30 * time.Minute
	ledgerCursor = "pr-ledger"
	ledgerBack   = time.Hour
)

type prLedger struct {
	env   lanes.Env
	store *store.Store
	id    string
}

func (l prLedger) pass(ctx context.Context) error {
	repo, checkout := l.env.Config.Repo, l.env.Config.Checkout
	return errors.Join(
		l.advance(ctx),
		l.register(ctx),
		l.run(ctx, "refresh", "--repo", repo, "--ledger", l.id),
		l.run(ctx, "reconcile", "--repo", repo, "--ledger", l.id, "--checkout", checkout),
	)
}

func (l prLedger) advance(ctx context.Context) error {
	git := []string{"git", "-C", l.env.Config.Checkout}
	if _, err := l.env.Runner.Run(ctx, shell.Command{Argv: append(slices.Clone(git), "fetch", "-q", "--no-tags", "--refmap=", "origin", "refs/heads/"+l.env.Config.Trunk), Timeout: ledgerLimit}); err != nil {
		return err
	}
	_, err := l.env.Runner.Run(ctx, shell.Command{Argv: append(git, "merge", "-q", "--ff-only", "FETCH_HEAD"), Timeout: ledgerLimit})
	return err
}

func (l prLedger) register(ctx context.Context) error {
	after, err := l.store.Cursor(ledgerCursor)
	if err != nil {
		return err
	}
	query := client.Query{Drive: l.env.Config.Drive, After: after, Kinds: []string{"head", "opened"}}
	if after == 0 {
		query.Since = l.env.Now().Add(-ledgerBack)
	}
	records, err := l.env.Bus.Tail(ctx, query)
	if err != nil {
		return err
	}
	rows, err := l.store.LedgerRows(l.id)
	if err != nil {
		return err
	}
	var failed []error
	named := map[string]bool{}
	for _, record := range records {
		for _, pr := range record.Refs.PRs {
			number := strconv.Itoa(pr.Number)
			if pr.Repo != "" && !strings.EqualFold(pr.Repo, l.env.Config.Repo) || rows[number] != nil || named[number] {
				continue
			}
			named[number] = true
			failed = append(failed, l.run(ctx, "register", "--ledger", l.id, "--repo", l.env.Config.Repo, "--lane", record.Lane, "--pr", number))
		}
		failed = append(failed, l.store.Advance(ledgerCursor, record.Seq))
	}
	return errors.Join(failed...)
}

func (l prLedger) run(ctx context.Context, argv ...string) error {
	tool := filepath.Join(l.env.Claude, "long-running", "current", "bin", "ledger.py")
	_, err := l.env.Runner.Run(ctx, shell.Command{
		Argv:    append([]string{tool, "-C", l.env.Config.Checkout}, argv...),
		Env:     []string{"CLAUDE_LONG_RUNNING_DRIVE=" + l.env.Config.Drive},
		Timeout: ledgerLimit,
	})
	return err
}
