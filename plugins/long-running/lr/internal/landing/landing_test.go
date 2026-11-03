package landing_test

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	repo  = "org/repo"
	head  = "e718a7434fa01ee9835f992a9a647a825350732c"
	moved = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
)

var t0 = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)

func facts(number int, change func(*machine.Facts)) machine.Facts {
	f := machine.Facts{
		Repo: repo, Number: number, Head: head, Base: "dev", Lane: "lane-7",
		CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true,
		Finder: machine.FinderClear, Fidelity: machine.FidelityPass, Tap: machine.TapTapped,
	}
	if change != nil {
		change(&f)
	}
	return f
}

func newStore(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func evicted(f *machine.Facts)  { f.Queue = machine.QueueEvicted }
func conflict(f *machine.Facts) { f.Mergeable = machine.MergeableConflict }

type queue struct {
	enqueued []landing.Head
	dequeued []int
}

func (q *queue) Enqueue(_ context.Context, number int, head string) (string, error) {
	q.enqueued = append(q.enqueued, landing.Head{Number: number, Head: head})
	return fmt.Sprintf("exit 0: #%d QUEUED", number), nil
}

func (q *queue) Dequeue(_ context.Context, number int) error {
	q.dequeued = append(q.dequeued, number)
	return nil
}

type restacker struct {
	result string
	err    error
	calls  []landing.Move
}

func (r *restacker) Restack(_ context.Context, number int, head, base string) (string, error) {
	r.calls = append(r.calls, landing.Move{Number: number, From: head, To: base})
	return r.result, r.err
}

func acts(t *testing.T, s *store.Store, now time.Time, snapshot ...machine.Facts) []pass.Act {
	t.Helper()
	out, err := pass.Run(s, snapshot, now, func([]string, bus.Body) error { return nil }, []machine.Mover{machine.MoverSupervisor}, func(desk string) string { return desk })
	if err != nil {
		t.Fatal(err)
	}
	return out.Acts
}

func act(t *testing.T, s *store.Store, q *queue, r *restacker, now time.Time, snapshot ...machine.Facts) landing.Done {
	t.Helper()
	done, err := landing.Act(context.Background(), s, q, r, acts(t, s, now, snapshot...), now)
	if err != nil {
		t.Fatal(err)
	}
	return done
}

func fact(t *testing.T, s *store.Store, number int, kind string) string {
	t.Helper()
	values, err := s.Facts(repo, number, head)
	if err != nil {
		t.Fatal(err)
	}
	return values[kind]
}

func TestALandableHeadIsEnqueuedTheSamePass(t *testing.T) {
	s, q := newStore(t), &queue{}

	done := act(t, s, q, &restacker{}, t0, facts(45583, nil))

	want := []landing.Head{{Number: 45583, Head: head}}
	if !slices.Equal(q.enqueued, want) || !slices.Equal(done.Enqueued, want) {
		t.Fatalf("enqueued = %v, done = %v", q.enqueued, done.Enqueued)
	}
}

func TestAnEvictedGreenHeadIsResubmittedAndCounted(t *testing.T) {
	s, q := newStore(t), &queue{}

	done := act(t, s, q, &restacker{}, t0, facts(45711, evicted))

	want := []landing.Head{{Number: 45711, Head: head}}
	if !slices.Equal(q.enqueued, want) || !slices.Equal(done.Resubmitted, want) || len(done.Enqueued) != 0 {
		t.Fatalf("enqueued = %v, done = %+v", q.enqueued, done)
	}
	if got := fact(t, s, 45711, landing.ResubmittedFact); got != "1" {
		t.Fatalf("resubmitted = %q", got)
	}
}

func TestASecondEvictionIsNotResubmittedButGoesRedToTheLane(t *testing.T) {
	s := newStore(t)
	second := facts(45711, func(f *machine.Facts) { f.Queue, f.Resubmitted = machine.QueueEvicted, 1 })

	if got := machine.Classify(second).State; got != machine.StateRed {
		t.Fatalf("state = %q", got)
	}
	if done := act(t, s, &queue{}, &restacker{}, t0, second); len(done.Resubmitted) != 0 {
		t.Fatalf("done = %+v", done)
	}
}

func TestAQueuedPRWhoseHeadMovedIsDequeued(t *testing.T) {
	s, q := newStore(t), &queue{}

	done := act(t, s, q, &restacker{}, t0, facts(45744, func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, moved }))

	if !slices.Equal(q.dequeued, []int{45744}) || !slices.Equal(done.Dequeued, []int{45744}) || len(q.enqueued) != 0 {
		t.Fatalf("queue = %+v, done = %+v", q, done)
	}
}

