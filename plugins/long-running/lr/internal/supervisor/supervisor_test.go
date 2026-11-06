package supervisor

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const head = "e718a7434fa01ee9835f992a9a647a825350732c"

var t0 = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)

type clock struct{ now time.Time }

func (c *clock) read() time.Time        { return c.now }
func (c *clock) sleep(by time.Duration) { c.now = c.now.Add(by) }

type queue struct {
	enqueued []int
	err      error
}

func (q *queue) Enqueue(_ context.Context, number int, _ string) (string, error) {
	if q.err != nil {
		return "", q.err
	}
	q.enqueued = append(q.enqueued, number)
	return "queued", nil
}

func (q *queue) Reenter(ctx context.Context, number int) (string, error) {
	return q.Enqueue(ctx, number, "")
}

func (q *queue) Dequeue(context.Context, int) (string, error) { return "", nil }

func (q *queue) LastFailure(context.Context, int, time.Time) (landing.Failure, error) {
	return landing.Failure{}, nil
}

func (q *queue) States(context.Context, []int) (map[int]facts.QueueEntry, error) {
	return nil, nil
}

type restacker struct{}

func (restacker) Restack(context.Context, int, string, string) (string, error) { return "", nil }

type pool struct{ jobs []string }

func (p *pool) TellStopped(context.Context, machine.Facts, string) error {
	return nil
}

func (p *pool) Dispatch(_ context.Context, grader string, facts machine.Facts) (string, error) {
	job := fmt.Sprintf("job/%s/%d", grader, facts.Number)
	p.jobs = append(p.jobs, job)
	return job, nil
}

func (*pool) Plan(_ context.Context, items []store.Item, _ map[int]machine.Facts, _ int, _ time.Time) ([]store.Item, error) {
	return items, nil
}

func (*pool) Firsts(map[int]machine.Facts) []machine.Facts                   { return nil }
func (*pool) Whole(context.Context, map[int]machine.Facts) ([]string, error) { return nil, nil }
func (*pool) Reopen(context.Context, map[int]machine.Facts) ([]string, map[int]string, error) {
	return nil, nil, nil
}

func (*pool) Settle(_ context.Context, workers []string) (map[string]bool, error) {
	live := make(map[string]bool, len(workers))
	for _, worker := range workers {
		live[worker] = true
	}
	return live, nil
}

func memory(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func prFacts(number int, change func(*machine.Facts)) machine.Facts {
	f := machine.Facts{
		Repo: "org/repo", Number: number, Head: head, Base: "dev", Lane: "lane-7",
		CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true,
		Finder: machine.FinderClear, Fidelity: machine.FidelityPass, Tap: machine.TapTapped,
	}
	if change != nil {
		change(&f)
	}
	return f
}

func deps(c *clock, q *queue, p *pool, snapshot ...machine.Facts) Deps {
	return Deps{
		Movers: []string{"landing", "grading"},
		Clock:  c.read,
		Sleep:  c.sleep,
		Snapshot: func(context.Context) ([]machine.Facts, error) {
			return snapshot, nil
		},
		Post:      func([]string, bus.Body) error { return nil },
		Queue:     q,
		Trunk:     "dev",
		Restacker: restacker{},
		Pool:      p,
		Log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

func equal(t *testing.T, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v, want %#v", got, want)
	}
}

func TestAGradeThatLandsAfterTheReadEnqueuesItsHeadInThatPass(t *testing.T) {
	s, c, q, p := memory(t), &clock{t0}, &queue{}, &pool{}
	d := deps(c, q, p, prFacts(2, func(f *machine.Facts) { f.Fidelity = "" }))
	d.Movers = []string{"landing"}
	d.LateGrades = func(_ context.Context, snapshot []machine.Facts) ([]machine.Facts, error) {
		graded := slices.Clone(snapshot)
		graded[0].Fidelity = machine.FidelityPass
		return graded, nil
	}

	runLoop(t, s, "0.8.0", 1, d, 1)

	equal(t, q.enqueued, []int{2})
}

func TestAPassItsSupervisorStopsRaisesNoEscalationAndALivePassStillDoes(t *testing.T) {
	failed := fmt.Errorf("read schema version: %w", context.Canceled)
	for _, tt := range []struct {
		name, subject string
		fail          func(*Deps)
	}{
		{"unread", unreadSubject, func(d *Deps) {
			d.Snapshot = func(context.Context) ([]machine.Facts, error) { return nil, failed }
		}},
		{"unwritten", unwrittenSubject, func(d *Deps) {
			d.WriteRows = func(context.Context, []machine.Facts) error { return failed }
		}},
	} {
		for _, stopped := range []bool{true, false} {
			t.Run(fmt.Sprintf("%s stopped=%v", tt.name, stopped), func(t *testing.T) {
				s := memory(t)
				var told []string
				d := deps(&clock{t0}, &queue{}, &pool{})
				d.Movers = nil
				d.Post = func(to []string, _ bus.Body) error {
					told = append(told, to...)
					return nil
				}
				tt.fail(&d)
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				if stopped {
					cancel()
				}
				if _, err := runPass(ctx, s, "d", 1, d, t0, Report{At: t0}); err != nil {
					t.Fatal(err)
				}
				raised, err := s.Escalations(tt.subject)
				if err != nil {
					t.Fatal(err)
				}
				if got := len(raised) > 0 || len(told) > 0; got == stopped {
					t.Fatalf("escalated %v and told %v, want an escalation only when the supervisor is not stopping", raised, told)
				}
			})
		}
	}
}

func runLoop(t *testing.T, s *store.Store, version string, pid int, d Deps, ticks int) ([]Report, Ended) {
	t.Helper()
	reports, ended, err := Run(context.Background(), s, "d", version, pid, d, ticks)
	if err != nil {
		t.Fatal(err)
	}
	return reports, ended
}

func TestATickIsOnePassThatLandsAndGrades(t *testing.T) {
	s, c, q, p := memory(t), &clock{t0}, &queue{}, &pool{}
	ungraded := prFacts(2, func(f *machine.Facts) { f.Finder, f.Fidelity = "", "" })

	reports, _ := runLoop(t, s, "0.8.0", 1, deps(c, q, p, prFacts(1, nil), ungraded), 1)

	r := reports[0]
	equal(t, []int{r.PRs, r.Enqueued, r.Dispatched, r.Escalated}, []int{2, 1, 2, 0})
	equal(t, q.enqueued, []int{1})
	lease, err := s.Lease("d")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, lease.Generation, int64(1))
	workers, err := s.Workers()
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(workers))
	for _, worker := range workers {
		names = append(names, worker.Name)
	}
	equal(t, names, []string{"job/fidelity/2", "job/finder/2", lanes.Self})
}

