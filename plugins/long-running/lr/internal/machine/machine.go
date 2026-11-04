package machine

import (
	"slices"
	"time"
)

type State string

const (
	StatePushed       State = "pushed"
	StateGrading      State = "grading"
	StateNeedsVerdict State = "needs-verdict"
	StateNeedsRestack State = "needs-restack"
	StateRed          State = "red"
	StateAwaitingTap  State = "awaiting-tap"
	StateHeld         State = "held"
	StateNotReady     State = "not-ready"
	StateReady        State = "ready"
	StateQueued       State = "queued"
	StateEvicted      State = "evicted"
	StateLanded       State = "landed"
	StateClosed       State = "closed"
)

var States = []State{
	StatePushed, StateGrading, StateNeedsVerdict, StateNeedsRestack, StateRed, StateAwaitingTap,
	StateHeld, StateNotReady, StateReady, StateQueued, StateEvicted, StateLanded, StateClosed,
}

func (s State) Terminal() bool {
	return s == StateLanded || s == StateClosed
}

type Mover string

const (
	MoverSupervisor Mover = "supervisor"
	MoverPool       Mover = "pool"
	MoverLane       Mover = "lane"
	MoverOwner      Mover = "owner"
	MoverRoot       Mover = "root"
	MoverNobody     Mover = "nobody"
)

type Action string

const (
	ActionGrade      Action = "grade"
	ActionWaitCI     Action = "wait-ci"
	ActionReview     Action = "review"
	ActionRowWait    Action = "row-wait"
	ActionRestack    Action = "restack"
	ActionVerdict    Action = "verdict"
	ActionFixRed     Action = "fix-red"
	ActionTap        Action = "tap"
	ActionLift       Action = "lift"
	ActionEnqueue    Action = "enqueue"
	ActionDequeue    Action = "dequeue"
	ActionWatchQueue Action = "watch-queue"
	ActionNone       Action = "none"
)

type CI string

const (
	CIPending CI = "pending"
	CISuccess CI = "success"
	CIFailure CI = "failure"
)

type Mergeable string

const (
	MergeableUnknown  Mergeable = "unknown"
	MergeableClean    Mergeable = "clean"
	MergeableConflict Mergeable = "conflict"
	MergeableBehind   Mergeable = "behind"
)

type Queue string

const (
	QueueNone    Queue = "none"
	QueueQueued  Queue = "queued"
	QueueEvicted Queue = "evicted"
)

type Finder string

const (
	FinderClear Finder = "clear"
	FinderAsk   Finder = "ask"
)

type Fidelity string

const (
	FidelityPass Fidelity = "pass"
	FidelityFail Fidelity = "fail"
)

type Tap string

const (
	TapTapped      Tap = "tapped"
	TapWithdrawn   Tap = "withdrawn"
	TapConditional Tap = "conditional"
)

const GradeStoppedFact = "grade-stopped"

func OverrideFact(grader string) string {
	return "override-" + grader
}

func GradesOf(stored map[string]string) (Finder, Fidelity) {
	finder, fidelity := Finder(stored["finder"]), Fidelity(stored["fidelity"])
	if stored[GradeStoppedFact] == "" {
		return finder, fidelity
	}
	if finder == "" && stored[OverrideFact("finder")] != "" {
		finder = FinderClear
	}
	if fidelity == "" && stored[OverrideFact("fidelity")] != "" {
		fidelity = FidelityPass
	}
	return finder, fidelity
}

const (
	SupervisorClock = 10 * time.Minute
	GradeClock      = 10 * time.Minute
	LaneClock       = 20 * time.Minute
	QueueClock      = 30 * time.Minute
	CIClock         = 120 * time.Minute
	RootClock       = time.Hour
	OwnerClock      = 12 * time.Hour
)

type Facts struct {
	Repo          string
	Number        int
	Title         string
	Head          string
	Branch        string
	Base          string
	Lane          string
	ForeignAuthor string
	Draft         bool
	Closed        bool
	Merged        bool
	CI            CI
	Failing       []string
	Approved      bool
	Approvers     []string
	Mergeable     Mergeable
	Uncomputed    bool
	Queue         Queue
	QueuedHead    string
	Resubmitted   int
	Finder        Finder
	Fidelity      Fidelity
	Verdict       bool
	Tap           Tap
	TapFrom       string
	CarriedBy     string
	TapReturned   string
	Exempt        bool
	Hold          Mover
	HoldSeq       int64
	Carries       []int
	Condition     string
	UnsafeWait    string
	Body          string
	GradeStopped  string
	RowWait       string
	TapRefused    string
}

func (f Facts) Green() bool {
	return f.CI == CISuccess
}

