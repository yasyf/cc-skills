package grading

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	repo   = "org/repo"
	number = 45489
	head   = "e718a7434fa01ee9835f992a9a647a825350732c"
	newer  = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
)

var t0 = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)

type fakePool struct {
	jobs    []Dispatched
	box     string
	gone    map[string]bool
	refuse  map[string]error
	refused []string
	queued  []int
	settles int

	reversed bool
	wholes   []string
	told     []string
	parked   map[int]string
}

func (p *fakePool) TellStopped(_ context.Context, _ machine.Facts, why string) error {
	p.told = append(p.told, why)
	return nil
}

func (p *fakePool) Plan(_ context.Context, items []store.Item, _ map[int]machine.Facts, queued int, _ time.Time) ([]store.Item, error) {
	p.queued = append(p.queued, queued)
	if p.reversed {
		items = slices.Clone(items)
		slices.Reverse(items)
	}
	return items, nil
}

func (p *fakePool) Whole(context.Context, map[int]machine.Facts) ([]string, error) {
	started := p.wholes
	p.wholes = nil
	return started, nil
}

func (p *fakePool) Reopen(context.Context, map[int]machine.Facts) ([]string, error) {
	return nil, nil
}

func (p *fakePool) Parked(context.Context, map[int]machine.Facts) (map[int]string, error) {
	return p.parked, nil
}

func (p *fakePool) Settle(_ context.Context, workers []string) (map[string]bool, error) {
	p.settles++
	live := make(map[string]bool, len(workers))
	for _, worker := range workers {
		live[worker] = !p.gone[worker]
	}
	return live, nil
}

func (p *fakePool) Dispatch(_ context.Context, grader string, f machine.Facts) (string, error) {
	if err := p.refuse[grader]; err != nil {
		p.refused = append(p.refused, grader)
		return "", err
	}
	p.jobs = append(p.jobs, Dispatched{grader, f.Number, f.Head})
	return fmt.Sprintf("%s/%s/%d", p.box, grader, f.Number), nil
}

func memory(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func facts(head string, mutate ...func(*machine.Facts)) machine.Facts {
	f := machine.Facts{Repo: repo, Number: number, Head: head, Base: "dev", Lane: "lane-7", CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true, Tap: machine.TapTapped}
	for _, change := range mutate {
		change(&f)
	}
	return f
}

func opened(t *testing.T, s *store.Store, head string) []store.Item {
	t.Helper()
	if _, err := s.OpenItem(store.NewItem{Kind: ItemKind, Repo: repo, Number: number, Head: head, Mover: machine.MoverPool, Deadline: machine.GradeClock}, t0); err != nil {
		t.Fatal(err)
	}
	return items(t, s)
}

func items(t *testing.T, s *store.Store) []store.Item {
	t.Helper()
	open, err := s.OpenItems(repo, 0)
	if err != nil {
		t.Fatal(err)
	}
	return open
}

func act(t *testing.T, s *store.Store, pool Pool, open []store.Item, f machine.Facts, at time.Time) []Dispatched {
	t.Helper()
	if err := Settle(context.Background(), s, pool, at, 1); err != nil {
		t.Fatal(err)
	}
	dispatched, err := Act(context.Background(), s, pool, open, map[int]machine.Facts{number: f}, at, 1)
	if err != nil {
		t.Fatal(err)
	}
	return dispatched
}

func graders(dispatched []Dispatched) []string {
	names := make([]string, 0, len(dispatched))
	for _, d := range dispatched {
		names = append(names, d.Grader)
	}
	return names
}

func workerNames(t *testing.T, s *store.Store) []string {
	t.Helper()
	workers, err := s.Workers()
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(workers))
	for _, worker := range workers {
		names = append(names, worker.Name)
	}
	return names
}

func TestAnUngradedHeadGetsBothGradersOnce(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	open := opened(t, s, head)

	first := act(t, s, pool, open, facts(head), t0)
	second := act(t, s, pool, items(t, s), facts(head), t0.Add(2*time.Minute))

	want := []Dispatched{{GraderFinder, number, head}, {GraderFidelity, number, head}}
	if !reflect.DeepEqual(first, want) {
		t.Fatalf("first = %v, want %v", first, want)
	}
	if len(second) != 0 {
		t.Fatalf("second = %v, want none", second)
	}
	if !reflect.DeepEqual(pool.jobs, want) {
		t.Fatalf("jobs = %v, want %v", pool.jobs, want)
	}
	if got, want := workerNames(t, s), []string{"sprite-1/fidelity/45489", "sprite-1/finder/45489"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("workers = %v, want %v", got, want)
	}
}