func TestTicksAreThirtySecondsApartAndRenewTheLease(t *testing.T) {
	s, c := memory(t), &clock{t0}

	reports, ended := runLoop(t, s, "0.8.0", 1, deps(c, &queue{}, &pool{}), 3)

	equal(t, ended, EndedTicks)
	equal(t, []time.Time{reports[0].At, reports[1].At, reports[2].At}, []time.Time{t0, t0.Add(30 * time.Second), t0.Add(time.Minute)})
	lease, err := s.Lease("d")
	if err != nil {
		t.Fatal(err)
	}
	if !lease.Renewed.Equal(t0.Add(time.Minute)) {
		t.Fatalf("renewed %v", lease.Renewed)
	}
}

func TestASecondSupervisorTakesTheLeaseAndTheFirstExitsOnItsNextTick(t *testing.T) {
	s, c := memory(t), &clock{t0}
	first, err := s.AcquireLease("d", "0.8.0", 101, t0)
	if err != nil {
		t.Fatal(err)
	}
	d := deps(c, &queue{}, &pool{}, prFacts(1, nil))
	if _, err := Tick(context.Background(), s, "d", first.Generation, d); err != nil {
		t.Fatal(err)
	}

	second, _ := runLoop(t, s, "0.8.1", 202, d, 1)

	equal(t, len(second), 1)
	lease, err := s.Lease("d")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, lease.PID, 202)
	if _, err := Tick(context.Background(), s, "d", first.Generation, d); !errors.Is(err, store.ErrStaleGeneration) {
		t.Fatalf("err %v", err)
	}
}

func TestRunEndsBeforeItEnqueuesOnceItsGenerationIsTakenMidPass(t *testing.T) {
	s, c, q := memory(t), &clock{t0}, &queue{}
	d := deps(c, q, &pool{})
	d.Snapshot = func(context.Context) ([]machine.Facts, error) {
		if _, err := s.AcquireLease("d", "0.8.1", 202, c.read()); err != nil {
			t.Fatal(err)
		}
		return []machine.Facts{prFacts(1, nil)}, nil
	}

	reports, ended := runLoop(t, s, "0.8.0", 101, d, 0)

	equal(t, len(reports), 0)
	equal(t, ended, EndedGeneration)
	equal(t, q.enqueued, []int(nil))
}

func TestRunEndsAtTheTickBoundaryOnceANewerReleaseIsInstalled(t *testing.T) {
	s, c := memory(t), &clock{t0}
	d := deps(c, &queue{}, &pool{}, prFacts(1, nil))
	d.Stale = func(context.Context) (bool, error) { return true, nil }

	reports, ended := runLoop(t, s, "0.8.0", 101, d, 0)

	equal(t, len(reports), 1)
	equal(t, ended, EndedRelease)
	equal(t, c.now, t0)
}

