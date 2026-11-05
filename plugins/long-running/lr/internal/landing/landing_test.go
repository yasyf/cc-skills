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

func evicted(f *machine.Facts)  { f.Queue, f.Eviction = machine.QueueEvicted, "a batch failed" }
func conflict(f *machine.Facts) { f.Mergeable = machine.MergeableConflict }

type queue struct {
	enqueued     []landing.Head
	dequeued     []int
	reentered    []int
	failure      landing.Failure
	states       map[int]landing.QueueEntry
	statusReads  int
	failureReads int
	since        time.Time
	refused      bool
}

func (q *queue) Enqueue(_ context.Context, number int, head string) (string, error) {
	q.enqueued = append(q.enqueued, landing.Head{Number: number, Head: head})
	return fmt.Sprintf("exit 0: #%d QUEUED", number), nil
}

func (q *queue) Reenter(_ context.Context, number int) (string, error) {
	q.reentered = append(q.reentered, number)
	if q.refused {
		return fmt.Sprintf("exit 5: #%d GitHub still computing mergeability", number), nil
	}
	return fmt.Sprintf("exit 0: #%d QUEUED", number), nil
}

func (q *queue) LastFailure(_ context.Context, _ int, since time.Time) (landing.Failure, error) {
	q.failureReads++
	q.since = since
	return q.failure, nil
}

func (q *queue) States(context.Context, []int) (map[int]landing.QueueEntry, error) {
	q.statusReads++
	return q.states, nil
}

func (q *queue) Dequeue(_ context.Context, number int) (string, error) {
	q.dequeued = append(q.dequeued, number)
	return fmt.Sprintf("exit 0: #%d DEQUEUED", number), nil
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
	out, err := pass.Run(s, snapshot, now, func([]string, bus.Body) error { return nil }, []machine.Mover{machine.MoverSupervisor}, func(desk string) string { return desk }, func(lane string, _ int) (string, error) { return lane, nil })
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

func TestAPRIsEnqueuedOnlyWhenEveryPRBelowItWouldLandOrIsQueued(t *testing.T) {
	stacked := func(number int, branch, base string, change func(*machine.Facts)) machine.Facts {
		return facts(number, func(f *machine.Facts) {
			f.Branch, f.Base = branch, base
			if change != nil {
				change(f)
			}
		})
	}
	queued := func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, f.Head }
	untapped := func(f *machine.Facts) { f.Tap = "" }
	held := func(f *machine.Facts) { f.Hold = machine.MoverRoot }
	for name, c := range map[string]struct {
		below []machine.Facts
		base  string
		want  bool
	}{
		"on trunk":                {nil, "dev", true},
		"on a landable PR":        {[]machine.Facts{stacked(1, "lower", "dev", nil)}, "lower", true},
		"on a queued PR":          {[]machine.Facts{stacked(1, "lower", "dev", queued)}, "lower", true},
		"on a queued untapped PR": {[]machine.Facts{stacked(1, "lower", "dev", func(f *machine.Facts) { queued(f); untapped(f) })}, "lower", false},
		"on an untapped PR":       {[]machine.Facts{stacked(1, "lower", "dev", untapped)}, "lower", false},
		"on a held PR":            {[]machine.Facts{stacked(1, "lower", "dev", held)}, "lower", false},
		"two above an untapped":   {[]machine.Facts{stacked(1, "lowest", "dev", untapped), stacked(3, "lower", "lowest", nil)}, "lower", false},
		"on a branch nobody read": {nil, "unread", false},
		"on a base cycle":         {[]machine.Facts{stacked(1, "lower", "upper", nil)}, "lower", false},
	} {
		t.Run(name, func(t *testing.T) {
			top := stacked(45583, "upper", c.base, nil)
			act := pass.Act{Facts: top, Verdict: machine.Verdict{Action: machine.ActionEnqueue}}

			kept := landing.Clear([]pass.Act{act}, append(c.below, top), "dev")

			if got := len(kept) == 1; got != c.want {
				t.Fatalf("kept = %v, want %v", kept, c.want)
			}
		})
	}
}

func TestClearLeavesActsOtherThanEnqueueAlone(t *testing.T) {
	act := pass.Act{Facts: facts(45583, func(f *machine.Facts) { f.Base = "unread" }), Verdict: machine.Verdict{Action: machine.ActionRestack}}

	if kept := landing.Clear([]pass.Act{act}, nil, "dev"); len(kept) != 1 {
		t.Fatalf("kept = %v, want the restack", kept)
	}
}

