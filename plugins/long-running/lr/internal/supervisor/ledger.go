package supervisor

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	ledgerEvery      = 2 * time.Minute
	ledgerLimit      = 30 * time.Minute
	ledgerCursor     = "pr-ledger"
	ledgerBack       = time.Hour
	ledgerLimited    = 75
	ledgerOutage     = 10 * time.Minute
	ledgerNewLane    = 24 * time.Hour
	ledgerLanePrefix = "lane/"
)

var ledgerPRKey = regexp.MustCompile(`^(?:[\w.-]+/[\w.-]+#)?\d+$`)

type prLedger struct {
	env          lanes.Env
	store        *store.Store
	id           string
	tokenCommand []string
	limited      time.Time
	told         bool
}

func (l *prLedger) pass(ctx context.Context) error {
	advanced, registered := l.advance(ctx), l.register(ctx)
	fresh, refreshed := l.refresh(ctx)
	var dropped error
	if fresh {
		dropped = l.dropRetired(ctx)
	}
	return errors.Join(advanced, registered, refreshed, dropped, l.run(ctx, nil, "reconcile", "--repo", l.env.Config.Repo, "--ledger", l.id, "--checkout", l.env.Config.Checkout))
}

func (l *prLedger) refresh(ctx context.Context) (bool, error) {
	env, unread := l.buildkite(ctx)
	fresh, err := l.refreshWith(ctx, env)
	return fresh, errors.Join(unread, err)
}

func (l *prLedger) refreshWith(ctx context.Context, env []string) (fresh bool, err error) {
	err = l.run(ctx, env, "refresh", "--repo", l.env.Config.Repo, "--ledger", l.id)
	var exit *shell.ExitError
	if !errors.As(err, &exit) || exit.Code != ledgerLimited {
		l.limited, l.told = time.Time{}, false
		return err == nil, err
	}
	now := l.env.Now()
	if l.limited.IsZero() {
		l.limited = now
	}
	if l.told || now.Sub(l.limited) < ledgerOutage {
		return false, nil
	}
	l.told = true
	return false, err
}

func (l *prLedger) dropRetired(ctx context.Context) error {
	rows, err := l.store.LedgerRows(l.id)
	if err != nil {
		return err
	}
	var failed []error
	for key, row := range rows {
		name, isLane := strings.CutPrefix(key, ledgerLanePrefix)
		if !isLane {
			continue
		}
		retired, err := l.laneRetired(name, row)
		if err != nil {
			failed = append(failed, err)
			continue
		}
		if retired && !hasOpenPR(rows, name, row["branch_prefix"]) {
			failed = append(failed, l.dropLaneRow(ctx, key))
		}
	}
	return errors.Join(failed...)
}

func (l *prLedger) laneRetired(name string, row map[string]string) (bool, error) {
	lane, known, err := l.store.Lane(name)
	if err != nil {
		return false, err
	}
	if known {
		return lane.State == store.LaneRetired, nil
	}
	registered, err := time.Parse(time.RFC3339, row["registered_at"])
	if err != nil {
		return false, err
	}
	return l.env.Now().Sub(registered) >= ledgerNewLane, nil
}

func hasOpenPR(rows map[string]map[string]string, lane, prefix string) bool {
	for key, row := range rows {
		if ledgerPRKey.MatchString(key) && row["state"] == "open" && (row["lane"] == lane || onBranchPrefix(row["branch"], prefix)) {
			return true
		}
	}
	return false
}

func onBranchPrefix(branch, prefix string) bool {
	if strings.HasSuffix(prefix, "/") || strings.HasSuffix(prefix, "-") {
		return strings.HasPrefix(branch, prefix)
	}
	return branch == prefix
}

func (l *prLedger) dropLaneRow(ctx context.Context, key string) error {
	if _, err := l.env.Runner.Run(ctx, shell.Command{Argv: []string{"ccn", "-R", l.env.Config.Checkout, "ledger", "row", "rm", l.id, "--key", key}, Timeout: ledgerLimit}); err != nil {
		return err
	}
	return l.store.DropLedgerRow(l.id, key)
}

func (l *prLedger) advance(ctx context.Context) error {
	lock, err := landing.LockTree(l.env.Config.Checkout)
	if err != nil {
		return err
	}
	defer lock.Close()
	git := []string{"git", "-C", l.env.Config.Checkout}
	fetched := l.env.Config.Refs() + "ledger/" + l.env.Config.Trunk
	if _, err := l.env.Runner.Run(ctx, shell.Command{Argv: append(slices.Clone(git), "fetch", "-q", "--no-tags", "--refmap=", "origin", "+refs/heads/"+l.env.Config.Trunk+":"+fetched), Timeout: ledgerLimit}); err != nil {
		return err
	}
	_, err = l.env.Runner.Run(ctx, shell.Command{Argv: append(git, "merge", "-q", "--ff-only", fetched), Timeout: ledgerLimit})
	return err
}

func (l *prLedger) register(ctx context.Context) error {
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
			failed = append(failed, l.run(ctx, nil, "register", "--ledger", l.id, "--repo", l.env.Config.Repo, "--lane", record.Lane, "--pr", number))
		}
		failed = append(failed, l.store.Advance(ledgerCursor, record.Seq))
	}
	return errors.Join(failed...)
}

func (l *prLedger) buildkite(ctx context.Context) ([]string, error) {
	if len(l.tokenCommand) == 0 {
		return nil, nil
	}
	value, err := (&bkread.Token{Command: l.tokenCommand}).Value(ctx, l.env.Runner)
	if err != nil {
		return nil, fmt.Errorf("refresh ran with no Buildkite token, so failing checks carry no job or error line: %w", err)
	}
	return []string{"BUILDKITE_TOKEN=" + value}, nil
}

func (l *prLedger) run(ctx context.Context, env []string, argv ...string) error {
	tool := filepath.Join(l.env.Claude, "long-running", "current", "bin", "ledger.py")
	_, err := l.env.Runner.Run(ctx, shell.Command{
		Argv:    append([]string{tool, "-C", l.env.Config.Checkout}, argv...),
		Env:     append([]string{"CLAUDE_LONG_RUNNING_DRIVE=" + l.env.Config.Drive, "CLAUDE_LONG_RUNNING_REFS=" + l.env.Config.Refs()}, env...),
		Timeout: ledgerLimit,
	})
	return err
}