func TestARunTakesANewerReleaseAfterItsSleepWithoutAnotherTick(t *testing.T) {
	s, c := memory(t), &clock{t0}
	d := deps(c, &queue{}, &pool{}, prFacts(1, nil))
	reads := 0
	d.Stale = func(context.Context) (bool, error) {
		reads++
		return reads == 2, nil
	}

	reports, ended := runLoop(t, s, "0.8.0", 101, d, 0)

	equal(t, len(reports), 1)
	equal(t, ended, EndedRelease)
	equal(t, reads, 2)
}

func TestALanesOnlySupervisorRunsNoPRPass(t *testing.T) {
	s, c := memory(t), &clock{t0}
	var ticked []time.Time
	d := Deps{Movers: []string{"lanes"}, Clock: c.read, Sleep: c.sleep, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Lanes: func(now time.Time, _ int64) (lanes.Outcome, error) {
		ticked = append(ticked, now)
		return lanes.Outcome{}, nil
	}}

	reports, _ := runLoop(t, s, "0.8.0", 1, d, 1)

	equal(t, ticked, []time.Time{t0})
	equal(t, reports[0].PRs+reports[0].Enqueued+reports[0].Dispatched, 0)
	items, err := s.OpenItems("", 0)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(items), 0)
}

type signallingPool struct {
	*pool
	dispatched chan struct{}
	once       sync.Once
}

func (p *signallingPool) Dispatch(ctx context.Context, grader string, facts machine.Facts) (string, error) {
	p.once.Do(func() { close(p.dispatched) })
	return p.pool.Dispatch(ctx, grader, facts)
}

func TestALanesHostDispatchesGradesWhileItsLanesTickIsStillRunning(t *testing.T) {
	p := &signallingPool{pool: &pool{}, dispatched: make(chan struct{})}
	ungraded := prFacts(2, func(f *machine.Facts) { f.Finder, f.Fidelity = "", "" })
	d := Deps{
		Movers: []string{"lanes", "grading"}, Clock: func() time.Time { return t0 }, Sleep: func(time.Duration) {}, Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Snapshot: func(context.Context) ([]machine.Facts, error) { return []machine.Facts{ungraded}, nil },
		Post:     func([]string, bus.Body) error { return nil },
		Pool:     p,
		Lanes: func(time.Time, int64) (lanes.Outcome, error) {
			select {
			case <-p.dispatched:
			case <-time.After(10 * time.Second):
				t.Error("no grade was dispatched while the lanes tick ran")
			}
			return lanes.Outcome{}, nil
		},
	}

	runLoop(t, memory(t), "0.8.0", 1, d, 1)
}

func TestAnUnreadLanesSourceAndAFailedCheckSkipOnlyThemselves(t *testing.T) {
	var ran sync.WaitGroup
	ran.Add(3)
	once := func(err error) lanes.Check {
		var first sync.Once
		return func(context.Context, time.Time) error {
			first.Do(ran.Done)
			return err
		}
	}
	d := Deps{
		Movers: []string{"lanes"}, Clock: func() time.Time { return t0 }, Sleep: func(time.Duration) { ran.Wait() }, Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Lanes: func(time.Time, int64) (lanes.Outcome, error) {
			return lanes.Outcome{}, fmt.Errorf("%w: orca refused", lanes.ErrUnread)
		},
		Checks: []lanes.Check{
			once(fmt.Errorf("%w: slack down", lanes.ErrUnread)),
			once(errors.New("cci post: time 1:15 AM in the text names something that has not happened yet")),
			once(nil),
		},
	}

	reports, _ := runLoop(t, memory(t), "0.8.0", 1, d, 2)

	equal(t, len(reports), 2)
}

func TestALaterCheckStartsItsStaggerAfterTheFirst(t *testing.T) {
	const stagger = 20 * time.Millisecond
	ran := make(chan struct{})
	var first sync.Once
	var waited time.Duration
	began := time.Now()
	noop := func(context.Context, time.Time) error { return nil }
	d := Deps{
		Clock: func() time.Time { return t0 }, Sleep: func(time.Duration) { <-ran }, Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Stagger: stagger,
		Checks: []lanes.Check{noop, noop, func(context.Context, time.Time) error {
			first.Do(func() {
				waited = time.Since(began)
				close(ran)
			})
			return nil
		}},
	}

	runLoop(t, memory(t), "0.8.0", 1, d, 1)

	if waited < 2*stagger {
		t.Fatalf("the third check started after %v", waited)
	}
}

func TestAHungCheckHoldsNeitherTheOtherChecksNorTheTickAndEndsWithTheRun(t *testing.T) {
	ran := make(chan struct{})
	started := make(chan struct{})
	var first sync.Once
	ended := make(chan struct{})
	d := Deps{
		Clock: func() time.Time { return t0 }, Sleep: func(time.Duration) { <-ran }, Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Checks: []lanes.Check{
			func(ctx context.Context, _ time.Time) error {
				if _, bounded := ctx.Deadline(); !bounded {
					t.Error("a check runs with no deadline")
				}
				close(started)
				<-ctx.Done()
				close(ended)
				return ctx.Err()
			},
			func(context.Context, time.Time) error {
				<-started
				first.Do(func() { close(ran) })
				return nil
			},
		},
	}

	reports, _ := runLoop(t, memory(t), "0.8.0", 1, d, 1)

	equal(t, len(reports), 1)
	select {
	case <-ended:
	case <-time.After(10 * time.Second):
		t.Fatal("the hung check did not end with the run")
	}
}

