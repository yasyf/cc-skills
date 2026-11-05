package machine

import (
	"testing"
	"time"
)

const (
	head    = "e718a7434fa01ee9835f992a9a647a825350732c"
	oldHead = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
)

func facts(change func(*Facts)) Facts {
	f := Facts{
		Repo: "org/repo", Number: 45711, Head: head, Base: "dev", Lane: "lane-7",
		CI: CISuccess, Mergeable: MergeableClean, Approved: true,
		Finder: FinderClear, Fidelity: FidelityPass, Tap: TapTapped,
	}
	if change != nil {
		change(&f)
	}
	return f
}

var cases = []struct {
	name  string
	given Facts
	want  Verdict
}{
	{"a green graded head without an approving review asks its lane for one", facts(func(f *Facts) { f.Approved = false }), Verdict{StateGrading, MoverLane, ActionReview, 20 * time.Minute}},
	{"evicted and green again gets one enqueue the same pass", facts(func(f *Facts) { f.Queue, f.Eviction = QueueEvicted, "a batch failed" }), Verdict{StateEvicted, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"evicted with its failure unread stays out of the queue and on the supervisor", facts(func(f *Facts) { f.Queue = QueueEvicted }), Verdict{StateEvicted, MoverSupervisor, ActionWatchQueue, 30 * time.Minute}},
	{"evicted by its own change goes to its lane and stays out of the queue", facts(func(f *Facts) { f.Queue, f.Eviction, f.EvictionOwn = QueueEvicted, "its test failed", true }), Verdict{StateEvicted, MoverLane, ActionFixRed, 20 * time.Minute}},
	{"evicted a second time on a green head is red for its lane", facts(func(f *Facts) { f.Queue, f.Resubmitted = QueueEvicted, 1 }), Verdict{StateRed, MoverLane, ActionFixRed, 20 * time.Minute}},
	{"evicted for its own red check is red for its lane", facts(func(f *Facts) { f.Queue, f.CI = QueueEvicted, CIFailure }), Verdict{StateRed, MoverLane, ActionFixRed, 20 * time.Minute}},
	{"evicted while the tap is missing reads evicted and waits on the owner, not the queue", facts(func(f *Facts) { f.Queue, f.Tap = QueueEvicted, "" }), Verdict{StateEvicted, MoverOwner, ActionTap, 12 * time.Hour}},
	{"landable base with no row and no queue entry is enqueued", facts(nil), Verdict{StateReady, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"landable base without a tap opens the owner's row", facts(func(f *Facts) { f.Tap = "" }), Verdict{StateAwaitingTap, MoverOwner, ActionTap, 12 * time.Hour}},
	{"a withdrawn tap reopens the owner's row", facts(func(f *Facts) { f.Tap = TapWithdrawn }), Verdict{StateAwaitingTap, MoverOwner, ActionTap, 12 * time.Hour}},
	{"a conditional tap waits on the owner like no tap", facts(func(f *Facts) { f.Tap, f.Condition = TapConditional, "only if covered" }), Verdict{StateAwaitingTap, MoverOwner, ActionTap, 12 * time.Hour}},
	{"an exempt change lands without a tap", facts(func(f *Facts) { f.Tap, f.Exempt = "", true }), Verdict{StateReady, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"base in conflict with dev gets a restack order", facts(func(f *Facts) { f.Mergeable = MergeableConflict }), Verdict{StateNeedsRestack, MoverSupervisor, ActionRestack, 10 * time.Minute}},
	{"an untapped conflict is its lane's, never rebased under it", facts(func(f *Facts) { f.Mergeable, f.Tap = MergeableConflict, "" }), Verdict{StateNeedsRestack, MoverLane, ActionRestack, 20 * time.Minute}},
	{"a held conflict is left alone until its hold lifts", facts(func(f *Facts) { f.Mergeable, f.Hold = MergeableConflict, MoverRoot }), Verdict{StateNeedsRestack, MoverLane, ActionRestack, 20 * time.Minute}},
	{"an exempt conflict gets a restack order with no tap", facts(func(f *Facts) { f.Mergeable, f.Tap, f.Exempt = MergeableConflict, "", true }), Verdict{StateNeedsRestack, MoverSupervisor, ActionRestack, 10 * time.Minute}},
	{"base behind dev gets a restack order", facts(func(f *Facts) { f.Mergeable = MergeableBehind }), Verdict{StateNeedsRestack, MoverSupervisor, ActionRestack, 10 * time.Minute}},
	{"a branch carrying other open PRs' commits is restacked, never landed", facts(func(f *Facts) { f.Carries = []int{45501, 45583} }), Verdict{StateNeedsRestack, MoverSupervisor, ActionRestack, 10 * time.Minute}},
	{"a bot finding with no verdict is the lane's with a 20-minute clock", facts(func(f *Facts) { f.Finder = FinderAsk }), Verdict{StateNeedsVerdict, MoverLane, ActionVerdict, 20 * time.Minute}},
	{"a failed plan check with no verdict is the lane's", facts(func(f *Facts) { f.Fidelity = FidelityFail }), Verdict{StateNeedsVerdict, MoverLane, ActionVerdict, 20 * time.Minute}},
	{"a finder override clears the finding", facts(func(f *Facts) { f.Finder, f.FinderOverride = FinderAsk, true }), Verdict{StateReady, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"red CI is the lane's to fix", facts(func(f *Facts) { f.CI = CIFailure }), Verdict{StateRed, MoverLane, ActionFixRed, 20 * time.Minute}},
	{"a fresh head with nothing graded is pushed and goes to the pool", facts(func(f *Facts) { f.CI, f.Finder, f.Fidelity = CIPending, "", "" }), Verdict{StatePushed, MoverPool, ActionGrade, 10 * time.Minute}},
	{"a head missing one grade is grading on the pool", facts(func(f *Facts) { f.Fidelity = "" }), Verdict{StateGrading, MoverPool, ActionGrade, 10 * time.Minute}},
	{"a head no pass will grade leaves the pool for its lane", facts(func(f *Facts) { f.Fidelity, f.GradeStopped = "", "reader" }), Verdict{StateNeedsVerdict, MoverLane, ActionVerdict, 20 * time.Minute}},
	{"a grade posted after the passes stopped clears the head", facts(func(f *Facts) { f.GradeStopped = "reader" }), Verdict{StateReady, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"graded and cleared but CI still running waits on CI, never on the owner", facts(func(f *Facts) { f.CI = CIPending }), Verdict{StateGrading, MoverSupervisor, ActionWaitCI, 120 * time.Minute}},
	{"mergeability not yet computed is not ready", facts(func(f *Facts) { f.Mergeable = MergeableUnknown }), Verdict{StateGrading, MoverSupervisor, ActionWaitCI, 120 * time.Minute}},
	{"a root hold on an untapped head waits on the owner's clock", facts(func(f *Facts) { f.Hold, f.Tap = MoverRoot, "" }), Verdict{StateAwaitingTap, MoverRoot, ActionLift, 12 * time.Hour}},
	{"a root hold on a tapped head is root's to decide within the hour", facts(func(f *Facts) { f.Hold = MoverRoot }), Verdict{StateHeld, MoverRoot, ActionLift, time.Hour}},
	{"an owner hold on a tapped head is root's to decide too", facts(func(f *Facts) { f.Hold = MoverOwner }), Verdict{StateHeld, MoverRoot, ActionLift, time.Hour}},
	{"queued on this head is watched for thirty minutes", facts(func(f *Facts) { f.Queue, f.QueuedHead = QueueQueued, head }), Verdict{StateQueued, MoverSupervisor, ActionWatchQueue, 30 * time.Minute}},
	{"queued on an older head is dequeued the same pass", facts(func(f *Facts) { f.Queue, f.QueuedHead = QueueQueued, oldHead }), Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, 10 * time.Minute}},
	{"queued under an open hold is dequeued the same pass", facts(func(f *Facts) { f.Queue, f.QueuedHead, f.Hold = QueueQueued, head, MoverRoot }), Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, 10 * time.Minute}},
	{"queued with no tap at its head is dequeued the same pass", facts(func(f *Facts) { f.Queue, f.QueuedHead, f.Tap = QueueQueued, head, "" }), Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, 10 * time.Minute}},
	{"queued with no lane and no tap is another session's, and is left alone", facts(func(f *Facts) { f.Queue, f.QueuedHead, f.Tap, f.Lane = QueueQueued, head, "", "" }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"queued on a withdrawn tap is dequeued the same pass", facts(func(f *Facts) { f.Queue, f.QueuedHead, f.Tap = QueueQueued, head, TapWithdrawn }), Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, 10 * time.Minute}},
	{"queued on a conditional tap is dequeued the same pass", facts(func(f *Facts) {
		f.Queue, f.QueuedHead, f.Tap, f.Condition = QueueQueued, head, TapConditional, "only if covered"
	}), Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, 10 * time.Minute}},
	{"queued exempt with no tap stays in the queue", facts(func(f *Facts) { f.Queue, f.QueuedHead, f.Tap, f.Exempt = QueueQueued, head, "", true }), Verdict{StateQueued, MoverSupervisor, ActionWatchQueue, 30 * time.Minute}},
	{"an ungraded PR no lane owns and nobody tapped is nobody's", facts(func(f *Facts) { f.Lane, f.Finder, f.Fidelity, f.Tap = "", "", "", "" }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"a graded PR no lane owns never asks the owner for a tap", facts(func(f *Facts) { f.Lane, f.Tap = "", "" }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"a tapped PR with no lane is still graded", facts(func(f *Facts) { f.Lane, f.Finder, f.Fidelity, f.Tap = "", "", "", TapTapped }), Verdict{StateGrading, MoverPool, ActionGrade, GradeClock}},
	{"a conflicting PR no lane owns and nobody tapped is never restacked", facts(func(f *Facts) { f.Lane, f.Tap, f.Mergeable = "", "", MergeableConflict }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"a red PR no lane owns and nobody tapped is no lane's to fix", facts(func(f *Facts) { f.Lane, f.Tap, f.CI = "", "", CIFailure }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"another person's PR a lane carries never asks the owner for a tap", facts(func(f *Facts) { f.ForeignAuthor, f.Tap = "teammate", "" }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"another person's PR a lane carries is never graded", facts(func(f *Facts) { f.ForeignAuthor, f.Tap, f.Finder, f.Fidelity = "teammate", "", "", "" }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"another person's queued PR a lane carries is never dequeued", facts(func(f *Facts) { f.ForeignAuthor, f.Tap, f.Queue, f.QueuedHead = "teammate", "", QueueQueued, head }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"another person's conflicting PR a lane carries is never restacked", facts(func(f *Facts) { f.ForeignAuthor, f.Tap, f.Mergeable = "teammate", "", MergeableConflict }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"another person's red PR a lane carries is no lane's to fix", facts(func(f *Facts) { f.ForeignAuthor, f.Tap, f.CI = "teammate", "", CIFailure }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"another person's PR the owner tapped lands like the drive's own", facts(func(f *Facts) { f.ForeignAuthor = "teammate" }), Verdict{StateReady, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"a draft is nobody's whatever its lane and tap", facts(func(f *Facts) { f.Draft, f.Tap = true, TapTapped }), Verdict{StatePushed, MoverNobody, ActionNone, 0}},
	{"a tapped PR whose row lost readiness holds and its lane owns the wait", facts(func(f *Facts) { f.RowWait = "not graded" }), Verdict{StateNotReady, MoverLane, ActionRowWait, LaneClock}},
	{"an untapped PR whose row is not ready is not put to the owner", facts(func(f *Facts) { f.RowWait, f.Tap = "its PR body has no Summary", "" }), Verdict{StateNotReady, MoverLane, ActionRowWait, LaneClock}},
	{"an exempt PR lands under its own rule whatever its row says", facts(func(f *Facts) { f.RowWait, f.Tap, f.Exempt = "its PR body has no Summary", "", true }), Verdict{StateReady, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"a hold his tap ends opens his row, untapped", facts(func(f *Facts) { f.Hold, f.HoldAwaitsTap, f.Tap = MoverRoot, true, "" }), Verdict{StateAwaitingTap, MoverOwner, ActionTap, OwnerClock}},
	{"a hold his tap ends opens his row over a tap from before it", facts(func(f *Facts) { f.Hold, f.HoldAwaitsTap = MoverRoot, true }), Verdict{StateAwaitingTap, MoverOwner, ActionTap, OwnerClock}},
	{"a hold his tap ends yields to a row not ready", facts(func(f *Facts) { f.Hold, f.HoldAwaitsTap, f.RowWait = MoverRoot, true, "CI is red at this head" }), Verdict{StateNotReady, MoverLane, ActionRowWait, LaneClock}},
	{"a hold wins over a row not ready", facts(func(f *Facts) { f.RowWait, f.Hold = "held by root", MoverRoot }), Verdict{StateHeld, MoverRoot, ActionLift, RootClock}},
	{"a queued PR with its tap at its head stays in the queue while its row flaps", facts(func(f *Facts) { f.Queue, f.QueuedHead, f.RowWait = QueueQueued, head, "CI is not green" }), Verdict{StateQueued, MoverSupervisor, ActionWatchQueue, 30 * time.Minute}},
	{"merged is landed and nobody's", facts(func(f *Facts) { f.Merged, f.Closed = true, true }), Verdict{StateLanded, MoverNobody, ActionNone, 0}},
	{"closed without a merge is closed and nobody's", facts(func(f *Facts) { f.Closed = true }), Verdict{StateClosed, MoverNobody, ActionNone, 0}},
}

func TestOnlyAPRALaneOfThisDriveOpenedOrTheOwnerTappedIsOurs(t *testing.T) {
	for _, c := range []struct {
		name          string
		given         Facts
		ours, foreign bool
	}{
		{"a lane of this drive opened it", facts(func(f *Facts) { f.Tap = "" }), true, false},
		{"no lane of this drive owns it", facts(func(f *Facts) { f.Lane, f.Tap = "", "" }), false, false},
		{"no lane owns it and the owner tapped it", facts(func(f *Facts) { f.Lane = "" }), true, false},
		{"another person wrote it and a lane carries it", facts(func(f *Facts) { f.ForeignAuthor, f.Tap = "teammate", "" }), false, true},
		{"another person wrote it and the owner's tap is conditional", facts(func(f *Facts) { f.ForeignAuthor, f.Tap, f.Condition = "teammate", TapConditional, "only if covered" }), false, true},
		{"another person wrote it and the owner withdrew his tap", facts(func(f *Facts) { f.ForeignAuthor, f.Tap = "teammate", TapWithdrawn }), false, true},
		{"another person wrote it and the owner tapped it", facts(func(f *Facts) { f.ForeignAuthor = "teammate" }), true, false},
		{"another person wrote it and no lane owns it", facts(func(f *Facts) { f.ForeignAuthor, f.Lane, f.Tap = "teammate", "", "" }), false, true},
	} {
		t.Run(c.name, func(t *testing.T) {
			if ours, foreign := c.given.Ours(), c.given.Foreign(); ours != c.ours || foreign != c.foreign {
				t.Fatalf("Ours() = %v, Foreign() = %v, want %v, %v", ours, foreign, c.ours, c.foreign)
			}
		})
	}
}

func TestEveryWaitHasOneMoverAndADeadline(t *testing.T) {
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := Classify(c.given); got != c.want {
				t.Fatalf("got %+v, want %+v", got, c.want)
			}
		})
	}
}

func TestAWaitThatOpensAnItemIsBounded(t *testing.T) {
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			verdict := Classify(c.given)
			if !verdict.OpensItem() {
				if !verdict.State.Terminal() && !c.given.Undriven() {
					t.Fatalf("%s opens no item and is not terminal", verdict.State)
				}
				return
			}
			if verdict.Mover == MoverNobody || verdict.Deadline <= 0 {
				t.Fatalf("an item with mover %s and deadline %s", verdict.Mover, verdict.Deadline)
			}
		})
	}
}

func TestEveryStateIsReachable(t *testing.T) {
	reached := map[State]bool{}
	for _, c := range cases {
		reached[Classify(c.given).State] = true
	}
	for _, state := range States {
		if !reached[state] {
			t.Errorf("no case reaches %s", state)
		}
	}
}

func TestALanesZeroFactsAreAnOpenUngradedPushedHead(t *testing.T) {
	want := Verdict{StatePushed, MoverPool, ActionGrade, 10 * time.Minute}
	if got := Classify(Facts{Lane: "lane-7"}); got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestLandableReadsEveryGate(t *testing.T) {
	if !facts(nil).Landable() {
		t.Fatal("the base case must land")
	}
	broken := map[string]func(*Facts){
		"ci":       func(f *Facts) { f.CI = CIPending },
		"conflict": func(f *Facts) { f.Mergeable = MergeableConflict },
		"finder":   func(f *Facts) { f.Finder = "" },
		"fidelity": func(f *Facts) { f.Fidelity = FidelityFail },
		"tap":      func(f *Facts) { f.Tap = "" },
		"hold":     func(f *Facts) { f.Hold = MoverOwner },
		"tap hold": func(f *Facts) { f.Hold, f.HoldAwaitsTap = MoverRoot, true },
		"carries":  func(f *Facts) { f.Carries = []int{1} },
		"approval": func(f *Facts) { f.Approved = false },
		"row":      func(f *Facts) { f.RowWait = "not graded" },
	}
	for name, change := range broken {
		if facts(change).Landable() {
			t.Errorf("%s broken must not land", name)
		}
	}
}

func TestAnOverrideCountsAsItsOwnGradersGradeWhetherOrNotThisHostLearnedItStopped(t *testing.T) {
	for name, stored := range map[string]map[string]string{
		"stopped": {"finder": "clear", GradeStoppedFact: "fidelity: its pass ended without a grade", OverrideFact("fidelity"): "accept"},
		"no stop": {"finder": "clear", OverrideFact("fidelity"): "accept"},
	} {
		if finder, fidelity := GradesOf(stored); finder != FinderClear || fidelity != FidelityPass {
			t.Errorf("%s: overridden read %q and %q", name, finder, fidelity)
		}
	}
	for name, stored := range map[string]map[string]string{
		"no override":          {"finder": "clear", GradeStoppedFact: "fidelity"},
		"the other's override": {"finder": "clear", GradeStoppedFact: "fidelity", OverrideFact("finder"): "accept"},
	} {
		if _, fidelity := GradesOf(stored); fidelity != "" {
			t.Errorf("%s: fidelity reads %q", name, fidelity)
		}
	}
	if _, fidelity := GradesOf(map[string]string{"fidelity": "fail", GradeStoppedFact: "fidelity", OverrideFact("fidelity"): "accept"}); fidelity != FidelityFail {
		t.Errorf("an override rewrote a failing grade to %q", fidelity)
	}
}

func TestEachGraderClearsOnlyOnItsOwnPassOrItsOwnOverride(t *testing.T) {
	for name, test := range map[string]struct {
		edit func(*Facts)
		want bool
	}{
		"a finder override leaves a failed fidelity failed": {func(f *Facts) { f.Finder, f.Fidelity, f.FinderOverride = FinderAsk, FidelityFail, true }, false},
		"a fidelity override leaves an open finder open":    {func(f *Facts) { f.Finder, f.Fidelity, f.FidelityOverride = FinderAsk, FidelityFail, true }, false},
		"a finder override with a clean fidelity clears":    {func(f *Facts) { f.Finder, f.FinderOverride = FinderAsk, true }, true},
		"a fidelity override with a clean finder clears":    {func(f *Facts) { f.Fidelity, f.FidelityOverride = FidelityFail, true }, true},
		"both overrides clear": {func(f *Facts) {
			f.Finder, f.Fidelity, f.FinderOverride, f.FidelityOverride = FinderAsk, FidelityFail, true, true
		}, true},
		"a fidelity override does not answer an open finder": {func(f *Facts) { f.Finder, f.FidelityOverride = FinderAsk, true }, false},
	} {
		t.Run(name, func(t *testing.T) {
			if got := facts(test.edit).Cleared(); got != test.want {
				t.Fatalf("Cleared() = %v, want %v", got, test.want)
			}
		})
	}
}

func TestAGoNamedHeadWhoseOnlyFidelityIsAnOverrideGetsItsEnqueue(t *testing.T) {
	f := facts(func(f *Facts) { f.Tap, f.Exempt, f.Fidelity = "", true, "" })
	if got := Classify(f); got.Action != ActionGrade {
		t.Fatalf("with no grade and no override it reads %+v, want a grade item", got)
	}

	f.Finder, f.Fidelity = GradesOf(map[string]string{"finder": "clear", OverrideFact("fidelity"): "439107"})
	f.FinderOverride, f.FidelityOverride = OverridesOf(map[string]string{OverrideFact("fidelity"): "439107"})

	if got := Classify(f); got.Action != ActionEnqueue {
		t.Fatalf("with the override it reads %+v, want an enqueue", got)
	}
}
