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
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/choices"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	prfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/findings"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/profile"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/replay"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/update"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/waste"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/prompts"
)

const (
	runnerFile    = "runner.json"
	configFile    = "lr.json"
	storeRunLimit = 150 * time.Second
	profileFile   = "supervisor-pprof.sock"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "supervisor", Short: "The drive's one leased process, kept alive by launchd"}
	cmd.AddCommand(
		&cobra.Command{Use: "run", Short: "Hold the lease and tick until a newer generation or release takes over", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return run(cmd.Context(), a, cmd.Root().Version)
		}},
		&cobra.Command{Use: "install", Short: "Write this drive's launchd agent and bootstrap it", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return Install(cmd, a)
		}},
		&cobra.Command{Use: "uninstall", Short: "Boot this drive's launchd agent out and remove it", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return Uninstall(cmd, a)
		}},
		&cobra.Command{Use: "status", Short: "Print the running supervisor's binary, the pin, the served and installed pack, and why it has not re-executed", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, _ []string) error {
			return Status(cmd, a)
		}},
	)
	return cmd
}

func run(ctx context.Context, a *app.App, lrVersion string) error {
	if err := lanes.Adopt(lanes.Self); err != nil {
		return err
	}
	read := a.Now()
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return err
	}
	settings, err := readSettings(state)
	if err != nil {
		return err
	}
	if gone := missing(ctx, a.Runner, shell.LookPath, cfg); len(gone) > 0 {
		text := "SUPERVISOR-REFUSED the supervisor will not start: " + strings.Join(gone, "; ")
		_, err := client.Post(ctx, client.Draft{Drive: cfg.Drive, Lane: lanes.Self, Kind: "blocker", To: []string{lanes.Root}, ID: "supervisor-start", Text: text})
		return errors.Join(errors.New(text), err)
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
	deps, err := wired(ctx, a, env, s, lrVersion)
	if err != nil {
		return err
	}
	if stop, err := profile.Serve(filepath.Join(state, profileFile), a.Log); err != nil {
		a.Log.Warn("the supervisor serves no profiles", "err", err)
	} else {
		defer stop()
	}
	version := Installed(env.Claude)
	upgrade := &Upgrade{Env: env, Running: version, Binary: lrVersion, Read: read, Settings: settings, Holding: lanes.GCPassRunning, Yield: lanes.YieldGC, Sleep: time.Sleep, Serve: func(installed string) error {
		return s.ServeVersion(cfg.Drive, os.Getpid(), installed)
	}}
	deps.Stale = upgrade.Stale
	deps.Checks = append(deps.Checks, upgrade.YieldOnceOwed)
	_, ended, err := Run(ctx, s, cfg.Drive, version, os.Getpid(), deps, 0)
	if err != nil {
		return err
	}
	a.Log.Info("supervisor ended", "how", string(ended))
	if ended == EndedRelease {
		return Reexec(env.Claude, syscall.Exec)
	}
	return nil
}

func storeVerbRun(runner shell.Runner, driveName string) askdesk.Run {
	return func(ctx context.Context, argv []string, lane string) (string, string, int, error) {
		self, err := os.Executable()
		if err != nil {
			return "", "", 0, err
		}
		ran, err := runner.Run(ctx, shell.Command{Argv: append([]string{self}, argv...), Env: []string{drive.Env + "=" + driveName, "LR_DRIVE=" + driveName, lanes.LaneEnv + "=" + lane}, Timeout: storeRunLimit})
		var exit *shell.ExitError
		if errors.As(err, &exit) {
			return ran.Stdout, ran.Stderr, exit.Code, nil
		}
		return ran.Stdout, ran.Stderr, 0, err
	}
}