func TestAStoreFailureInALanesTickEndsTheProcess(t *testing.T) {
	s, c := memory(t), &clock{t0}
	broken := errors.New("disk full")
	d := Deps{Movers: []string{"lanes"}, Clock: c.read, Sleep: c.sleep, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Lanes: func(time.Time, int64) (lanes.Outcome, error) {
		return lanes.Outcome{}, broken
	}}

	if _, _, err := Run(context.Background(), s, "d", "0.8.0", 1, d, 1); !errors.Is(err, broken) {
		t.Fatalf("err %v", err)
	}
}

func TestThePRPassRunsEveryThreeMinutesWhileTheLanesTickEveryThirtySeconds(t *testing.T) {
	s, c := memory(t), &clock{t0}
	untapped := prFacts(1, func(f *machine.Facts) { f.Tap = "" })

	reports, _ := runLoop(t, s, "0.8.0", 1, deps(c, &queue{}, &pool{}, untapped), 7)

	prs := make([]int, 0, len(reports))
	for _, r := range reports {
		prs = append(prs, r.PRs)
	}
	equal(t, prs, []int{1, 0, 0, 0, 0, 0, 1})
}

func TestALandingPassSweepsGosOverItsSnapshotAndAPassWithoutLandingDoesNot(t *testing.T) {
	for movers, want := range map[string][]int{"landing": {7}, "grading": nil} {
		s, c := memory(t), &clock{t0}
		d := deps(c, &queue{}, &pool{}, prFacts(7, nil))
		d.Movers = []string{movers}
		var swept []int
		d.SweepGos = func(_ context.Context, snapshot []machine.Facts) ([]int, error) {
			for _, f := range snapshot {
				swept = append(swept, f.Number)
			}
			return nil, errors.New("the gate inputs are stale")
		}

		reports, _ := runLoop(t, s, "0.8.0", 1, d, 1)

		equal(t, swept, want)
		equal(t, reports[0].PRs, 1)
	}
}

func TestAHeadTheSweepPostsAGoForEnqueuesInThatSamePass(t *testing.T) {
	s, c, q := memory(t), &clock{t0}, &queue{}
	untapped := prFacts(7, func(f *machine.Facts) { f.Tap = "" })
	d := deps(c, q, &pool{}, untapped, prFacts(8, func(f *machine.Facts) { f.Tap = "" }))
	d.SweepGos = func(context.Context, []machine.Facts) ([]int, error) { return []int{7}, nil }

	runLoop(t, s, "0.8.0", 1, d, 1)

	equal(t, q.enqueued, []int{7})
}

func TestAPassWhoseSourcesDoNotAnswerActsOnNothing(t *testing.T) {
	s, c, q := memory(t), &clock{t0}, &queue{}
	d := deps(c, q, &pool{})
	d.Snapshot = func(context.Context) ([]machine.Facts, error) {
		return nil, fmt.Errorf("%w: ccx vcs pr status exited 1", lanes.ErrUnread)
	}

	reports, _ := runLoop(t, s, "0.8.0", 1, d, 1)

	equal(t, reports[0].PRs+reports[0].Enqueued, 0)
	equal(t, len(q.enqueued), 0)
}

func TestAQueueThatDoesNotAnswerStopsTheMoversAndNeverTheSupervisor(t *testing.T) {
	s, c := memory(t), &clock{t0}

	reports, _ := runLoop(t, s, "0.8.0", 1, deps(c, &queue{err: errors.New("git fetch exited 128")}, &pool{}, prFacts(1, nil)), 2)

	equal(t, []int{reports[0].PRs, reports[1].PRs}, []int{1, 0})
	lease, err := s.Lease("d")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, lease.Generation, int64(1))
}

func TestARefusedPassPostNeverStopsTheSupervisor(t *testing.T) {
	s, c := memory(t), &clock{t0}
	d := deps(c, &queue{}, &pool{}, prFacts(1, func(f *machine.Facts) { f.Finder, f.Fidelity = "", "" }))
	refused := 0
	d.Post = func([]string, bus.Body) error {
		refused++
		return &bus.Refused{Message: "an escalation record never reaches this lane"}
	}

	runLoop(t, s, "0.8.0", 1, d, 40)

	if refused == 0 {
		t.Fatal("no post was refused, so the test proves nothing")
	}
	lease, err := s.Lease("d")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, lease.Generation, int64(1))
}

type scripted func(argv []string) (shell.Result, error)

func (s scripted) Run(_ context.Context, c shell.Command) (shell.Result, error) { return s(c.Argv) }

