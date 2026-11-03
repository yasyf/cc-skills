package landing

import (
	"context"
	"fmt"
	"io"
	"path/filepath"
	"slices"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
)

const (
	self       = "supervisor"
	storeFile  = "lr.sqlite"
	shadowFile = "lr-shadow.sqlite"
	runnerFile = "runner.json"
)

func passCommand(a *app.App) *cobra.Command {
	var shadow bool
	cmd := &cobra.Command{
		Use:   "pass",
		Short: "Read every open PR's facts once, open the one item each needs, and act on what the supervisor does itself",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return runPass(cmd.Context(), a, shadow, cmd.OutOrStdout())
		},
	}
	cmd.Flags().BoolVar(&shadow, "shadow", false, "run over lr-shadow.sqlite, post one note per disagreement with the live runner, and act on nothing")
	return cmd
}

func runPass(ctx context.Context, a *app.App, shadow bool, stdout io.Writer) error {
	cfg, err := a.Config()
	if err != nil {
		return err
	}
	dir := filepath.Dir(a.ConfigPath)
	name := storeFile
	if shadow {
		name = shadowFile
	}
	s, err := store.Open(filepath.Join(dir, name))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	snapshot, err := facts.New(a, cfg, s).Snapshot(ctx)
	if err != nil {
		return fmt.Errorf("pass skipped: %w", err)
	}
	now := a.Now()
	b := bus.Bus{Drive: cfg.Drive, Lane: self}
	post := func(to []string, body bus.Body) error {
		_, err := b.Post(ctx, to, body)
		return err
	}
	if shadow {
		diffs, err := pass.Shadow(s, snapshot, now, post)
		fmt.Fprintf(stdout, "%s shadow prs=%d disagreements=%d\n", store.Stamp(now), len(snapshot), len(diffs))
		return err
	}
	route, err := watch.Desks(s)
	if err != nil {
		return err
	}
	out, err := pass.Run(s, snapshot, now, post, pass.Live(cfg.Movers), route)
	if err != nil {
		return err
	}
	fmt.Fprintf(stdout, "%s pass prs=%d opened=%d escalated=%d\n", store.Stamp(now), len(snapshot), len(out.Opened), len(out.Escalated))
	if !slices.Contains(cfg.Movers, config.MoverLanding) {
		return nil
	}
	remote, err := NewRemote(a, filepath.Join(dir, runnerFile), self, "")
	if err != nil {
		return err
	}
	trunk := Trunk{Runner: a.Runner, Checkout: cfg.Checkout, Branch: cfg.Trunk, Script: cfg.QueueScript, KeyCommand: cfg.QueueKey}
	done, err := Act(ctx, s, trunk, remote, Clear(out.Acts, snapshot, cfg.Trunk), now)
	fmt.Fprintf(stdout, "%s landing enqueued=%d resubmitted=%d dequeued=%d restacked=%d conflicts=%d\n", store.Stamp(now), len(done.Enqueued), len(done.Resubmitted), len(done.Dequeued), len(done.Restacked), len(done.Conflicts))
	if err != nil {
		return fmt.Errorf("movers stopped mid-pass: %w", err)
	}
	return nil
}
