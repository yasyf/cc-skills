package supervisor

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "supervisor", Short: "The drive's one leased process, kept alive by launchd"}
	cmd.AddCommand(
		&cobra.Command{Use: "run", Short: "Hold the lease and tick until a newer generation or release takes over", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return run(cmd.Context(), a)
		}},
		&cobra.Command{Use: "install", Short: "Write this drive's launchd agent and bootstrap it", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return install(cmd, a)
		}},
		&cobra.Command{Use: "uninstall", Short: "Boot this drive's launchd agent out and remove it", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return uninstall(cmd, a)
		}},
	)
	return cmd
}

func run(ctx context.Context, a *app.App) error {
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return err
	}
	if runsPass(cfg.Movers) {
		return fmt.Errorf("movers %s need the facts read, which this build does not link yet; keep lr.json at [lanes] until the cutover", strings.Join(cfg.Movers, ", "))
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
	deps, err := wired(ctx, a.Log, env, s)
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

func wired(ctx context.Context, log *slog.Logger, env lanes.Env, s *store.Store) (Deps, error) {
	checks, err := env.Checks(s, log)
	if err != nil {
		return Deps{}, err
	}
	laneDeps, err := env.Deps(ctx)
	if err != nil {
		return Deps{}, err
	}
	return Deps{
		Movers: env.Config.Movers,
		Clock:  env.Now,
		Sleep:  time.Sleep,
		Lanes: func(now time.Time, generation int64) (lanes.Outcome, error) {
			return lanes.Tick(s, laneDeps, now, generation)
		},
		Checks: checks,
		Log:    log,
	}, nil
}

func launchd(a *app.App, out *cobra.Command) (Launchd, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return Launchd{}, err
	}
	return Launchd{Runner: a.Runner, Home: home, UID: os.Getuid(), Sleep: time.Sleep, Out: out.OutOrStdout()}, nil
}

func install(cmd *cobra.Command, a *app.App) error {
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

func uninstall(cmd *cobra.Command, a *app.App) error {
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