func wired(ctx context.Context, a *app.App, env lanes.Env, s *store.Store, lrVersion string) (Deps, error) {
	sender := bus.Bus{Drive: env.Config.Drive, Lane: lanes.Self, Bodies: filepath.Join(env.State, bus.BodiesDir)}
	post := func(to []string, body bus.Body) error {
		_, err := sender.Post(ctx, to, body)
		return err
	}
	var checks []lanes.Check
	var dropStale func(context.Context, []machine.Facts) error
	var laneDeps lanes.Deps
	var squashFixes func(context.Context, []machine.Facts) error
	if slices.Contains(env.Config.Movers, moverLanes) {
		monitorLane, err := orca.MonitorLane(a)
		if err != nil {
			return Deps{}, err
		}
		env.MonitorLane = monitorLane
		if checks, err = env.Checks(ctx, s, a.Log); err != nil {
			return Deps{}, err
		}
		if laneDeps, err = env.Deps(ctx); err != nil {
			return Deps{}, err
		}
		installs, err := install.Installs(env.Claude)
		if err != nil {
			return Deps{}, err
		}
		counter := replay.Deps{Runner: a.Runner, CCX: a.CCX, Installs: installs, Now: a.Now, Stdout: io.Discard, Tail: env.Bus.Tail}
		wasteConfig, err := waste.ReadConfig(env.State)
		if err != nil {
			return Deps{}, err
		}
		running, err := waste.Current(a.CCX, env.Claude, lrVersion)
		if err != nil {
			return Deps{}, err
		}
		report := waste.Poster{Sources: waste.Live(env.Bus, a.Runner, s, running), Config: wasteConfig, Store: s, Drive: env.Config, Now: a.Now, Post: post}
		rejoin := orca.Rejoin{Runner: a.Runner, Store: s, Config: filepath.Join(env.State, runnerFile), Sleep: time.Sleep, Out: os.Stderr}
		promptWatch := prompts.NewWatch(a, env.Config.Drive)
		checks = append(checks,
			(&background{name: "waste-report", every: wasteEvery, run: report.Pass, store: s, post: post, log: a.Log, to: wasteConfig.Lane}).Check,
			(&watch.Stalls{Store: s, Drive: env.Config.Drive, Runner: a.Runner, Post: post, Log: a.Log}).Check,
			(&lanes.MutePushback{Env: env, Log: a.Log}).Check,
			(&background{name: "relay-rejoin", every: rejoinEvery, run: rejoin.Pass, store: s, post: post, log: a.Log}).Check,
			(&background{name: "prompt-watch", every: TickEvery, run: func(ctx context.Context) error {
				entry, err := prompts.Find(env.Config.Drive)
				if err != nil {
					return err
				}
				if err := promptWatch.Check(ctx, entry); !errors.Is(err, prompts.ErrHeld) {
					return err
				}
				return nil
			}, store: s, post: post, log: a.Log}).Check,
			(&background{name: "recurrence-count", every: recurEvery, run: func(ctx context.Context) error { return counter.Recur(ctx, env.Config.Drive) }, store: s, post: post, log: a.Log}).Check,
			(&background{name: "judge-watch", every: judgeEvery, run: func(ctx context.Context) error {
				_, err := a.Runner.Run(ctx, shell.Command{Argv: []string{filepath.Join(env.Claude, "long-running", "current", "bin", "judge_watch.py"), "--config", filepath.Join(env.State, runnerFile)}, Timeout: judgeLimit})
				return err
			}, store: s, post: post, log: a.Log, forgiven: 1}).Check,
		)
	}
	if slices.Contains(env.Config.Movers, moverLanes) {
		entry, err := drive.Named(env.Config.Drive)
		if err != nil {
			a.Log.Warn("the choices check is off", "err", err)
			laneDeps.Promises = func() ([]lanes.Promise, error) { return nil, err }
		} else {
			fixLanes, err := orca.FixLanes(a)
			if err != nil {
				return Deps{}, err
			}
			liveClocks, err := orca.LiveClocks(a)
			if err != nil {
				return Deps{}, err
			}
			inputs := &landing.InputsPublisher{Runner: a.Runner, Store: s, FixLane: fixLanes, LiveClocks: liveClocks, Session: entry.Root(), Config: a.ConfigPath, Now: a.Now}
			if squasher, err := newSquash(a, env, s, fixLanes, liveClocks); err != nil {
				a.Log.Warn("the incident squash is off", "err", err)
			} else {
				squashFixes = squasher.Pass
			}
			sources := update.Wire(a, entry, s)
			steps := &update.StepWatch{Store: s, Lanes: sources.Lanes, Plans: sources.Plans, Post: env.Bus.Post, Drive: env.Config.Drive, Now: a.Now}
			cards := board.CardsOf(a, entry, entry.Root(), s)
			laneDeps.Promises = func() ([]lanes.Promise, error) { return cards.Promises(ctx) }
			dropStale = board.StaleTaps{Runner: a.Runner, Session: entry.Root(), Say: func(ctx context.Context, tap board.DroppedTap) error {
				return sayDropped(ctx, env.Config.Drive, env.Config.Repo, tap)
			}, Retire: func(ctx context.Context, card, why string) error {
				return cards.Retire(ctx, lanes.Self, why, []string{card}, io.Discard)
			}, Pull: func(ctx context.Context, target, why string) error {
				_, err := cards.Pull(ctx, lanes.Self, target, why, io.Discard)
				return err
			}}.Pass
			checks = append(checks,
				(&background{name: "choices", every: choiceEvery, run: choices.New(ctx, a, entry, env.Claude, s).Pass, store: s, post: post, log: a.Log}).Check,
				(&background{name: "step-owners", every: stepOwnersEvery, run: steps.Pass, store: s, post: post, log: a.Log, forgiven: 1}).Check,
				(&background{name: "landing-gate-inputs", every: landing.InputsEvery, run: inputs.Pass, store: s, post: post, log: a.Log}).Check,
				(&background{name: "card-sweep", every: sweepEvery, run: func(ctx context.Context) error {
					return cards.Pass(ctx, lanes.Self, board.PackJudge(a.Runner), sweepLog{a.Log, "card sweep"})
				}, store: s, post: post, log: a.Log, forgiven: 1}).Check,
				(&background{name: "findings", every: findingsEvery, run: findings.New(a, entry).Pass, store: s, post: post, log: a.Log, forgiven: 1}).Check,
				(&background{name: "ready-cards", every: PassEvery, run: board.ReadyCards{Cards: cards, Repo: env.Config.Repo, Lane: lanes.Self, Exempt: func() ([]string, error) { return landing.ExemptGrades(filepath.Join(env.State, runnerFile)) }}.Pass, store: s, post: post, log: a.Log, to: board.Communicator, forgiven: 1}).Check,
			)
		}
		orcaRunner, err := config.LoadRunner(filepath.Join(env.State, runnerFile))
		if err != nil {
			return Deps{}, err
		}
		if orcaRunner.Orca.DeskInbox != "" {
			agents, err := launchd.New(a.Runner, os.Stdout)
			if err != nil {
				return Deps{}, err
			}
			if err := agents.Load(ctx, agentOf(env).RelayJob(orcaRunner.Orca.DeskInbox)); err != nil {
				a.Log.Warn("the relay's launchd job is not installed", "err", err)
			}
			if err := installLink(ctx, agents, env); err != nil {
				a.Log.Warn("the board link's launchd job is not installed, so no lane's card reaches his board", "err", err)
			}
			if _, hosted := orcaRunner.Sections["host"]; hosted {
				if err := agents.Load(ctx, agentOf(env).DeskCredsJob(orcaRunner.Source)); err != nil {
					a.Log.Warn("the desk-creds launchd job is not installed, so a restarted desk host waits for a push by hand", "err", err)
				}
			}
			checks = append(checks, NewRelayKeeper(env.State, orcaRunner.Orca.DeskInbox, s, post, a.Log).Check)
		}
	}
	passSource := prfacts.New(a, env.Config, s)
	passSource.ReadsRows = slices.Contains(env.Config.Movers, moverLanes)
	deps := Deps{
		Movers: env.Config.Movers,
		Clock:  env.Now,
		Sleep:  time.Sleep,
		Lanes: func(now time.Time, generation int64) (lanes.Outcome, error) {
			return lanes.Tick(s, laneDeps, now, generation)
		},
		Checks:     checks,
		Stagger:    checkStagger,
		Snapshot:   passSource.Snapshot,
		LateGrades: passSource.ReadLateGrades,
		WriteRows:  prfacts.New(a, env.Config, s).WriteRows,
		Post:       post,
		Wake:       orca.Poster{Bus: orca.CCI{}, Runner: a.Runner, Env: os.Getenv, Now: a.Now, Sleep: time.Sleep, Out: io.Discard, Drive: env.Config.Drive, Author: "supervisor"}.Relay,
		Squash:     squashFixes,
		Log:        a.Log,
	}
	if err := holdsStore(env.Config.Drive, env.Config.Movers); err != nil {
		a.Log.Warn("the store desk stays off", "drive", env.Config.Drive, "err", err)
	} else {
		deps.Followers = append(deps.Followers, askdesk.Desk{
			Lane: askdesk.StoreDesk, Drive: env.Config.Drive, Duty: askdesk.StoreDuty, Bodies: filepath.Join(env.State, askdesk.StoreDesk), Now: a.Now, Log: a.Stderr,
			Read: askdesk.StoreRuns(filepath.Join(env.State, configFile), storeVerbRun(a.Runner, env.Config.Drive)),
		}.Follow)
	}
	deps.Checks = append(deps.Checks, (&background{name: "review-rows", every: TickEvery, run: prfacts.New(a, env.Config, s).FoldRows, store: s, post: post, log: a.Log}).Check)
	deps.Checks = append(deps.Checks, (&background{name: "tap-orders", every: TickEvery, run: prfacts.New(a, env.Config, s).WriteOrders, store: s, post: post, log: a.Log}).Check)
	if slices.Contains(env.Config.Movers, config.MoverGrading) {
		pool, err := grading.NewLivePool(a, filepath.Join(env.State, runnerFile), s)
		if err != nil {
			return Deps{}, err
		}
		deps.Pool = pool
		deps.Checks = append(deps.Checks, (&background{name: "grade-delivery", every: TickEvery, run: func(ctx context.Context) error {
			lease, err := s.Lease(env.Config.Drive)
			if err != nil {
				return err
			}
			return grading.Settle(ctx, s, pool, env.Now(), lease.Generation)
		}, store: s, post: post, log: a.Log, forgiven: 1}).Check)
	}
	if slices.Contains(env.Config.Movers, moverLanes) {
		notes, err := lanes.NewNotesPublish(s, laneDeps)
		if err != nil {
			return Deps{}, err
		}
		deps.Checks = append(deps.Checks, notes.Check)
		claude, err := drive.ConfigDir()
		if err != nil {
			return Deps{}, err
		}
		followPins := func(ctx context.Context) error {
			return orca.FollowPins(ctx, a.Runner, s, claude, filepath.Join(env.State, runnerFile), a.Now, a.Stderr)
		}
		deps.Checks = append(deps.Checks, (&background{name: "follow-pins", every: PassEvery, run: followPins, store: s, post: post, log: a.Log}).Check)
		if entry, err := drive.Named(env.Config.Drive); err != nil {
			a.Log.Warn("the talk follower has no drive entry and stays off", "drive", env.Config.Drive, "err", err)
		} else {
			deps.Followers = append(deps.Followers, (&talk.Follower{Runner: a.Runner, Entry: entry, Writers: env.Config.TalkWriterNames(), Now: a.Now, Log: a.Log}).Run)
			deps.Followers = append(deps.Followers, (&owner.CardThreads{Runner: a.Runner, Entry: entry, Log: a.Log}).Run)
		}
	}
	if !slices.Contains(env.Config.Movers, config.MoverLanding) {
		if slices.Contains(env.Config.Movers, moverLanes) {
			deps.Checks = append(deps.Checks, (&background{name: "landing-silent", every: TickEvery, run: landing.Silence{Store: s, Drive: env.Config.Drive, Now: a.Now}.Check, store: s, post: post, log: a.Log}).Check)
		}
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
		deps.Checks = append(deps.Checks, (&background{name: "pr-ledger", every: ledgerEvery, run: (&prLedger{env: env, store: s, id: runner.Ledger, tokenCommand: runner.CITokenCommand}).pass, store: s, post: post, log: a.Log, forgiven: 1}).Check)
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
			State:    env.State,
			Config:   landing.TrunkConfig{Check: runner.TrunkCheck, CIBuild: runner.CIBuild, TokenCommand: runner.CITokenCommand, FlakeLane: runner.FlakeLane, Channel: runner.FlakyChannel},
			Now:      a.Now,
		}
		if runner.Ledger != "" {
			trunk.Asks = &asks.Mirror{Source: ledger.CLI{Runner: a.Runner, Repo: env.Config.Checkout}, Store: s, Ledger: runner.Ledger, Now: a.Now}
		}
		deps.Checks = append(deps.Checks, (&background{name: "trunk-watch", every: PassEvery, run: trunk.Pass, store: s, post: post, log: a.Log, forgiven: 1}).Check)
	}
	queue := landing.Trunk{Runner: a.Runner, Checkout: env.Config.Checkout, Branch: env.Config.Trunk, Script: env.Config.QueueScript, KeyCommand: env.Config.QueueKey}
	if runner.CIBuild != "" {
		queue.Logs = landing.TrunkWatch{Runner: a.Runner, Checkout: env.Config.Checkout, Config: landing.TrunkConfig{CIBuild: runner.CIBuild, TokenCommand: runner.CITokenCommand}}.FailedJob
	}
	deps.Queue = landing.HoldGuard{Queue: queue, Held: prfacts.New(a, env.Config, s).HeldPRs}
	deps.Restacker = restacker
	deps.Followers = append(deps.Followers, (&landing.HoldWatch{Store: s, Drive: env.Config.Drive, Repo: env.Config.Repo, Trunk: env.Config.Trunk, Read: prfacts.New(a, env.Config, s).HeldAndQueued, Queue: deps.Queue, Now: a.Now, Log: a.Log}).Run)
	deps.Unstick = landing.Unstick{Runner: a.Runner, Read: prfacts.New(a, env.Config, s).ReadMergeable, Move: restacker.Run, Store: s, Drive: env.Config.Drive, Trunk: env.Config.Trunk, Now: a.Now, Log: a.Log}.Pass
	gates, err := landing.NewGates(a, s, env.Config, filepath.Join(env.State, runnerFile))
	if err != nil {
		return Deps{}, err
	}
	mirror := &landing.InputsMirror{Drive: env.Config.Drive, Store: s, CopyLanes: !slices.Contains(env.Config.Movers, moverLanes), Now: a.Now}
	if err := mirror.Pass(ctx); err != nil {
		a.Log.Warn("landing starts before the lanes machine's inputs are read", "err", err)
	}
	deps.Checks = append(deps.Checks, (&background{name: "landing-inputs", every: landing.InputsEvery, run: mirror.Pass, store: s, post: post, log: a.Log}).Check)
	squash, err := landing.NewSquash(a, s, env.Config, filepath.Join(env.State, runnerFile), restacker, mirror.FixLane, mirror.LiveClocks)
	if err != nil {
		return Deps{}, err
	}
	owed, err := landing.NewReleaseOwed(a, s, env.Config, filepath.Join(env.State, runnerFile), squash, mirror.LiveClocks)
	if err != nil {
		return Deps{}, err
	}
	if owed.Owner != "" {
		deps.Checks = append(deps.Checks, (&background{name: "release-owed", every: time.Minute, run: owed.Pass, store: s, post: post, log: a.Log}).Check)
	}
	queueFell := landing.QueueFell{Store: s, Drive: env.Config.Drive, Repo: env.Config.Repo}
	deps.Checks = append(deps.Checks, (&background{name: "trunk-queue", every: PassEvery, run: queueFell.Pass, store: s, post: post, log: a.Log}).Check)
	clobbers := &landing.Clobbers{Runner: a.Runner, Store: s, Drive: env.Config.Drive, Repo: env.Config.Repo, Trunk: env.Config.Trunk, Checkout: env.Config.Checkout, Now: a.Now, Log: a.Log}
	deps.Checks = append(deps.Checks, (&background{name: "trunk-clobbers", every: PassEvery, run: clobbers.Pass, store: s, post: post, log: a.Log}).Check)
	outlived := landing.Outlived{Store: s, Drive: env.Config.Drive, Repo: env.Config.Repo}
	deps.Checks = append(deps.Checks, (&background{name: "outlived-blockers", every: PassEvery, run: outlived.Pass, store: s, post: post, log: a.Log}).Check)
	quota := landing.Quota{Runner: a.Runner, Drive: env.Config.Drive, Repo: env.Config.Repo}
	deps.Checks = append(deps.Checks, (&background{name: "github-quota", every: PassEvery, run: quota.Pass, store: s, post: post, log: a.Log}).Check)
	deps.Admit = func(ctx context.Context, acts []pass.Act, snapshot []machine.Facts) []pass.Act {
		return gates.Admit(ctx, squash.Admit(ctx, acts, snapshot), snapshot)
	}
	deps.Trunk = env.Config.Trunk
	deps.DropStale = dropStale
	deps.SweepGos = func(ctx context.Context, snapshot []machine.Facts) ([]int, error) {
		return landing.SweepGos(ctx, a, env.Config, s, snapshot, a.Now(), sweepLog{a.Log, "go sweep"})
	}
	return deps, nil
}