func TestAnEvictedGreenHeadIsResubmittedAndCounted(t *testing.T) {
	s, q := newStore(t), &queue{}

	done := act(t, s, q, &restacker{}, t0, facts(45711, evicted))

	want := []landing.Head{{Number: 45711, Head: head}}
	if !slices.Equal(q.reentered, []int{45711}) || len(q.enqueued) != 0 || !slices.Equal(done.Resubmitted, want) || len(done.Enqueued) != 0 {
		t.Fatalf("enqueued = %v, done = %+v", q.enqueued, done)
	}
	if got := fact(t, s, 45711, landing.ResubmittedFact); got != "1" {
		t.Fatalf("resubmitted = %q", got)
	}
}

func TestARefusedReentryDoesNotSpendItsRetry(t *testing.T) {
	s, q := newStore(t), &queue{refused: true}

	act(t, s, q, &restacker{}, t0, facts(45711, evicted))

	if fact(t, s, 45711, landing.ResubmittedFact) != "" || fact(t, s, 45711, landing.FreeRetriesFact) != "1" {
		t.Fatalf("resubmitted = %q", fact(t, s, 45711, landing.ResubmittedFact))
	}
}

func TestAFourthRefusedReentrySpendsTheRetrySoItCannotLoop(t *testing.T) {
	s, q := newStore(t), &queue{refused: true}

	for range 4 {
		act(t, s, q, &restacker{}, t0, facts(45711, evicted))
	}

	if fact(t, s, 45711, landing.ResubmittedFact) != "1" || fact(t, s, 45711, landing.FreeRetriesFact) != "3" {
		t.Fatalf("resubmitted = %q, free = %q", fact(t, s, 45711, landing.ResubmittedFact), fact(t, s, 45711, landing.FreeRetriesFact))
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

func TestAQueuedPRWithNoTapAtItsHeadIsDequeuedWithAnAlarm(t *testing.T) {
	s, q := newStore(t), &queue{}

	done := act(t, s, q, &restacker{}, t0, facts(46927, func(f *machine.Facts) { f.Queue, f.QueuedHead, f.Tap = machine.QueueQueued, head, "" }))

	if !slices.Equal(q.dequeued, []int{46927}) || len(done.Said) != 1 || !done.Said[0].Alarm || done.Said[0].Accepted != "UNTAPPED-DEQUEUED" {
		t.Fatalf("queue = %+v, done = %+v", q, done)
	}
}

func TestAQueuedPRNoLaneOfThisDriveOwnsStaysInTheQueueWithNoAlarm(t *testing.T) {
	s, q := newStore(t), &queue{}

	done := act(t, s, q, &restacker{}, t0, facts(49110, func(f *machine.Facts) { f.Queue, f.QueuedHead, f.Tap, f.Lane = machine.QueueQueued, head, "", "" }))

	if len(q.dequeued) != 0 || len(done.Said) != 0 {
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

func TestAHeldStackLeavesTheQueueTopFirstAndNoPRAboveItEnters(t *testing.T) {
	stacked := func(number int, branch, base string, change func(*machine.Facts)) machine.Facts {
		return facts(number, func(f *machine.Facts) {
			f.Branch, f.Base = branch, base
			if change != nil {
				change(f)
			}
		})
	}
	heldInQueue := func(f *machine.Facts) { f.Queue, f.QueuedHead, f.Hold = machine.QueueQueued, f.Head, machine.MoverRoot }
	inQueue := func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, f.Head }
	bottom := stacked(1, "bottom", "dev", heldInQueue)
	middle := stacked(2, "middle", "bottom", inQueue)
	waiting := stacked(3, "top", "middle", nil)
	out := func(f machine.Facts) pass.Act {
		return pass.Act{Facts: f, Verdict: machine.Verdict{Action: machine.ActionDequeue}}
	}

	acts := landing.Clear([]pass.Act{
		out(bottom),
		{Facts: waiting, Verdict: machine.Verdict{Action: machine.ActionEnqueue}},
		out(middle),
	}, []machine.Facts{bottom, middle, waiting}, "dev")

	if len(acts) != 2 || acts[0].Facts.Number != 2 || acts[1].Facts.Number != 1 {
		t.Fatalf("acts = %+v", acts)
	}
}

func TestAHandEnqueueGateNamesEachGateAndCallsANoTapTapRequired(t *testing.T) {
	lines := landing.GateLines(facts(44705, func(f *machine.Facts) { f.Tap, f.CI = "", machine.CIFailure }))

	want := []string{"#44705 " + head[:10] + " CI is not green", "#44705 " + head[:10] + " TAP-REQUIRED no approval on the owner's board at this head"}
	if !slices.Equal(lines, want) {
		t.Fatalf("lines = %q", lines)
	}
	if lines := landing.GateLines(facts(44706, nil)); len(lines) != 0 {
		t.Fatalf("a head that passes printed %q", lines)
	}
}