func TestAHeldHeadTakesNoGraderUntilTheHoldLiftsUnlessItHoldsForHisTap(t *testing.T) {
	cases := []struct {
		name string
		hold func(*machine.Facts)
		want []Dispatched
	}{
		{"root hold", func(f *machine.Facts) { f.Hold, f.HoldRecord = machine.MoverRoot, "#481773" }, nil},
		{"owner hold", func(f *machine.Facts) { f.Hold, f.HoldRecord = machine.MoverOwner, "#481773" }, nil},
		{"held for his tap", func(f *machine.Facts) { f.Hold, f.HoldRecord, f.HoldAwaitsTap = machine.MoverRoot, "#481773", true }, []Dispatched{{GraderFinder, number, head}, {GraderFidelity, number, head}}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s, pool := memory(t), &fakePool{box: "sprite-1"}
			held := act(t, s, pool, opened(t, s, head), facts(head, c.hold), t0)
			if !reflect.DeepEqual(held, c.want) {
				t.Fatalf("held = %v, want %v", held, c.want)
			}
			if c.want != nil {
				return
			}
			lifted := act(t, s, pool, items(t, s), facts(head), t0.Add(time.Minute))
			if want := []Dispatched{{GraderFinder, number, head}, {GraderFidelity, number, head}}; !reflect.DeepEqual(lifted, want) {
				t.Fatalf("lifted = %v, want %v", lifted, want)
			}
		})
	}
}

func TestOnlyTheMissingGraderIsDispatched(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	graded := facts(head, func(f *machine.Facts) { f.Finder = machine.FinderClear })

	act(t, s, pool, opened(t, s, head), graded, t0)

	if want := []Dispatched{{GraderFidelity, number, head}}; !reflect.DeepEqual(pool.jobs, want) {
		t.Fatalf("jobs = %v, want %v", pool.jobs, want)
	}
}

func TestAJobThatDiedIsDispatchedAgainAfterTheDeadline(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	act(t, s, pool, opened(t, s, head), facts(head), t0)

	quiet := act(t, s, pool, items(t, s), facts(head), t0.Add(9*time.Minute))
	late := act(t, s, pool, items(t, s), facts(head), t0.Add(10*time.Minute))

	if len(quiet) != 0 {
		t.Fatalf("quiet = %v, want none", quiet)
	}
	if got, want := graders(late), []string{GraderFinder, GraderFidelity}; !reflect.DeepEqual(got, want) {
		t.Fatalf("late = %v, want %v", got, want)
	}
}

func TestAJobWhoseExecutorIsGoneIsDispatchedAgainInTheSamePass(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	act(t, s, pool, opened(t, s, head), facts(head), t0)
	pool.gone = map[string]bool{"sprite-1/finder/45489": true, "sprite-1/fidelity/45489": true}
	pool.box = "sprite-2"

	again := act(t, s, pool, items(t, s), facts(head), t0.Add(time.Minute))

	if got, want := graders(again), []string{GraderFinder, GraderFidelity}; !reflect.DeepEqual(got, want) {
		t.Fatalf("again = %v, want %v", got, want)
	}
	if got := items(t, s)[0].Worker; got != "sprite-2/fidelity/45489" {
		t.Fatalf("worker = %q, want sprite-2/fidelity/45489", got)
	}
	if got, want := workerNames(t, s), []string{"sprite-2/fidelity/45489", "sprite-2/finder/45489"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("workers = %v, want %v", got, want)
	}
}

func TestALiveJobBeforeItsDeadlineIsLeftAlone(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	act(t, s, pool, opened(t, s, head), facts(head), t0)

	if again := act(t, s, pool, items(t, s), facts(head), t0.Add(time.Minute)); len(again) != 0 {
		t.Fatalf("again = %v, want none", again)
	}
}

func TestAStaleJobReleasesTheItemAndTheNextPassDispatchesAgain(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	act(t, s, pool, opened(t, s, head), facts(head), t0)
	at := t0.Add(16 * time.Minute)
	stale, err := s.StaleWorkers(at)
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range stale {
		if _, err := s.ReleaseWorker(name); err != nil {
			t.Fatal(err)
		}
	}

	again := act(t, s, pool, items(t, s), facts(head), at)

	if len(again) != 2 {
		t.Fatalf("again = %v, want both graders", again)
	}
	if got := items(t, s)[0].Worker; got != "sprite-1/fidelity/45489" {
		t.Fatalf("worker = %q, want sprite-1/fidelity/45489", got)
	}
}