func newSquash(a *app.App, env lanes.Env, s *store.Store, fixLane func(string) (bool, error), liveClocks func() ([]string, error)) (*landing.Squash, error) {
	runner := filepath.Join(env.State, runnerFile)
	restacker, err := landing.NewRemote(a, runner, lanes.Self, "")
	if err != nil {
		return nil, err
	}
	return landing.NewSquash(a, s, env.Config, runner, restacker, fixLane, liveClocks)
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
	runner, err := config.LoadRunner(filepath.Join(state, runnerFile))
	if err != nil {
		return err
	}
	if runner.Orca.DeskInbox != "" {
		if err := agents.Install(cmd.Context(), agent.RelayJob(runner.Orca.DeskInbox)); err != nil {
			return err
		}
		if err := installLink(cmd.Context(), agents, env); err != nil {
			return err
		}
		if _, hosted := runner.Sections["host"]; hosted {
			if err := agents.Install(cmd.Context(), agent.DeskCredsJob(runner.Source)); err != nil {
				return err
			}
		}
	}
	return agents.Install(cmd.Context(), agent.Job())
}

func installLink(ctx context.Context, agents launchd.Launchd, env lanes.Env) error {
	if err := board.SeedCursor(ctx, env.Config.Drive, env.State); err != nil {
		return err
	}
	if err := agents.Install(ctx, board.LinkJob(env.Config.Drive, agentOf(env).Binary, env.State)); err != nil {
		return err
	}
	return agents.Restart(ctx, board.LinkLabel(env.Config.Drive))
}