type recordingBus struct{ posted []client.Draft }

func (b *recordingBus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	b.posted = append(b.posted, d)
	return client.Record{Seq: 1}, nil
}

func (*recordingBus) Tail(context.Context, client.Query) ([]client.Record, error) { return nil, nil }

func (*recordingBus) Open(context.Context, string, ...string) ([]client.Record, error) {
	return nil, nil
}

func (*recordingBus) Who(context.Context, string) ([]client.Peer, error) { return nil, nil }

func link(t *testing.T, claude, version string) {
	t.Helper()
	dir := filepath.Join(claude, "long-running")
	target := filepath.Join(dir, version)
	if err := os.MkdirAll(target, 0o750); err != nil {
		t.Fatal(err)
	}
	current := filepath.Join(dir, "current")
	if err := os.Remove(current); err != nil && !errors.Is(err, os.ErrNotExist) {
		t.Fatal(err)
	}
	if err := os.Symlink(target, current); err != nil {
		t.Fatal(err)
	}
}

func TestTheSupervisorMovesToANewerInstallWithoutReadingOrReplayingHistory(t *testing.T) {
	claude := t.TempDir()
	b := &recordingBus{}
	env := lanes.Env{Bus: b, Claude: claude, State: configs(t), Runner: scripted(func(argv []string) (shell.Result, error) {
		t.Fatalf("upgrade launched a command: %v", argv)
		return shell.Result{}, nil
	})}
	upgrade := &Upgrade{Env: env, Running: "0.7.820", Read: time.Now(), Holding: nothingHolds, Yield: func(bool) {}}
	for _, step := range []struct {
		version string
		stale   bool
	}{{"0.7.819", false}, {"0.7.820", false}, {"0.7.821", true}, {"0.7.821", true}, {"0.7.822", true}} {
		link(t, claude, step.version)
		stale, err := upgrade.Stale(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		equal(t, stale, step.stale)
	}
	equal(t, len(b.posted), 0)
}

func TestABumpThatKeepsTheBinaryMovesTheLeaseWithoutARestart(t *testing.T) {
	claude := t.TempDir()
	pin := func(version, binary string) {
		link(t, claude, version)
		dir := filepath.Join(claude, "long-running", version, "bin")
		if err := os.MkdirAll(dir, 0o750); err != nil {
			t.Fatal(err)
		}
		descriptor := `#!/usr/bin/env binrun
{"schema": 1, "name": "lr", "version": {"static": "` + binary + `"}}
`
		if err := os.WriteFile(filepath.Join(dir, "lr.binrun"), []byte(descriptor), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if _, err := s.AcquireLease("d", "0.7.820", 42, time.Now()); err != nil {
		t.Fatal(err)
	}
	upgrade := &Upgrade{
		Env: lanes.Env{Bus: &recordingBus{}, Claude: claude, State: configs(t)}, Running: "0.7.820", Binary: "0.390.1", Read: time.Now(), Holding: nothingHolds, Yield: func(bool) {},
		Serve: func(version string) error { return s.ServeVersion("d", 42, version) },
	}

	pin("0.7.821", "0.390.1")
	stale, err := upgrade.Stale(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stale, false)
	lease, err := s.Lease("d")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, lease.Version, "0.7.821")
	equal(t, lease.Generation, int64(1))

	pin("0.7.822", "0.391.1")
	stale, err = upgrade.Stale(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stale, true)
}

func nothingHolds() bool {
	return false
}

func TestARestartWaitsForTheCleanupPassItWouldCut(t *testing.T) {
	claude := t.TempDir()
	state := configs(t)
	holding := true
	yielding := false
	polls := 0
	upgrade := &Upgrade{Env: lanes.Env{Bus: &recordingBus{}, Claude: claude, State: state}, Running: "0.7.820", Read: time.Now(), Holding: func() bool { return holding }, Yield: func(yield bool) { yielding = yield }, Sleep: func(time.Duration) {
		polls++
		holding = polls < 3
	}}
	link(t, claude, "0.7.820")

	stale, err := upgrade.Stale(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stale, false)
	equal(t, yielding, false)
	equal(t, polls, 0)

	link(t, claude, "0.7.821")
	stale, err = upgrade.Stale(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stale, true)
	equal(t, yielding, true)
	equal(t, polls, 3)
}

func TestAHeldCleanupPassStartsNoFurtherTickBeforeTheRestart(t *testing.T) {
	claude := t.TempDir()
	link(t, claude, "0.7.821")
	holding := true
	upgrade := &Upgrade{Env: lanes.Env{Bus: &recordingBus{}, Claude: claude, State: configs(t)}, Running: "0.7.820", Read: time.Now(), Holding: func() bool { return holding }, Yield: func(bool) {}, Sleep: func(time.Duration) { holding = false }}
	s, c := memory(t), &clock{t0}
	ticks := 0
	d := Deps{Movers: []string{"lanes"}, Clock: c.read, Sleep: c.sleep, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Stale: upgrade.Stale, Lanes: func(time.Time, int64) (lanes.Outcome, error) {
		ticks++
		return lanes.Outcome{}, nil
	}}

	_, ended := runLoop(t, s, "0.8.0", 1, d, 2)

	equal(t, ended, EndedRelease)
	equal(t, ticks, 1)
}

func TestACleanupPassStartingAsTheProbeYieldsStillHoldsTheRestart(t *testing.T) {
	claude := t.TempDir()
	link(t, claude, "0.7.821")
	running := false
	waited := 0
	upgrade := &Upgrade{Env: lanes.Env{Bus: &recordingBus{}, Claude: claude, State: configs(t)}, Running: "0.7.820", Read: time.Now(), Holding: func() bool { return running }, Yield: func(yield bool) { running = running || yield }, Sleep: func(time.Duration) {
		waited++
		running = false
	}}

	stale, err := upgrade.Stale(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stale, true)
	equal(t, waited, 1)
}

func TestAMovedPinYieldsCleanupPassesBeforeTheTickEnds(t *testing.T) {
	claude := t.TempDir()
	link(t, claude, "0.7.820")
	yielding := false
	upgrade := &Upgrade{Env: lanes.Env{Claude: claude, State: configs(t)}, Running: "0.7.820", Read: time.Now(), Yield: func(yield bool) { yielding = yield }}

	if err := upgrade.YieldOnceOwed(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	equal(t, yielding, false)

	link(t, claude, "0.7.821")
	if err := upgrade.YieldOnceOwed(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	equal(t, yielding, true)
}

func TestAConfigEditYieldsTheNextCleanupPassToo(t *testing.T) {
	claude := t.TempDir()
	state := configs(t)
	link(t, claude, "0.7.820")
	yielding := false
	holding := true
	upgrade := &Upgrade{Env: lanes.Env{Bus: &recordingBus{}, Claude: claude, State: state}, Running: "0.7.820", Read: time.Now().Add(-time.Hour), Holding: func() bool { return holding }, Yield: func(yield bool) { yielding = yield }, Sleep: func(time.Duration) { holding = false }}
	stale, err := upgrade.Stale(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stale, true)
	equal(t, yielding, true)
}

func TestARewrittenConfigWithTheSameSettingsDoesNotWaitOnACleanupPass(t *testing.T) {
	claude := t.TempDir()
	state := configs(t)
	link(t, claude, "0.7.820")
	settings, err := readSettings(state)
	if err != nil {
		t.Fatal(err)
	}
	yielding := true
	upgrade := &Upgrade{Env: lanes.Env{Bus: &recordingBus{}, Claude: claude, State: state}, Running: "0.7.820", Read: time.Now().Add(-time.Hour), Settings: settings, Holding: func() bool { return true }, Yield: func(yield bool) { yielding = yield }, Sleep: func(time.Duration) { t.Fatal("waited on a cleanup pass with nothing owed") }}
	stale, err := upgrade.Stale(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stale, false)
	equal(t, yielding, false)
}

func configs(t *testing.T) string {
	t.Helper()
	state := t.TempDir()
	for _, name := range []string{configFile, runnerFile} {
		if err := os.WriteFile(filepath.Join(state, name), []byte("{}"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return state
}

func rewriteRunner(t *testing.T, state, text string, at time.Time) {
	t.Helper()
	path := filepath.Join(state, runnerFile)
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, at, at); err != nil {
		t.Fatal(err)
	}
}

func TestAMovedPinOrTheSameBytesNeverRestartTheSupervisorAndAChangedSettingDoes(t *testing.T) {
	const pinned = `{"deadlines":{"hold_minutes":%d},"orca":{"sprite":{"env":{"WORKER_CCI_SHA256":%q,"WORKER_CCI_URL":%q,"WORKER_CHECKOUT":"/repo"},"tools":[{"name":"long-running@skills","version":%q}]}}}`
	b := &recordingBus{}
	state := configs(t)
	read := time.Now()
	rewriteRunner(t, state, fmt.Sprintf(pinned, 15, "s1", "u1", "0.7.1"), read.Add(-time.Minute))
	settings, err := readSettings(state)
	if err != nil {
		t.Fatal(err)
	}
	upgrade := &Upgrade{Env: lanes.Env{Bus: b, Claude: t.TempDir(), State: state, Config: config.Config{Drive: "d1"}}, Running: "0.7.820", Read: read, Settings: settings, Holding: nothingHolds, Yield: func(bool) {}}

	for minutes, text := range []string{
		fmt.Sprintf(pinned, 15, "s1", "u1", "0.7.1"),
		fmt.Sprintf(pinned, 15, "s1", "u1", "0.7.2"),
		fmt.Sprintf(pinned, 15, "s2", "u2", "0.7.2"),
	} {
		at := read.Add(time.Duration(minutes+1) * time.Minute)
		rewriteRunner(t, state, text, at)
		stale, err := upgrade.Stale(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		equal(t, stale, false)
		equal(t, upgrade.Read.Unix(), at.Unix())
	}
	equal(t, len(b.posted), 0)

	rewriteRunner(t, state, fmt.Sprintf(pinned, 16, "s2", "u2", "0.7.2"), read.Add(time.Hour))
	stale, err := upgrade.Stale(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stale, true)
	equal(t, len(b.posted), 1)
	equal(t, b.posted[0].To, []string{lanes.Root})
	equal(t, b.posted[0].Text, "CONFIG-CHANGED runner.json changed after the supervisor read it, so the supervisor restarts to read it fresh")
}

func TestTheSupervisorNamesEveryToolAndKeyItStartsWithout(t *testing.T) {
	cfg := config.Config{OrcaRun: "run_1", Movers: []string{config.MoverLanes, config.MoverLanding}, QueueKey: "security find-generic-password -w -s trunk"}
	keys := map[string]string{"gh auth token": "token\n", cfg.QueueKey: ""}
	runner := scripted(func(argv []string) (shell.Result, error) {
		return shell.Result{Stdout: keys[argv[len(argv)-1]]}, nil
	})
	absent := map[string]bool{"orca": true}
	look := func(tool string) (string, error) {
		if absent[tool] {
			return "", exec.ErrNotFound
		}
		return "/usr/bin/" + tool, nil
	}

	equal(t, missing(context.Background(), runner, look, cfg), []string{
		"orca is not on PATH",
		"the queue key is unreadable: `security find-generic-password -w -s trunk` printed nothing",
	})

	cfg.Movers = []string{config.MoverLanding}
	equal(t, missing(context.Background(), runner, look, cfg), []string{
		"the queue key is unreadable: `security find-generic-password -w -s trunk` printed nothing",
	})

	absent, keys[cfg.QueueKey] = map[string]bool{}, "key\n"
	equal(t, len(missing(context.Background(), runner, look, cfg)), 0)
}

func TestBehindComparesVersionsNumerically(t *testing.T) {
	equal(t, Behind("0.7.99", "0.7.100"), true)
	equal(t, Behind("0.7.100", "0.7.99"), false)
	equal(t, Behind("0.7.100", "0.7.100"), false)
	equal(t, Behind("", "0.7.100"), true)
	equal(t, Behind("0.7.100", ""), false)
}

func TestReexecRunsTheInstalledBinaryWithTheSameArguments(t *testing.T) {
	var path string
	var argv []string
	err := Reexec("/claude", func(p string, a, _ []string) error {
		path, argv = p, a
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, path, "/claude/long-running/current/bin/lr")
	equal(t, argv[0], path)
}

type ctl struct {
	calls  []string
	loaded []int
}

func (c *ctl) run(argv []string) (shell.Result, error) {
	verb := argv[1]
	c.calls = append(c.calls, verb)
	if verb != "print" || len(c.loaded) == 0 {
		return shell.Result{}, nil
	}
	code := c.loaded[0]
	c.loaded = c.loaded[1:]
	if code == 0 {
		return shell.Result{}, nil
	}
	return shell.Result{Code: code}, &shell.ExitError{Argv: argv, Code: code}
}

func agent() Agent {
	return Agent{Drive: "d1", Config: "/state/lr.json", Binary: "/claude/long-running/current/bin/lr", Log: "/state/supervisor.log"}
}

func TestAReinstallBootstrapsOnlyOnceLaunchdHasFinishedBootingOutTheOldCopy(t *testing.T) {
	c := &ctl{loaded: []int{0, 0, 0, 113}}
	var out bytes.Buffer
	l := launchd.Launchd{Runner: scripted(c.run), Home: t.TempDir(), UID: 501, Sleep: func(time.Duration) {}, Out: &out}

	if err := l.Install(context.Background(), agent().Job()); err != nil {
		t.Fatal(err)
	}

	equal(t, c.calls, []string{"print", "bootout", "print", "print", "print", "bootstrap"})
}

func TestTheSupervisorAgentNamesTheInstallLinkSoAPluginUpdateNeverChangesIt(t *testing.T) {
	c := &ctl{loaded: []int{113}}
	var out bytes.Buffer
	home := t.TempDir()
	l := launchd.Launchd{Runner: scripted(c.run), Home: home, UID: 501, Sleep: func(time.Duration) {}, Out: &out}

	if err := l.Install(context.Background(), agent().Job()); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(filepath.Join(home, "Library", "LaunchAgents", Label("d1")+".plist"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(body, []byte("<string>/claude/long-running/current/bin/lr</string>")) {
		t.Fatal(string(body))
	}
	c.calls, c.loaded = nil, []int{0}

	if err := l.Install(context.Background(), agent().Job()); err != nil {
		t.Fatal(err)
	}

	equal(t, c.calls, []string{"print"})
}

func TestUninstallBootsOutAndRemovesThePlist(t *testing.T) {
	c := &ctl{loaded: []int{113}}
	var out bytes.Buffer
	home := t.TempDir()
	l := launchd.Launchd{Runner: scripted(c.run), Home: home, UID: 501, Sleep: func(time.Duration) {}, Out: &out}
	if err := l.Install(context.Background(), agent().Job()); err != nil {
		t.Fatal(err)
	}
	c.calls, c.loaded = nil, []int{0}

	if err := l.Uninstall(context.Background(), Label("d1")); err != nil {
		t.Fatal(err)
	}

	equal(t, c.calls, []string{"print", "bootout"})
	if _, err := os.Stat(filepath.Join(home, "Library", "LaunchAgents", Label("d1")+".plist")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("err %v", err)
	}
}

func TestThePlistEscapesItsValuesAndCarriesTheKeepAliveAndLimits(t *testing.T) {
	a := agent()
	a.Config = "/state/a&b/lr.json"

	body := string(a.Job().Plist())

	for _, want := range []string{"/state/a&amp;b/lr.json", "<key>KeepAlive</key>\n\t<true/>", "<integer>65536</integer>", "<integer>60</integer>", "<string>com.long-running.d1.supervisor</string>", "<string>--config</string>"} {
		if !strings.Contains(body, want) {
			t.Fatalf("%q not in\n%s", want, body)
		}
	}
}

func TestThePlistStartsTheSupervisorUnderItsOwnLane(t *testing.T) {
	body := string(agent().Job().Plist())

	if want := "<key>CLAUDE_LONG_RUNNING_LANE</key>\n\t\t<string>supervisor</string>"; !strings.Contains(body, want) {
		t.Fatalf("%q not in\n%s", want, body)
	}
}

func TestALandablePRAboveAnUntappedOneIsLeftOutOfTheQueue(t *testing.T) {
	s, c, q := memory(t), &clock{t0}, &queue{}
	lower := prFacts(1, func(f *machine.Facts) { f.Branch, f.Tap = "lower", "" })
	upper := prFacts(2, func(f *machine.Facts) { f.Branch, f.Base = "upper", "lower" })

	runLoop(t, s, "0.8.0", 1, deps(c, q, &pool{}, lower, upper), 1)

	equal(t, len(q.enqueued), 0)
}

func TestAPassThatCannotReadItsPRsTellsRootOncePerCause(t *testing.T) {
	s, c, q, p := memory(t), &clock{t0}, &queue{}, &pool{}
	lease, err := s.AcquireLease("d", "0.8.0", 101, t0)
	if err != nil {
		t.Fatal(err)
	}
	d := deps(c, q, p)
	failures := []string{"ccx exited 1 at 04:01:02", "ccx exited 1 at 04:07:44", "gh: not found"}
	d.Snapshot = func(context.Context) ([]machine.Facts, error) {
		failure := failures[0]
		failures = failures[1:]
		return nil, errors.New(failure)
	}
	var said []string
	d.Post = func(to []string, body bus.Body) error {
		said = append(said, to[0]+": "+body.(bus.Escalation).Cause)
		return nil
	}

	for range 3 {
		if _, err := Tick(context.Background(), s, "d", lease.Generation, d); err != nil {
			t.Fatal(err)
		}
		c.now = c.now.Add(PassEvery)
	}

	prefix := "the PR read failed, so this pass moved nothing and the next one reads again: "
	equal(t, said, []string{"root: " + prefix + "ccx exited 1 at 04:01:02", "root: " + prefix + "gh: not found"})
}

func TestThePassLogsWhyEachClearedHeadDoesNotEnqueue(t *testing.T) {
	s, c, q, p := memory(t), &clock{t0}, &queue{}, &pool{}
	lower := prFacts(1, func(f *machine.Facts) { f.Branch, f.CI = "b1", machine.CIPending })
	upper := prFacts(2, func(f *machine.Facts) { f.Branch, f.Base = "b2", "b1" })
	orphan := prFacts(3, func(f *machine.Facts) { f.Branch, f.Base = "b3", "merged-away" })
	var logged bytes.Buffer
	d := deps(c, q, p, lower, upper, orphan)
	d.Log = slog.New(slog.NewJSONHandler(&logged, nil))

	runLoop(t, s, "0.8.0", 1, d, 1)

	equal(t, q.enqueued, []int(nil))
	for _, want := range []string{
		`"msg":"a cleared head waits","pr":1,"head":"` + head + `","why":"its CI is not green"`,
		`"msg":"a cleared head waits","pr":2,"head":"` + head + `","why":"a PR below it is not clear to land"`,
		`"msg":"an enqueue was dropped","pr":3,"head":"` + head + `","why":"its base is not trunk or an open PR of the drive"`,
	} {
		if !strings.Contains(logged.String(), want) {
			t.Fatalf("log = %s, want a line holding %s", logged.String(), want)
		}
	}
}
