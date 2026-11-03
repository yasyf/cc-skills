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
	{"evicted and green again gets one enqueue the same pass", facts(func(f *Facts) { f.Queue = QueueEvicted }), Verdict{StateEvicted, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"evicted a second time on a green head is red for its lane", facts(func(f *Facts) { f.Queue, f.Resubmitted = QueueEvicted, 1 }), Verdict{StateRed, MoverLane, ActionFixRed, 20 * time.Minute}},
	{"evicted for its own red check is red for its lane", facts(func(f *Facts) { f.Queue, f.CI = QueueEvicted, CIFailure }), Verdict{StateRed, MoverLane, ActionFixRed, 20 * time.Minute}},
	{"evicted while the tap is missing waits on the owner, not the queue", facts(func(f *Facts) { f.Queue, f.Tap = QueueEvicted, "" }), Verdict{StateAwaitingTap, MoverOwner, ActionTap, 12 * time.Hour}},
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
	{"a verdict on the finding clears it", facts(func(f *Facts) { f.Finder, f.Verdict = FinderAsk, true }), Verdict{StateReady, MoverSupervisor, ActionEnqueue, 10 * time.Minute}},
	{"red CI is the lane's to fix", facts(func(f *Facts) { f.CI = CIFailure }), Verdict{StateRed, MoverLane, ActionFixRed, 20 * time.Minute}},
	{"a fresh head with nothing graded is pushed and goes to the pool", facts(func(f *Facts) { f.CI, f.Finder, f.Fidelity = CIPending, "", "" }), Verdict{StatePushed, MoverPool, ActionGrade, 10 * time.Minute}},
	{"a head missing one grade is grading on the pool", facts(func(f *Facts) { f.Fidelity = "" }), Verdict{StateGrading, MoverPool, ActionGrade, 10 * time.Minute}},
	{"graded and cleared but CI still running waits on CI, never on the owner", facts(func(f *Facts) { f.CI = CIPending }), Verdict{StateGrading, MoverSupervisor, ActionWaitCI, 120 * time.Minute}},
	{"mergeability not yet computed is not ready", facts(func(f *Facts) { f.Mergeable = MergeableUnknown }), Verdict{StateGrading, MoverSupervisor, ActionWaitCI, 120 * time.Minute}},
	{"a root hold is root's to lift", facts(func(f *Facts) { f.Hold = MoverRoot }), Verdict{StateAwaitingTap, MoverRoot, ActionLift, 12 * time.Hour}},
	{"queued on this head is watched for thirty minutes", facts(func(f *Facts) { f.Queue, f.QueuedHead = QueueQueued, head }), Verdict{StateQueued, MoverSupervisor, ActionWatchQueue, 30 * time.Minute}},
	{"queued on an older head is dequeued the same pass", facts(func(f *Facts) { f.Queue, f.QueuedHead = QueueQueued, oldHead }), Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, 10 * time.Minute}},
	{"merged is landed and nobody's", facts(func(f *Facts) { f.Merged, f.Closed = true, true }), Verdict{StateLanded, MoverNobody, ActionNone, 0}},
	{"closed without a merge is closed and nobody's", facts(func(f *Facts) { f.Closed = true }), Verdict{StateClosed, MoverNobody, ActionNone, 0}},
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
				if !verdict.State.Terminal() {
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

func TestZeroFactsAreAnOpenUngradedPushedHead(t *testing.T) {
	want := Verdict{StatePushed, MoverPool, ActionGrade, 10 * time.Minute}
	if got := Classify(Facts{}); got != want {
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
		"carries":  func(f *Facts) { f.Carries = []int{1} },
		"approval": func(f *Facts) { f.Approved = false },
	}
	for name, change := range broken {
		if facts(change).Landable() {
			t.Errorf("%s broken must not land", name)
		}
	}
}