func agentOf(env lanes.Env) Agent {
	return Agent{
		Drive:  env.Config.Drive,
		Config: filepath.Join(env.State, configFile),
		Binary: filepath.Join(env.Claude, "long-running", "current", "bin", "lr"),
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
	if err := agents.Uninstall(cmd.Context(), board.LinkLabel(cfg.Drive)); err != nil {
		return err
	}
	return agents.Uninstall(cmd.Context(), Label(cfg.Drive))
}

func sayDropped(ctx context.Context, name, repo string, tap board.DroppedTap) error {
	to := []string{pass.BoardDesk}
	if tap.Lane != "" {
		to = append(to, tap.Lane)
	}
	_, err := client.Post(ctx, client.Draft{
		Drive: name,
		Lane:  lanes.Self,
		Kind:  "note",
		To:    to,
		ID:    fmt.Sprintf("tap-dropped:%s:%d:%s", tap.Block, tap.PR, tap.Head),
		PRs:   []string{fmt.Sprintf("%s#%d", repo, tap.PR)},
		Text:  fmt.Sprintf("Took #%d off the owner's board: %s. It returns only on a new head with its gates green; keep it off any hand-written list until then.", tap.PR, tap.Why),
	})
	return err
}

func holdsStore(name string, movers []string) error {
	if !slices.Contains(movers, moverLanes) {
		return fmt.Errorf("movers %v leave out %s, so this supervisor's store is not the drive's", movers, moverLanes)
	}
	entry, err := drive.Named(name)
	if err != nil {
		return err
	}
	if entry.OnSprite() {
		return fmt.Errorf("drive %s's entry here is a Sprite's, which holds no copy of the drive's store", name)
	}
	return nil
}
