package orca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	passEvery       = 10 * time.Second
	unreachableWait = 2 * time.Minute
	wakeEvery       = 250 * time.Millisecond
	sweepEvery      = 5 * time.Minute
	aliveEvery      = time.Minute
	unreachableFact = "orca-unreachable"
	relayLock       = "relay.lock"
	unboundExit     = 3
)

func (d *Desk) prompt(ctx context.Context) error {
	if err := d.reap(ctx); err != nil {
		return err
	}
	if err := d.readLines(ctx); err != nil {
		return err
	}
	if err := d.deliver(ctx); err != nil {
		return err
	}
	return d.flush(ctx)
}

func (d *Desk) onePass(ctx context.Context) error {
	d.followCoordinator(ctx)
	ready, err := d.check(ctx)
	if err != nil {
		return err
	}
	if ready {
		if err := d.prompt(ctx); err != nil {
			return err
		}
		if d.Now().Sub(d.swept) >= sweepEvery {
			if err := d.sweep(ctx); err != nil {
				return err
			}
			d.swept = d.Now()
			if err := d.prompt(ctx); err != nil {
				return err
			}
		}
	}
	if err := d.deadlines(ctx); err != nil {
		return err
	}
	if err := d.clockPass(ctx); err != nil {
		return err
	}
	if !ready {
		return nil
	}
	return d.prompt(ctx)
}

func (d *Desk) orcaDown(down *orcaUnreachable) error {
	d.unreachable++
	var streak struct {
		Since string `json:"since"`
		Error string `json:"error"`
	}
	if err := d.store.Edit(deskRunner, func(tx *incident.Tx) error {
		value, found, err := tx.Fact(unreachableFact)
		if err != nil {
			return err
		}
		if found {
			raw, err := json.Marshal(value)
			if err != nil {
				return err
			}
			return json.Unmarshal(raw, &streak)
		}
		streak.Since, streak.Error = d.stamp(), down.Error()
		return tx.SetFact(unreachableFact, map[string]any{"since": streak.Since, "error": streak.Error})
	}); err != nil {
		return err
	}
	since, err := store.Parse(streak.Since)
	if err != nil {
		return err
	}
	return d.escalate("orca:"+streak.Since, "ORCA-UNREACHABLE", "runner", fmt.Sprintf("%s; since %s the runner reads no dispatch as gone and fails no relay, and retries each pass with backoff up to %ds; ORCA-BACK follows when Orca answers", streak.Error, pacificClock(since), int(unreachableWait.Seconds())))
}

func (d *Desk) orcaBack() error {
	d.unreachable = 0
	value, found, err := d.factOf(deskRunner, unreachableFact)
	if err != nil || !found {
		return err
	}
	streak, _ := value.(map[string]any)
	stamp, _ := streak["since"].(string)
	since, err := store.Parse(stamp)
	if err != nil {
		return err
	}
	if err := d.store.Edit(deskRunner, func(tx *incident.Tx) error { return tx.DropFact(unreachableFact) }); err != nil {
		return err
	}
	return d.escalate("orca-back:"+stamp, "ORCA-BACK", "runner", fmt.Sprintf("Orca answers again; it gave no answer from %s for %d min (%v)", pacificClock(since), int(d.Now().Sub(since).Minutes()), streak["error"]))
}

func (d *Desk) passWait() time.Duration {
	pause := passEvery
	for range d.unreachable {
		pause *= 2
		if pause >= unreachableWait {
			return unreachableWait
		}
	}
	return pause
}

func (d *Desk) running() int {
	count := 0
	for _, process := range d.launching {
		if !process.Exited() {
			count++
		}
	}
	return count
}

func (d *Desk) idle(ctx context.Context, pause time.Duration) {
	running := d.running()
	for range int(pause / wakeEvery) {
		if ctx.Err() != nil || d.running() < running {
			return
		}
		d.Sleep(wakeEvery)
	}
}

