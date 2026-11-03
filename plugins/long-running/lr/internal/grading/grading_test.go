package grading

import (
	"fmt"
	"reflect"
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
	jobs []Dispatched
	box  string
	gone map[string]bool
}

func (p *fakePool) Dispatch(grader, _ string, number int, head string) (string, error) {
	p.jobs = append(p.jobs, Dispatched{grader, number, head})
	return fmt.Sprintf("%s/%s/%d", p.box, grader, number), nil
}

func (p *fakePool) Alive(worker string) bool {
	return !p.gone[worker]
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
	dispatched, err := Act(s, pool, open, map[int]machine.Facts{number: f}, at, 1)
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
