package machine

import (
	"slices"
	"testing"
	"time"
)

func clean(change func(*PR)) PR {
	pr := PR{Graded: true, Checks: CISuccess, Review: ReviewApproved, Tap: TapTapped}
	if change != nil {
		change(&pr)
	}
	return pr
}

func on(state State, mover Mover) *Place {
	return &Place{State: state, Mover: mover, Reason: "its own reason"}
}

func TestDecidePlacesOnePRByTheFirstGateItFails(t *testing.T) {
	ownerHold := Hold{MoverOwner, "the owner asked to read it first"}
	for _, tc := range []struct {
		name    string
		pr      PR
		beneath *Place
		want    Place
	}{
		{"a clean tapped PR with nothing beneath is ready", clean(nil), nil, Place{StateReady, MoverDesk, "every gate passes", 10 * time.Minute}},
		{"an exempt change goes straight to ready with no tap", clean(func(p *PR) { p.Tap, p.Exempt = "", "test-only" }), nil, Place{StateReady, MoverDesk, "every gate passes, exempt as test-only", 10 * time.Minute}},
		{"a repo that lands without a tap is ready untapped", clean(func(p *PR) { p.Tap, p.TapFree = "", true }), nil, Place{StateReady, MoverDesk, "every gate passes", 10 * time.Minute}},
		{"an untapped change waits on the owner", clean(func(p *PR) { p.Tap = "" }), nil, Place{StateAwaitingTap, MoverOwner, "clean, green and approved, with no tap on this change", 12 * time.Hour}},
		{"a conditional tap waits on the owner like no tap", clean(func(p *PR) { p.Tap = TapConditional }), nil, Place{StateAwaitingTap, MoverOwner, "clean, green and approved, with no tap on this change", 12 * time.Hour}},
		{"a withdrawn tap waits on the owner again", clean(func(p *PR) { p.Tap = TapWithdrawn }), nil, Place{StateAwaitingTap, MoverOwner, "clean, green and approved, with no tap on this change", 12 * time.Hour}},
		{"a red check outranks waiting on the owner", clean(func(p *PR) { p.Tap, p.Checks, p.Failing = "", CIFailure, []string{"go test", "lint"} }), nil, Place{StateNeedsFix, MoverLane, "a required check is red: go test, lint", 20 * time.Minute}},
		{"a hold outranks waiting on the owner", clean(func(p *PR) { p.Tap, p.Hold = "", ownerHold }), nil, Place{StateHeld, MoverOwner, "held: the owner asked to read it first", 12 * time.Hour}},
		{"a hold names its own lifter as the mover", clean(func(p *PR) { p.Hold = Hold{MoverRoot, "readers first"} }), nil, Place{StateHeld, MoverRoot, "held: readers first", 20 * time.Minute}},
		{"a head with no grades goes to the grader pool", clean(func(p *PR) { p.Graded = false }), nil, Place{StateGrading, MoverPool, "this head has no finder or fidelity grade yet", 10 * time.Minute}},
		{"a refused change is its lane's with no card, red or not", clean(func(p *PR) { p.Tap, p.Checks = TapRejected, CIFailure }), nil, Place{StateRejected, MoverLane, "the owner refused this change", 0}},
		{"a refused change at a head still being judged is grading", clean(func(p *PR) { p.Tap, p.Graded = TapRejected, false }), nil, Place{StateGrading, MoverPool, "this head has no finder or fidelity grade yet", 10 * time.Minute}},
		{"an incident fix lands approved and green, ungraded and untapped", clean(func(p *PR) { p.Incident, p.Graded, p.Finding, p.Tap = true, false, FindingUpheld, "" }), nil, Place{StateReady, MoverDesk, "every gate passes", 10 * time.Minute}},
		{"an open finding goes to the appeals worker", clean(func(p *PR) { p.Finding = FindingOpen }), nil, Place{StateAppeal, MoverAppeals, "a finding is open and not yet judged", 15 * time.Minute}},
		{"only a design conflict reaches the coordinator", clean(func(p *PR) { p.Finding = FindingConflict }), nil, Place{StateAppeal, MoverRoot, "a finding conflicts with the design and waits on the coordinator", 20 * time.Minute}},
		{"an upheld finding is the lane's to fix", clean(func(p *PR) { p.Finding = FindingUpheld }), nil, Place{StateNeedsFix, MoverLane, "a finding was upheld as real", 20 * time.Minute}},
		{"a conflict the replay could not solve is the lane's", clean(func(p *PR) { p.Unmergeable = true }), nil, Place{StateNeedsFix, MoverLane, "a conflict the replay could not solve", 20 * time.Minute}},
		{"a review asking for changes is the lane's", clean(func(p *PR) { p.Review = ReviewChanges }), nil, Place{StateNeedsFix, MoverLane, "a review asks for changes", 20 * time.Minute}},
		{"running checks wait on CI", clean(func(p *PR) { p.Checks = CIPending }), nil, Place{StateChecks, MoverChecks, "required checks are still running", 2 * time.Hour}},
		{"a missing approval waits on the review", clean(func(p *PR) { p.Review = "" }), nil, Place{StateChecks, MoverChecks, "waiting on the approving review", 20 * time.Minute}},
		{"a queued PR at its head stays queued", clean(func(p *PR) { p.Queued = true }), nil, Place{StateQueued, MoverQueue, "in the merge queue at this head", 30 * time.Minute}},
		{"a hold on a queued PR takes it out", clean(func(p *PR) { p.Queued, p.Hold = true, ownerHold }), nil, Place{StateHeld, MoverOwner, "held: the owner asked to read it first", 12 * time.Hour}},
		{"a refusal on a queued PR takes it out", clean(func(p *PR) { p.Queued, p.Tap = true, TapRejected }), nil, Place{StateRejected, MoverLane, "the owner refused this change", 0}},
		{"a hold beneath a queued PR takes it out", clean(func(p *PR) { p.Queued = true }), on(StateHeld, MoverRoot), Place{StateBelow, MoverBeneath, "the PR beneath is held: its own reason", 0}},
		{"a batch-mate's red re-enters at this head", clean(func(p *PR) { p.Eviction = EvictionBatchRed }), nil, Place{StateEvicted, MoverDesk, "a batch-mate's check was red, so it re-enters the queue at this head", 10 * time.Minute}},
		{"a reasonless cancel re-enters once", clean(func(p *PR) { p.Eviction = EvictionFlaky }), nil, Place{StateEvicted, MoverDesk, "the queue cancelled it as flaky or with no reason, so it re-enters once", 10 * time.Minute}},
		{"a second reasonless cancel is the lane's", clean(func(p *PR) { p.Eviction, p.Reentered = EvictionFlaky, true }), nil, Place{StateNeedsFix, MoverLane, "the queue cancelled it again after its one re-entry", 20 * time.Minute}},
		{"its own red in the queue never re-enters", clean(func(p *PR) { p.Eviction = EvictionOwnRed }), nil, Place{StateNeedsFix, MoverLane, "the queue evicted it for its own red check", 20 * time.Minute}},
		{"a conflict in the queue is restacked", clean(func(p *PR) { p.Eviction = EvictionConflict }), nil, Place{StateEvicted, MoverDesk, "the queue evicted it on a conflict, so it is restacked", 10 * time.Minute}},
		{"our own dequeue re-admits a PR that passes every gate", clean(func(p *PR) { p.Eviction = EvictionOurs }), nil, Place{StateReady, MoverDesk, "every gate passes", 10 * time.Minute}},
		{"a landed squash outranks GitHub calling it closed", clean(func(p *PR) { p.Landed, p.Closed = true, true }), nil, Place{StateLanded, MoverRelease, "its squash is on the landing branch", 0}},
		{"merged on GitHub but unseen on the landing branch is still queued, tapped or not", clean(func(p *PR) { p.Merged, p.Tap = true, "" }), nil, Place{StateQueued, MoverQueue, "merged, with its squash not yet seen on the landing branch", 30 * time.Minute}},
		{"merged into a parent branch is closed, never landed", clean(func(p *PR) { p.Closed = true }), nil, Place{StateClosed, MoverNobody, "closed without landing", 0}},
		{"a started release carries it", clean(func(p *PR) { p.Landed, p.Released = true, true }), nil, Place{StateReleased, MoverRelease, "a started release carries it", 0}},
		{"live everywhere is the end", clean(func(p *PR) { p.Landed, p.Released, p.Live = true, true, true }), nil, Place{StateLive, MoverNobody, "live on every target and tenant it touches", 0}},
		{"a tapped PR above one waiting on the owner is below", clean(nil), on(StateAwaitingTap, MoverOwner), Place{StateBelow, MoverBeneath, "the PR beneath is awaiting-tap: its own reason", 0}},
		{"an untapped PR above one waiting on the owner asks too, so a stack is tapped at once", clean(func(p *PR) { p.Tap = "" }), on(StateAwaitingTap, MoverOwner), Place{StateAwaitingTap, MoverOwner, "clean, green and approved, with no tap on this change", 12 * time.Hour}},
		{"an untapped PR above one the owner holds asks too", clean(func(p *PR) { p.Tap = "" }), on(StateHeld, MoverOwner), Place{StateAwaitingTap, MoverOwner, "clean, green and approved, with no tap on this change", 12 * time.Hour}},
		{"an untapped PR above one in CI is below, not asked", clean(func(p *PR) { p.Tap = "" }), on(StateChecks, MoverChecks), Place{StateBelow, MoverBeneath, "the PR beneath is checks: its own reason", 0}},
		{"a ready PR above a queued one is ready", clean(nil), on(StateQueued, MoverQueue), Place{StateReady, MoverDesk, "every gate passes", 10 * time.Minute}},
		{"a ready PR above a landed one is ready", clean(nil), on(StateLanded, MoverRelease), Place{StateReady, MoverDesk, "every gate passes", 10 * time.Minute}},
		{"a PR above a closed one is its lane's to retarget", clean(nil), on(StateClosed, MoverNobody), Place{StateNeedsFix, MoverLane, "the PR beneath closed without landing", 20 * time.Minute}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := Decide(tc.pr, tc.beneath); got != tc.want {
				t.Fatalf("Decide = %+v, want %+v", got, tc.want)
			}
		})
	}
}

func TestAHeldPRKeepsTheReadyStackAboveItOutOfTheQueue(t *testing.T) {
	bottom := Decide(clean(nil), nil)
	held := Decide(clean(func(p *PR) { p.Hold = Hold{MoverRoot, "readers first"} }), &bottom)
	child := Decide(clean(nil), &held)
	grandchild := Decide(clean(nil), &child)
	if got := []State{bottom.State, held.State, child.State, grandchild.State}; !slices.Equal(got, []State{StateReady, StateHeld, StateBelow, StateBelow}) {
		t.Fatalf("the stack, bottom up, is %v", got)
	}
}