func (d *Desk) postAlive(ctx context.Context) {
	if d.Now().Sub(d.alive) < aliveEvery {
		return
	}
	d.alive = d.Now()
	text := fmt.Sprintf("%s %s pass %d", runnerAlive, d.Now().In(pacificTime).Format("3:04 PM"), d.passes)
	attrs := map[string]string{"pid": fmt.Sprint(os.Getpid()), "config": d.Config.Source, "terminal": d.Env("ORCA_TERMINAL_HANDLE")}
	if _, err := d.Bus.Post(ctx, client.Draft{Drive: d.Config.Drive, Lane: deskRunner, Kind: kindState, Topic: runnerTopic, Text: text, To: []string{watchdogLane + "@" + d.Config.Drive}, Attrs: attrs}); err != nil {
		fmt.Fprintf(d.Stderr, "relay alive left for the next pass: %v\n", err)
	}
}

func (d *Desk) raised(ctx context.Context, err error) {
	fmt.Fprintf(d.Stderr, "relay pass raised: %v\n", err)
	if _, postErr := d.post(ctx, deskPost{Kind: kindBlocker, Text: fmt.Sprintf("orca relay pass raised %.240s; the relay goes on with its next pass", err.Error()), To: coordinatorLane, Topic: "relay", Key: "pass-raised:relay"}); postErr != nil {
		fmt.Fprintf(d.Stderr, "relay pass report left unposted: %v\n", postErr)
	}
}

func (d *Desk) settlePass(ctx context.Context, err error) error {
	var down *orcaUnreachable
	switch {
	case errors.As(err, &down):
		return d.orcaDown(down)
	case err != nil:
		d.raised(ctx, err)
		return nil
	}
	return d.orcaBack()
}

func (d *Desk) timedPass() {
	d.passTook = append(d.passTook, d.Now().Sub(d.passSince))
	if len(d.passTook) > passesTimed {
		d.passTook = d.passTook[len(d.passTook)-passesTimed:]
	}
}

func (d *Desk) holdRelayLock() (*os.File, bool, error) {
	path := filepath.Join(filepath.Dir(d.Config.Source), relayLock)
	lock, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, false, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = lock.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			fmt.Fprintf(d.Stderr, "another lr relay run holds %s\n", path)
			return nil, false, nil
		}
		return nil, false, err
	}
	return lock, true, nil
}

func (d *Desk) Run(ctx context.Context, once bool) error {
	lock, held, err := d.holdRelayLock()
	if err != nil || !held {
		return err
	}
	defer func() { _ = lock.Close() }()
	parent := d.Parent()
	d.open()
	if err := d.ensure(deskRunner, deskRunner); err != nil {
		return err
	}
	state, err := d.readBinding(ctx)
	if err != nil {
		return err
	}
	if !state.Bound {
		key := fmt.Sprintf("unbound:%s:%v", state.Terminal, state.Generation)
		if err := d.escalate(key, "UNBOUND", "runner", d.unboundText(state)+"; the runner refused to start"); err != nil {
			return err
		}
		if err := d.flush(ctx); err != nil {
			return err
		}
		fmt.Fprintln(d.Stderr, d.unboundText(state))
		return &wait.ExitError{Code: unboundExit}
	}
	if _, err := d.post(ctx, deskPost{Kind: kindNote, Text: fmt.Sprintf("%s reads its orders through lr relay pid %d", deskLane, os.Getpid()), To: watchdogLane + "@" + d.Config.Drive, Topic: "alive:" + deskLane, Key: "register:" + deskLane, Lane: deskLane}); err != nil {
		fmt.Fprintf(d.Stderr, "the desk's register note left unposted: %v\n", err)
	}
	for {
		d.passSince = d.Now()
		passErr := d.onePass(ctx)
		if ctx.Err() != nil {
			d.background.Wait()
			return nil
		}
		for _, step := range []func() error{
			func() error { return d.settlePass(ctx, passErr) },
			func() error { return d.stalledLaunches(ctx) },
			func() error { return d.flush(ctx) },
		} {
			if err := step(); err != nil {
				d.raised(ctx, err)
			}
		}
		d.passes++
		d.postAlive(ctx)
		d.timedPass()
		if once {
			d.background.Wait()
			return nil
		}
		d.idle(ctx, d.passWait())
		if d.Parent() != parent {
			d.background.Wait()
			return nil
		}
	}
}

