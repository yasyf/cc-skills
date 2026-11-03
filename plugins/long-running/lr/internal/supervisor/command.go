package supervisor

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	prfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const runnerFile = "runner.json"

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "supervisor", Short: "The drive's one leased process, kept alive by launchd"}
	cmd.AddCommand(
		&cobra.Command{Use: "run", Short: "Hold the lease and tick until a newer generation or release takes over", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return run(cmd.Context(), a)
		}},
		&cobra.Command{Use: "install", Short: "Write this drive's launchd agent and bootstrap it", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return Install(cmd, a)
		}},
		&cobra.Command{Use: "uninstall", Short: "Boot this drive's launchd agent out and remove it", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return Uninstall(cmd, a)
		}},
	)
	return cmd
}

func run(ctx context.Context, a *app.App) error {
	if err := lanes.Adopt(lanes.Self); err != nil {
		return err
	}
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return err
	}
	if unwired := slices.DeleteFunc(slices.Clone(cfg.Movers), func(mover string) bool { return mover == moverLanes || mover == config.MoverLanding || mover == config.MoverGrading }); len(unwired) > 0 {
		return fmt.Errorf("lr supervisor run wires the lanes, landing and grading movers, not %s", strings.Join(unwired, ", "))
	}
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	env, err := lanes.NewEnv(a.Runner, cfg, state, a.Now)
	if err != nil {
		return err
	}
	deps, err := wired(ctx, a, env, s)
	if err != nil {
		return err
	}
	version := Installed(env.Claude)
	upgrade := &Upgrade{Env: env, Running: version}
	deps.Stale = upgrade.Stale
	reports, ended, err := Run(ctx, s, cfg.Drive, version, os.Getpid(), deps, 0)
	if err != nil {
		return err
	}
	a.Log.Info("supervisor ended", "how", string(ended), "ticks", len(reports))
	if ended == EndedRelease {
		return Reexec(env.Claude, syscall.Exec)
	}
	return nil
}

func wired(ctx context.Context, a *app.App, env lanes.Env, s *store.Store) (Deps, error) {
	checks, err := env.Checks(s, a.Log)
	if err != nil {
		return Deps{}, err
	}
	laneDeps, err := env.Deps(ctx)
	if err != nil {
		return Deps{}, err
	}
	deps := Deps{
		Movers: env.Config.Movers,
		Clock:  env.Now,
		Sleep:  time.Sleep,
		Lanes: func(now time.Time, generation int64) (lanes.Outcome, error) {
			return lanes.Tick(s, laneDeps, now, generation)
		},
		Checks: checks,
		Log:    a.Log,
	}
	if !slices.Contains(env.Config.Movers, config.MoverLanding) {
		return deps, nil
	}
	restacker, err := landing.NewRemote(a, filepath.Join(env.State, runnerFile), lanes.Self, "")
	if err != nil {
		return Deps{}, err
	}
	sender := bus.Bus{Drive: env.Config.Drive, Lane: lanes.Self}
	deps.Snapshot = prfacts.New(a, env.Config, s).Snapshot
	deps.Post = func(to []string, body bus.Body) error {
		_, err := sender.Post(ctx, to, body)
		return err
	}
	deps.Queue = landing.Trunk{Runner: a.Runner, Checkout: env.Config.Checkout, Branch: env.Config.Trunk, Script: env.Config.QueueScript, KeyCommand: env.Config.QueueKey}
	deps.Restacker = restacker
	deps.Trunk = env.Config.Trunk
	if !slices.Contains(env.Config.Movers, config.MoverGrading) {
		return deps, nil
	}
	deps.Pool, err = grading.NewLivePool(a, filepath.Join(env.State, runnerFile), s)
	return deps, err
}

func launchd(a *app.App, out *cobra.Command) (Launchd, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return Launchd{}, err
	}
	return Launchd{Runner: a.Runner, Home: home, UID: os.Getuid(), Sleep: time.Sleep, Out: out.OutOrStdout()}, nil
}

func Install(cmd *cobra.Command, a *app.App) error {
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return err
	}
	env, err := lanes.NewEnv(a.Runner, cfg, state, a.Now)
	if err != nil {
		return err
	}
	agents, err := launchd(a, cmd)
	if err != nil {
		return err
	}
	path := os.Getenv("PATH")
	if path == "" {
		return errors.New("PATH is empty; launchd would run the supervisor with no tools")
	}
	return agents.Install(cmd.Context(), Agent{
		Drive:  cfg.Drive,
		Config: filepath.Join(state, "lr.json"),
		Binary: filepath.Join(env.Claude, "long-running", "current", "bin", "lr"),
		Path:   path,
		Log:    filepath.Join(state, "supervisor.log"),
	})
}

func Uninstall(cmd *cobra.Command, a *app.App) error {
	cfg, _, err := lanes.Loaded(a)
	if err != nil {
		return err
	}
	agents, err := launchd(a, cmd)
	if err != nil {
		return err
	}
	return agents.Uninstall(cmd.Context(), cfg.Drive)
}
