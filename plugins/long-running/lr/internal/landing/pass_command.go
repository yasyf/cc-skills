package landing

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
)

const (
	self       = "supervisor"
	storeFile  = "lr.sqlite"
	runnerFile = "runner.json"
)

func passCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "pass",
		Short: "Read every open PR's facts once, open the one item each needs, and act on what the supervisor does itself",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return runPass(cmd.Context(), a, cmd.OutOrStdout())
		},
	}
}

func runPass(ctx context.Context, a *app.App, stdout io.Writer) error {
	cfg, err := a.Config()
	if err != nil {
		return err
	}
	dir := filepath.Dir(a.ConfigPath)
	s, err := store.Open(filepath.Join(dir, storeFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	snapshot, err := facts.New(a, cfg, s).Snapshot(ctx)
	if err != nil {
		return fmt.Errorf("pass skipped: %w", err)
	}
	now := a.Now()
	trunk := Trunk{Runner: a.Runner, Checkout: cfg.Checkout, Worktree: filepath.Join(dir, QueueWorktree), Branch: cfg.Trunk, Script: cfg.QueueScript, KeyCommand: cfg.QueueKey}
	if slices.Contains(cfg.Movers, config.MoverLanding) {
		landingRunner, err := lanes.ReadRunnerLanding(dir)
		if err != nil {
			return err
		}
		if landingRunner.CIBuild != "" {
			trunk.Logs = TrunkWatch{Runner: a.Runner, Checkout: cfg.Checkout, Config: TrunkConfig{CIBuild: landingRunner.CIBuild, TokenCommand: landingRunner.CITokenCommand}}.FailedJob
		}
		read, err := Evictions{Store: s, Queue: trunk, Drive: cfg.Drive, Now: a.Now}.Pass(ctx, snapshot)
		if err != nil {
			fmt.Fprintf(stdout, "%s an eviction went unread or untold: %v\n", store.Stamp(now), err)
		}
		snapshot = read
	}
	if slices.Contains(cfg.Movers, config.MoverLanding) {
		posted, err := SweepGos(ctx, a, cfg, s, snapshot, now, stdout)
		if err != nil {
			return fmt.Errorf("the go sweep stopped: %w", err)
		}
		snapshot = Exempted(snapshot, posted)
	}
	b := bus.Bus{Drive: cfg.Drive, Lane: self}
	post := func(to []string, body bus.Body) error {
		_, err := b.Post(ctx, to, body)
		return err
	}
	route, err := watch.Desks(s)
	if err != nil {
		return err
	}
	out, err := pass.Run(s, snapshot, now, post, pass.Live(cfg.Movers), route, func(lane string, pr int) (string, error) { return lanes.ItemOwner(s, cfg.Drive, lane, pr) }, func(lane, text string) error {
		return orca.Poster{Bus: orca.CCI{}, Runner: a.Runner, Env: os.Getenv, Now: a.Now, Sleep: time.Sleep, Out: stdout, Drive: cfg.Drive, Author: self}.Relay(ctx, lane, text)
	})
	if err != nil {
		return err
	}
	fmt.Fprintf(stdout, "%s pass prs=%d opened=%d escalated=%d\n", store.Stamp(now), len(snapshot), len(out.Opened), len(out.Escalated))
	if !slices.Contains(cfg.Movers, config.MoverLanding) {
		return nil
	}
	if err := Announce(ctx, s, cfg.Drive, cfg.Trunk, out.Landed, route); err != nil {
		return fmt.Errorf("a landed record went unposted: %w", err)
	}
	if err := CarryTaps(ctx, cfg.Drive, out.Carried); err != nil {
		return fmt.Errorf("a tap did not carry: %w", err)
	}
	for _, f := range snapshot {
		if why := out.Stuck[f.Number]; why != "" {
			fmt.Fprintf(stdout, "%s STUCK #%d at %s: %s\n", store.Stamp(now), f.Number, shortHead(f.Head), why)
		}
	}
	if err := SayStuck(ctx, s, cfg.Drive, snapshot, out.Stuck); err != nil {
		return fmt.Errorf("a stuck head's reason went unposted: %w", err)
	}
	if err := WarnPersonQueued(ctx, cfg.Drive, snapshot); err != nil {
		return fmt.Errorf("a person's queue entry went unwarned: %w", err)
	}
	remote, err := NewRemote(a, filepath.Join(dir, runnerFile), self, "")
	if err != nil {
		return err
	}
	gates, err := NewGates(a, s, cfg, filepath.Join(dir, runnerFile))
	if err != nil {
		return err
	}
	done, err := Act(ctx, s, HoldGuard{Queue: trunk, Held: facts.New(a, cfg, s).HeldPRs}, remote, gates.Admit(ctx, Clear(out.Acts, snapshot, cfg.Trunk), snapshot), now)
	fmt.Fprintf(stdout, "%s landing enqueued=%d resubmitted=%d dequeued=%d restacked=%d conflicts=%d\n", store.Stamp(now), len(done.Enqueued), len(done.Resubmitted), len(done.Dequeued), len(done.Restacked), len(done.Conflicts))
	if err != nil {
		return fmt.Errorf("movers stopped mid-pass: %w", err)
	}
	return nil
}