func (f Facts) Graded() bool {
	return f.Finder != "" && f.Fidelity != ""
}

func (f Facts) Cleared() bool {
	return f.Graded() && ((f.Finder == FinderClear && f.Fidelity == FidelityPass) || f.Verdict)
}

func (f Facts) Ours() bool {
	return f.Tapped() || f.Lane != "" && f.ForeignAuthor == ""
}

func (f Facts) Foreign() bool {
	return f.ForeignAuthor != "" && !f.Ours()
}

func (f Facts) Undriven() bool {
	return f.Draft || !f.Ours()
}

func (f Facts) Tapped() bool {
	return f.Tap == TapTapped || f.Exempt
}

func (f Facts) RowClear() bool {
	return f.Exempt || f.RowWait == ""
}

func (f Facts) Restackable() bool {
	return slices.Contains([]Mergeable{MergeableConflict, MergeableBehind}, f.Mergeable) || len(f.Carries) > 0
}

func (f Facts) Queued() bool {
	return f.Queue == QueueQueued && f.QueuedHead == f.Head
}

func (f Facts) ClearsAbove() bool {
	return f.Landable() || (f.Queued() && f.Hold == "" && f.Tapped())
}

func (f Facts) Landable() bool {
	return f.Green() && f.Mergeable == MergeableClean && len(f.Carries) == 0 && f.Cleared() && f.Approved && f.Tapped() && f.Hold == "" && f.UnsafeWait == "" && f.RowClear()
}

type Verdict struct {
	State    State
	Mover    Mover
	Action   Action
	Deadline time.Duration
}

func (v Verdict) OpensItem() bool {
	return v.Action != ActionNone
}

func Classify(f Facts) Verdict {
	if f.Merged {
		return Verdict{StateLanded, MoverNobody, ActionNone, 0}
	}
	if f.Closed {
		return Verdict{StateClosed, MoverNobody, ActionNone, 0}
	}
	if !f.Ours() {
		return Verdict{StatePushed, MoverNobody, ActionNone, 0}
	}
	if f.CI == CIFailure {
		return Verdict{StateRed, MoverLane, ActionFixRed, LaneClock}
	}
	if f.Queue == QueueEvicted && f.Landable() {
		if f.Resubmitted >= 1 {
			return Verdict{StateRed, MoverLane, ActionFixRed, LaneClock}
		}
		return Verdict{StateEvicted, MoverSupervisor, ActionEnqueue, SupervisorClock}
	}
	if f.Queue == QueueQueued {
		if f.Undriven() {
			return Verdict{StatePushed, MoverNobody, ActionNone, 0}
		}
		if f.QueuedHead != f.Head || f.Hold != "" || !f.Tapped() {
			return Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, SupervisorClock}
		}
		return Verdict{StateQueued, MoverSupervisor, ActionWatchQueue, QueueClock}
	}
	if f.Restackable() {
		if f.Tapped() && f.Hold == "" {
			return Verdict{StateNeedsRestack, MoverSupervisor, ActionRestack, SupervisorClock}
		}
		return Verdict{StateNeedsRestack, MoverLane, ActionRestack, LaneClock}
	}
	if f.Undriven() {
		return Verdict{StatePushed, MoverNobody, ActionNone, 0}
	}
	if !f.Graded() {
		if f.GradeStopped != "" {
			return Verdict{StateNeedsVerdict, MoverLane, ActionVerdict, LaneClock}
		}
		state := StateGrading
		if f.Finder == "" && f.Fidelity == "" && !f.Green() {
			state = StatePushed
		}
		return Verdict{state, MoverPool, ActionGrade, GradeClock}
	}
	if !f.Cleared() {
		return Verdict{StateNeedsVerdict, MoverLane, ActionVerdict, LaneClock}
	}
	if !f.Green() || f.Mergeable != MergeableClean {
		return Verdict{StateGrading, MoverSupervisor, ActionWaitCI, CIClock}
	}
	if !f.Approved {
		return Verdict{StateGrading, MoverLane, ActionReview, LaneClock}
	}
	if f.Hold != "" && f.Tap == TapTapped {
		return Verdict{StateHeld, MoverRoot, ActionLift, RootClock}
	}
	if f.Hold != "" {
		return Verdict{StateAwaitingTap, f.Hold, ActionLift, OwnerClock}
	}
	if !f.RowClear() {
		return Verdict{StateNotReady, MoverLane, ActionRowWait, LaneClock}
	}
	if !f.Tapped() {
		return Verdict{StateAwaitingTap, MoverOwner, ActionTap, OwnerClock}
	}
	return Verdict{StateReady, MoverSupervisor, ActionEnqueue, SupervisorClock}
}