func (d *Desk) Rebind(ctx context.Context) error {
	d.open()
	if d.Env("ORCA_TERMINAL_HANDLE") == "" {
		fmt.Fprintf(d.Stderr, "lr relay rebind: no ORCA_TERMINAL_HANDLE; run `%s` from the coordinator's Orca terminal\n", d.rebindCommand())
		return &wait.ExitError{Code: unboundExit}
	}
	d.sender = nil
	if err := d.ensure(deskRunner, deskRunner); err != nil {
		return err
	}
	used := d.call(ctx, orchestration, "run-use", "--id", d.Config.Run)
	if !used.OK {
		fmt.Fprintf(d.Stderr, "lr relay rebind: orca orchestration run-use failed: %s\n", used.Refusal())
		return &wait.ExitError{Code: 1}
	}
	state, err := d.readBinding(ctx)
	if err != nil {
		return err
	}
	if !state.Bound {
		fmt.Fprintln(d.Stderr, d.unboundText(state))
		return &wait.ExitError{Code: 1}
	}
	return nil
}

type deskCCI struct{ CCI }

func (deskCCI) Among(ctx context.Context, drive string, seqs []int64) ([]client.Record, error) {
	return client.Among(ctx, drive, seqs)
}

func newDesk(a *app.App) (*Desk, error) {
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return nil, err
	}
	config, err := LoadDeskConfig(filepath.Join(state, "runner.json"), os.Getenv)
	if err != nil {
		return nil, err
	}
	env, err := lanes.NewEnv(a.Runner, cfg, state, a.Now)
	if err != nil {
		return nil, err
	}
	executors, err := incident.DefaultRoot()
	if err != nil {
		return nil, err
	}
	return &Desk{
		Config: config, Bus: deskCCI{}, Runner: a.Runner, Spawn: SpawnDetached, Env: os.Getenv, Now: a.Now, Sleep: time.Sleep, Parent: os.Getppid, Plugin: os.Getenv("BINRUN_PLUGIN_ROOT"), Stderr: os.Stderr,
		Incidents: incident.Store{Root: executors},
		FireIncident: func(ctx context.Context, fired lanes.Fired) error {
			_, err := env.IncidentLaunch(ctx, fired)
			return err
		},
	}, nil
}

func FixLanes(a *app.App) (func(lane string) (bool, error), error) {
	desk, err := newDesk(a)
	if err != nil {
		return nil, err
	}
	desk.open()
	return func(lane string) (bool, error) {
		launch, err := desk.fixLaunch(lanePrefix + lane)
		return launch != nil, err
	}, nil
}

func LiveClocks(a *app.App) (func() ([]string, error), error) {
	desk, err := newDesk(a)
	if err != nil {
		return nil, err
	}
	desk.open()
	return desk.liveClocks, nil
}

func relayRunCommand(a *app.App) *cobra.Command {
	var once bool
	cmd := &cobra.Command{
		Use:   "run",
		Short: "Read orca-desk lines, deliver relays, start launches and settle them, one pass after another",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			desk, err := newDesk(a)
			if err != nil {
				return err
			}
			ctx, stop := signal.NotifyContext(cmd.Context(), syscall.SIGTERM, syscall.SIGINT)
			defer stop()
			return desk.Run(ctx, once)
		},
	}
	cmd.Flags().BoolVar(&once, "once", false, "run one pass and stop")
	return cmd
}

func relayRebindCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "rebind",
		Short: "Bind the Run to this Orca terminal, so launches start from it",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			desk, err := newDesk(a)
			if err != nil {
				return err
			}
			return desk.Rebind(cmd.Context())
		},
	}
}