func TestAnItemForAnOlderHeadDispatchesNothing(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}

	act(t, s, pool, opened(t, s, head), facts(newer), t0)

	if len(pool.jobs) != 0 {
		t.Fatalf("jobs = %v, want none", pool.jobs)
	}
}

func TestASamePatchRebaseCarriesTheGradesAndTheVerdict(t *testing.T) {
	s := memory(t)
	for kind, value := range map[string]string{"finder": "ask", "fidelity": "pass", "verdict": "accept", "tap": "tapped"} {
		if _, err := s.PutFact(repo, number, head, kind, value, t0); err != nil {
			t.Fatal(err)
		}
	}

	carried, err := CarryGrades(s, repo, number, head, newer, map[string]string{head: "p1", newer: "p1"}, t0)
	if err != nil {
		t.Fatal(err)
	}

	if want := []string{"fidelity", "finder", "verdict"}; !reflect.DeepEqual(carried, want) {
		t.Fatalf("carried = %v, want %v", carried, want)
	}
	got, err := s.Facts(repo, number, newer)
	if err != nil {
		t.Fatal(err)
	}
	if want := map[string]string{"finder": "ask", "fidelity": "pass", "verdict": "accept"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("facts = %v, want %v", got, want)
	}
}

func TestAChangedPatchCarriesNothingAndIsGradedAgain(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	for _, kind := range []string{"finder", "fidelity"} {
		if _, err := s.PutFact(repo, number, head, kind, "clear", t0); err != nil {
			t.Fatal(err)
		}
	}

	carried, err := CarryGrades(s, repo, number, head, newer, map[string]string{head: "p1", newer: "p2"}, t0)
	if err != nil {
		t.Fatal(err)
	}
	if len(carried) != 0 {
		t.Fatalf("carried = %v, want none", carried)
	}

	act(t, s, pool, opened(t, s, newer), facts(newer), t0)
	if got, want := graders(pool.jobs), []string{GraderFinder, GraderFidelity}; !reflect.DeepEqual(got, want) {
		t.Fatalf("jobs = %v, want %v", got, want)
	}
}

func TestAFullGraderStopsOnlyItsOwnDispatchesAndTheOtherGoesOn(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1", refuse: map[string]error{GraderFinder: ErrNoCapacity}}
	if _, err := s.OpenItem(store.NewItem{Kind: ItemKind, Repo: repo, Number: number + 1, Head: newer, Mover: machine.MoverPool, Deadline: machine.GradeClock}, t0); err != nil {
		t.Fatal(err)
	}
	second := facts(newer, func(f *machine.Facts) { f.Number = number + 1 })
	open := opened(t, s, head)

	dispatched, err := Act(context.Background(), s, pool, open, map[int]machine.Facts{number: facts(head), number + 1: second}, t0, 1)
	if err != nil {
		t.Fatal(err)
	}

	if got, want := graders(dispatched), []string{GraderFidelity, GraderFidelity}; !reflect.DeepEqual(got, want) {
		t.Fatalf("dispatched = %v, want %v", got, want)
	}
	if want := []string{GraderFinder}; !reflect.DeepEqual(pool.refused, want) {
		t.Fatalf("refused = %v, want one refusal, %v", pool.refused, want)
	}
}

func TestAnAnsweredGraderHoldsNoWorkerAndTheOtherStillRuns(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1", refuse: map[string]error{GraderFinder: ErrAnswered}}

	dispatched := act(t, s, pool, opened(t, s, head), facts(head), t0)

	if got, want := graders(dispatched), []string{GraderFidelity}; !reflect.DeepEqual(got, want) {
		t.Fatalf("dispatched = %v, want %v", got, want)
	}
	if got, want := workerNames(t, s), []string{"sprite-1/fidelity/45489"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("workers = %v, want %v", got, want)
	}
}

func TestAnyOtherDispatchFailureStopsThePass(t *testing.T) {
	broken := errors.New("job not written")
	s, pool := memory(t), &fakePool{box: "sprite-1", refuse: map[string]error{GraderFinder: broken}}

	_, err := Act(context.Background(), s, pool, opened(t, s, head), map[int]machine.Facts{number: facts(head)}, t0, 1)

	if !errors.Is(err, broken) {
		t.Fatalf("err = %v, want %v", err, broken)
	}
}

