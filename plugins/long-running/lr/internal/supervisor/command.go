package supervisor

import (
	"context"
	"errors"
	"fmt"
	"io"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/replay"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/waste"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
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
	unwired := slices.DeleteFunc(slices.Clone(cfg.Movers), func(mover string) bool {
		return mover == moverLanes || mover == config.MoverLanding || mover == config.MoverGrading
	})
	if len(unwired) > 0 {
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
	sender := bus.Bus{Drive: env.Config.Drive, Lane: lanes.Self}
	post := func(to []string, body bus.Body) error {
		_, err := sender.Post(ctx, to, body)
		return err
	}
	if slices.Contains(env.Config.Movers, moverLanes) {
		installs, err := install.Installs(env.Claude)
		if err != nil {
			return Deps{}, err
		}
		counter := replay.Deps{Runner: a.Runner, CCX: a.CCX, Installs: installs, Now: a.Now, Stdout: io.Discard}
		wasteConfig, err := waste.ReadConfig(env.State)
		if err != nil {
			return Deps{}, err
		}
		report := waste.Poster{Sources: waste.Live(env.Bus, a.Runner, s), Config: wasteConfig, Store: s, Drive: env.Config, Now: a.Now, Post: post}
		rejoin := orca.Rejoin{Runner: a.Runner, Store: s, Config: filepath.Join(env.State, runnerFile), Sleep: time.Sleep, Out: os.Stderr}
		checks = append(checks,
			(&background{name: "waste-report", every: wasteEvery, run: report.Pass, store: s, post: post, log: a.Log, to: wasteConfig.Lane}).Check,
			(&watch.Stalls{Store: s, Drive: env.Config.Drive, Runner: a.Runner, Post: post, Log: a.Log}).Check,
			(&lanes.MutePushback{Env: env, Log: a.Log}).Check,
			(&background{name: "relay-rejoin", every: rejoinEvery, run: rejoin.Pass, store: s, post: post, log: a.Log}).Check,
			(&background{name: "recurrence-count", every: recurEvery, run: func(ctx context.Context) error { return counter.Recur(ctx, env.Config.Drive) }, store: s, post: post, log: a.Log}).Check,
			(&background{name: "judge-watch", every: judgeEvery, run: func(ctx context.Context) error {
				_, err := a.Runner.Run(ctx, shell.Command{Argv: []string{filepath.Join(env.Claude, "long-running", "current", "bin", "judge_watch.py"), "--config", filepath.Join(env.State, runnerFile)}, Timeout: judgeLimit})
				return err
			}, store: s, post: post, log: a.Log}).Check,
		)
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
	runner, err := lanes.ReadRunnerLanding(env.State)
	if err != nil {
		return Deps{}, err
	}
	if runner.Ledger != "" {
		deps.Checks = append(deps.Checks, (&background{name: "pr-ledger", every: ledgerEvery, run: prLedger{env, s, runner.Ledger}.pass, store: s, post: post, log: a.Log}).Check)
	}
	if runner.TrunkCheck != "" {
		trunk := &landing.TrunkWatch{
			Runner:   a.Runner,
			CCX:      a.CCX,
			Store:    s,
			Drive:    env.Config.Drive,
			Repo:     env.Config.Repo,
			Trunk:    env.Config.Trunk,
			Checkout: env.Config.Checkout,
			Config:   landing.TrunkConfig{Check: runner.TrunkCheck, CIBuild: runner.CIBuild, FlakeLane: runner.FlakeLane, Channel: runner.FlakyChannel},
			Now:      a.Now,
		}
		deps.Checks = append(deps.Checks, (&background{name: "trunk-watch", every: PassEvery, run: trunk.Pass, store: s, post: post, log: a.Log}).Check)
	}
	deps.Snapshot = prfacts.New(a, env.Config, s).Snapshot
	deps.Post = post
	deps.Queue = landing.Trunk{Runner: a.Runner, Checkout: env.Config.Checkout, Branch: env.Config.Trunk, Script: env.Config.QueueScript, KeyCommand: env.Config.QueueKey}
	deps.Restacker = restacker
	gates, err := landing.NewGates(a, s, env.Config, filepath.Join(env.State, runnerFile))
	if err != nil {
		return Deps{}, err
	}
	fixLanes, err := orca.FixLanes(a)
	if err != nil {
		return Deps{}, err
	}
	squash, err := landing.NewSquash(a, s, env.Config, restacker, fixLanes)
	if err != nil {
		return Deps{}, err
	}
	liveClocks, err := orca.LiveClocks(a)
	if err != nil {
		return Deps{}, err
	}
	owed, err := landing.NewReleaseOwed(a, s, env.Config, filepath.Join(env.State, runnerFile), squash, liveClocks)
	if err != nil {
		return Deps{}, err
	}
	if owed.Owner != "" {
		deps.Checks = append(deps.Checks, (&background{name: "release-owed", every: time.Minute, run: owed.Pass, store: s, post: post, log: a.Log}).Check)
	}
	queueFell := landing.QueueFell{Store: s, Drive: env.Config.Drive, Repo: env.Config.Repo}
	deps.Checks = append(deps.Checks, (&background{name: "trunk-queue", every: PassEvery, run: queueFell.Pass, store: s, post: post, log: a.Log}).Check)
	outlived := landing.Outlived{Store: s, Drive: env.Config.Drive, Repo: env.Config.Repo}
	deps.Checks = append(deps.Checks, (&background{name: "outlived-blockers", every: PassEvery, run: outlived.Pass, store: s, post: post, log: a.Log}).Check)
	quota := landing.Quota{Runner: a.Runner, Drive: env.Config.Drive, Repo: env.Config.Repo}
	deps.Checks = append(deps.Checks, (&background{name: "github-quota", every: PassEvery, run: quota.Pass, store: s, post: post, log: a.Log}).Check)
	deps.Admit = func(ctx context.Context, acts []pass.Act, snapshot []machine.Facts) []pass.Act {
		return gates.Admit(ctx, squash.Admit(ctx, acts, snapshot), snapshot)
	}
	deps.Trunk = env.Config.Trunk
	orcaRunner, err := config.LoadRunner(filepath.Join(env.State, runnerFile))
	if err != nil {
		return Deps{}, err
	}
	if orcaRunner.Orca.GoRelay {
		agents, err := launchd.New(a.Runner, os.Stdout)
		if err != nil {
			return Deps{}, err
		}
		if err := agents.Install(ctx, agentOf(env).RelayJob(orcaRunner.Orca.DeskInbox)); err != nil {
			a.Log.Warn("the relay's launchd job is not installed", "err", err)
		}
		deps.Checks = append(deps.Checks, NewRelayKeeper(env.State, orcaRunner.Orca.DeskInbox, s, post, a.Log).Check)
	}
	if !slices.Contains(env.Config.Movers, config.MoverGrading) {
		return deps, nil
	}
	deps.Pool, err = grading.NewLivePool(a, filepath.Join(env.State, runnerFile), s)
	return deps, err
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
	agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
	if err != nil {
		return err
	}
	agent := agentOf(env)
	if agent.Path == "" {
		return errors.New("PATH is empty; launchd would run the supervisor with no tools")
	}
	runner, err := config.LoadRunner(filepath.Join(state, runnerFile))
	if err != nil {
		return err
	}
	if runner.Orca.GoRelay {
		if err := agents.Install(cmd.Context(), agent.RelayJob(runner.Orca.DeskInbox)); err != nil {
			return err
		}
	}
	return agents.Install(cmd.Context(), agent.Job())
}

func agentOf(env lanes.Env) Agent {
	return Agent{
		Drive:  env.Config.Drive,
		Config: filepath.Join(env.State, "lr.json"),
		Binary: filepath.Join(env.Claude, "long-running", "current", "bin", "lr"),
		Path:   os.Getenv("PATH"),
		Log:    filepath.Join(env.State, "supervisor.log"),
	}
}

func Uninstall(cmd *cobra.Command, a *app.App) error {
	cfg, _, err := lanes.Loaded(a)
	if err != nil {
		return err
	}
	agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
	if err != nil {
		return err
	}
	if err := agents.Uninstall(cmd.Context(), RelayLabel(cfg.Drive)); err != nil {
		return err
	}
	return agents.Uninstall(cmd.Context(), Label(cfg.Drive))
}