func TestAConflictIsRestackedMechanicallyAndTheNewHeadRecorded(t *testing.T) {
	s, r := newStore(t), &restacker{result: moved}

	done := act(t, s, &queue{}, r, t0, facts(45410, conflict))

	if !slices.Equal(r.calls, []landing.Move{{Number: 45410, From: head, To: "dev"}}) {
		t.Fatalf("calls = %+v", r.calls)
	}
	if !slices.Equal(done.Restacked, []landing.Move{{Number: 45410, From: head, To: moved}}) {
		t.Fatalf("restacked = %+v", done.Restacked)
	}
	if got := fact(t, s, 45410, landing.RestackedFact); got != moved {
		t.Fatalf("restacked fact = %q", got)
	}
}

func TestASemanticConflictIsTriedOnceThenLeftToTheLane(t *testing.T) {
	s, r := newStore(t), &restacker{}
	given := facts(45580, conflict)

	first := act(t, s, &queue{}, r, t0, given)
	second := act(t, s, &queue{}, r, t0.Add(5*time.Minute), given)

	if !slices.Equal(first.Conflicts, []int{45580}) || len(second.Conflicts) != 0 || len(r.calls) != 1 {
		t.Fatalf("first = %+v, second = %+v, calls = %+v", first, second, r.calls)
	}
	if got := fact(t, s, 45580, landing.RestackedFact); got != "conflict" {
		t.Fatalf("restacked fact = %q", got)
	}
}

func TestAnUntappedOrRedHeadIsNeverEnqueued(t *testing.T) {
	s, q := newStore(t), &queue{}

	act(t, s, q, &restacker{}, t0,
		facts(1, func(f *machine.Facts) { f.Tap = "" }),
		facts(2, func(f *machine.Facts) { f.CI = machine.CIFailure }),
		facts(3, func(f *machine.Facts) { f.Finder = machine.FinderAsk }),
	)

	if len(q.enqueued) != 0 {
		t.Fatalf("enqueued = %v", q.enqueued)
	}
}

func TestOneActionPerPRPerPassAndNothingTwiceWhileTheFactsHold(t *testing.T) {
	s, q := newStore(t), &queue{}

	act(t, s, q, &restacker{}, t0, facts(45583, nil))
	act(t, s, q, &restacker{}, t0.Add(5*time.Minute), facts(45583, func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, head }))

	if !slices.Equal(q.enqueued, []landing.Head{{Number: 45583, Head: head}}) || len(q.dequeued) != 0 {
		t.Fatalf("queue = %+v", q)
	}
}

func TestWhatTheQueueSaidIsTheHeadsEnqueuedFact(t *testing.T) {
	s := newStore(t)

	act(t, s, &queue{}, &restacker{}, t0, facts(45583, nil))

	if got := fact(t, s, 45583, pass.EnqueuedFact); got != "exit 0: #45583 QUEUED" {
		t.Fatalf("enqueued fact = %q", got)
	}
}

func TestAnUntappedConflictIsLeftToItsLaneAndNeverRebasedUnderIt(t *testing.T) {
	s, r := newStore(t), &restacker{result: moved}

	done := act(t, s, &queue{}, r, t0, facts(45410, func(f *machine.Facts) { f.Mergeable, f.Tap = machine.MergeableConflict, "" }))

	if len(r.calls) != 0 || len(done.Restacked) != 0 {
		t.Fatalf("calls = %+v, done = %+v", r.calls, done)
	}
}

func TestARestackedHeadIsNotRestackedAgainAndAPassRestacksAtMostTwo(t *testing.T) {
	s, r := newStore(t), &restacker{result: "moved"}
	behind := func(f *machine.Facts) { f.Mergeable = machine.MergeableBehind }
	snapshot := []machine.Facts{facts(1, behind), facts(2, behind), facts(3, behind)}

	first := act(t, s, &queue{}, r, t0, snapshot...)
	second := act(t, s, &queue{}, r, t0.Add(3*time.Minute), snapshot...)

	numbers := func(moves []landing.Move) []int {
		got := make([]int, 0, len(moves))
		for _, move := range moves {
			got = append(got, move.Number)
		}
		return got
	}
	if !slices.Equal(numbers(first.Restacked), []int{1, 2}) || !slices.Equal(numbers(second.Restacked), []int{3}) || !slices.Equal(numbers(r.calls), []int{1, 2, 3}) {
		t.Fatalf("first = %+v, second = %+v, calls = %+v", first.Restacked, second.Restacked, r.calls)
	}
}

func TestARestackFailureStopsTheMoversAndWritesNoFactSoTheNextPassRetries(t *testing.T) {
	s, q := newStore(t), &queue{}
	r := &restacker{err: errors.New("the sprite did not answer")}
	snapshot := []machine.Facts{facts(1, conflict), facts(2, nil)}

	done, err := landing.Act(context.Background(), s, q, r, acts(t, s, t0, snapshot...), t0)

	if err == nil || !reflect.DeepEqual(done, landing.Done{}) || len(q.enqueued) != 0 {
		t.Fatalf("err = %v, done = %+v, enqueued = %v", err, done, q.enqueued)
	}
	if got := fact(t, s, 1, landing.RestackedFact); got != "" {
		t.Fatalf("restacked fact = %q", got)
	}
}