func TestALiveWorkerStaysFreshPastTheStaleWindowOfALongPass(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	act(t, s, pool, opened(t, s, head), facts(head), t0)

	for minutes := 5; minutes <= 60; minutes += 5 {
		at := t0.Add(time.Duration(minutes) * time.Minute)
		act(t, s, pool, items(t, s), facts(head), at)
		stale, err := s.StaleWorkers(at)
		if err != nil {
			t.Fatal(err)
		}
		if len(stale) != 0 {
			t.Fatalf("at %d minutes stale = %v, want none", minutes, stale)
		}
	}
}

func TestAPassScalesToWhatIsMissing(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	onlyFidelity := facts(head, func(f *machine.Facts) { f.Finder = machine.FinderClear })

	act(t, s, pool, opened(t, s, head), onlyFidelity, t0)
	act(t, s, pool, items(t, s), onlyFidelity, t0.Add(time.Minute))

	if want := []int{1, 1}; !reflect.DeepEqual(pool.queued, want) {
		t.Fatalf("queued = %v, want %v", pool.queued, want)
	}
}

func TestOnlySettleReleasesAWorkerAndTheNextPassDispatchesForItsItem(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	dispatched, err := Act(context.Background(), s, pool, opened(t, s, head), map[int]machine.Facts{number: facts(head)}, t0, 1)
	if err != nil || len(dispatched) != 2 {
		t.Fatalf("dispatched = %v, %v", dispatched, err)
	}
	pool.gone = map[string]bool{"sprite-1/finder/45489": true, "sprite-1/fidelity/45489": true}

	held, err := Act(context.Background(), s, pool, items(t, s), map[int]machine.Facts{number: facts(head)}, t0.Add(time.Minute), 1)
	if err != nil || len(held) != 0 || len(workerNames(t, s)) != 2 {
		t.Fatalf("held = %v, workers = %v, %v, want a pass to leave its workers to Settle", held, workerNames(t, s), err)
	}
	readBefore := items(t, s)
	if err := Settle(context.Background(), s, pool, t0.Add(2*time.Minute), 1); err != nil {
		t.Fatal(err)
	}
	if got := workerNames(t, s); len(got) != 0 {
		t.Fatalf("workers = %v, want both released by Settle", got)
	}
	pool.box = "sprite-2"
	again, err := Act(context.Background(), s, pool, readBefore, map[int]machine.Facts{number: facts(head)}, t0.Add(2*time.Minute), 1)
	if err != nil {
		t.Fatal(err)
	}

	if got, want := graders(again), []string{GraderFinder, GraderFidelity}; !reflect.DeepEqual(got, want) {
		t.Fatalf("again = %v, want %v from items read before the release", got, want)
	}
	if pool.settles != 1 {
		t.Fatalf("settles = %d, want Settle alone to settle", pool.settles)
	}
}

func TestTheOrderThePoolRanksIsTheOrderItemsTakeSlots(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1", reversed: true}
	if _, err := s.OpenItem(store.NewItem{Kind: ItemKind, Repo: repo, Number: number + 1, Head: newer, Mover: machine.MoverPool, Deadline: machine.GradeClock}, t0); err != nil {
		t.Fatal(err)
	}
	open := opened(t, s, head)
	graded := func(f *machine.Facts) { f.Finder = machine.FinderClear }
	second := facts(newer, graded, func(f *machine.Facts) { f.Number = number + 1 })

	dispatched, err := Act(context.Background(), s, pool, open, map[int]machine.Facts{number: facts(head, graded), number + 1: second}, t0, 1)
	if err != nil {
		t.Fatal(err)
	}

	if len(dispatched) != 2 || dispatched[0].Number != number || dispatched[1].Number != number+1 {
		t.Fatalf("dispatched = %v, want the pool's order", dispatched)
	}
}

func TestAJobWhoseItemClosedStillSettlesAndFreesItsSlot(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1", gone: map[string]bool{}}
	act(t, s, pool, opened(t, s, head), facts(head), t0)
	for _, item := range items(t, s) {
		if err := s.CloseItem(item.ID, t0); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range workerNames(t, s) {
		pool.gone[name] = true
	}

	act(t, s, pool, nil, facts(head), t0.Add(3*time.Minute))

	if got := workerNames(t, s); len(got) != 0 {
		t.Fatalf("workers = %v, want both jobs settled once their item closed", got)
	}
}

func TestALaneRowIsNoGraderJobAndKeepsItsRow(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1", gone: map[string]bool{"lane-7": true}}
	if err := s.Heartbeat(store.Worker{Name: "lane-7", Generation: 1, Heartbeat: t0}); err != nil {
		t.Fatal(err)
	}

	act(t, s, pool, nil, facts(head), t0)

	if got := workerNames(t, s); !reflect.DeepEqual(got, []string{"lane-7"}) {
		t.Fatalf("workers = %v, want the lane's row kept", got)
	}
}

func TestAWholePassHoldsAWorkerWithNoItemUntilItsJobEnds(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1"}
	name := "sprite-1/finder-desk/45489-whole-300001"
	pool.wholes = []string{name}

	act(t, s, pool, nil, facts(head), t0)
	if got := workerNames(t, s); !reflect.DeepEqual(got, []string{name}) {
		t.Fatalf("workers = %v", got)
	}

	act(t, s, pool, nil, facts(head), t0.Add(20*time.Minute))
	if stale, err := s.StaleWorkers(t0.Add(20 * time.Minute)); err != nil || len(stale) != 0 {
		t.Fatalf("stale = %v, %v, want a live whole pass kept fresh", stale, err)
	}

	pool.gone = map[string]bool{name: true}
	act(t, s, pool, nil, facts(head), t0.Add(21*time.Minute))
	if got := workerNames(t, s); len(got) != 0 {
		t.Fatalf("workers = %v, want it released", got)
	}
}

func TestAnItemPastItsDeadlineThatNoPassWillGradeTellsOnceAndStopsTheHead(t *testing.T) {
	s := memory(t)
	pool := &fakePool{refuse: map[string]error{GraderFinder: &Stopped{"failed"}, GraderFidelity: &Stopped{"reader"}}}
	open := opened(t, s, head)

	act(t, s, pool, open, facts(head), t0.Add(machine.GradeClock-time.Minute))
	if len(pool.told) != 0 {
		t.Fatalf("told before the deadline: %v", pool.told)
	}

	act(t, s, pool, open, facts(head), t0.Add(machine.GradeClock))

	want := GraderFinder + ": failed; " + GraderFidelity + ": reader"
	if !slices.Equal(pool.told, []string{want}) {
		t.Fatalf("told = %v", pool.told)
	}
	stored, err := s.Facts(repo, number, head)
	if err != nil {
		t.Fatal(err)
	}
	if stored[machine.GradeStoppedFact] != want {
		t.Fatalf("facts = %v", stored)
	}
}

func TestAnItemWithAPassStillToRunDoesNotStopForTheOtherGrader(t *testing.T) {
	s := memory(t)
	pool := &fakePool{refuse: map[string]error{GraderFidelity: &Stopped{"reader"}}}

	act(t, s, pool, opened(t, s, head), facts(head), t0.Add(machine.GradeClock))

	if len(pool.told) != 0 {
		t.Fatalf("told = %v", pool.told)
	}
	if got := graders(pool.jobs); !slices.Equal(got, []string{GraderFinder}) {
		t.Fatalf("dispatched = %v", got)
	}
}

func TestWhatAParkedGradeWaitsOnStaysAFactAtItsHeadUntilItStopsWaiting(t *testing.T) {
	s, pool := memory(t), &fakePool{box: "sprite-1", parked: map[int]string{number: "fidelity waits on his answer to card rmw37abc"}}
	parked := facts(head, func(f *machine.Facts) { f.Finder, f.Fidelity = machine.FinderClear, machine.FidelityFail })

	act(t, s, pool, nil, parked, t0)
	marked, err := s.Facts(repo, number, head)
	if err != nil {
		t.Fatal(err)
	}
	pool.parked = nil
	act(t, s, pool, nil, parked, t0.Add(3*time.Minute))
	cleared, err := s.Facts(repo, number, head)
	if err != nil {
		t.Fatal(err)
	}

	if marked[machine.FidelityWaitsFact] != "fidelity waits on his answer to card rmw37abc" || cleared[machine.FidelityWaitsFact] != "" {
		t.Errorf("marked = %q, cleared = %q", marked[machine.FidelityWaitsFact], cleared[machine.FidelityWaitsFact])
	}
}
