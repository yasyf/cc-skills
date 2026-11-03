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
	StateReady        State = "ready"
	StateQueued       State = "queued"
	StateEvicted      State = "evicted"
	StateLanded       State = "landed"
	StateClosed       State = "closed"
)

var States = []State{
	StatePushed, StateGrading, StateNeedsVerdict, StateNeedsRestack, StateRed, StateAwaitingTap,
	StateReady, StateQueued, StateEvicted, StateLanded, StateClosed,
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

const (
	SupervisorClock = 10 * time.Minute
	GradeClock      = 10 * time.Minute
	LaneClock       = 20 * time.Minute
	QueueClock      = 30 * time.Minute
	CIClock         = 120 * time.Minute
	OwnerClock      = 12 * time.Hour
)

type Facts struct {
	Repo        string
	Number      int
	Head        string
	Base        string
	Lane        string
	Closed      bool
	Merged      bool
	CI          CI
	Approved    bool
	Mergeable   Mergeable
	Queue       Queue
	QueuedHead  string
	Resubmitted int
	Finder      Finder
	Fidelity    Fidelity
	Verdict     bool
	Tap         Tap
	Exempt      bool
	Hold        Mover
	Carries     []int
	Condition   string
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

func (f Facts) Tapped() bool {
	return f.Tap == TapTapped || f.Exempt
}

func (f Facts) Restackable() bool {
	return slices.Contains([]Mergeable{MergeableConflict, MergeableBehind}, f.Mergeable) || len(f.Carries) > 0
}

func (f Facts) Landable() bool {
	return f.Green() && f.Mergeable == MergeableClean && len(f.Carries) == 0 && f.Cleared() && f.Approved && f.Tapped() && f.Hold == ""
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
		if f.QueuedHead != f.Head {
			return Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, SupervisorClock}
		}
		return Verdict{StateQueued, MoverSupervisor, ActionWatchQueue, QueueClock}
	}
	if f.Restackable() {
		return Verdict{StateNeedsRestack, MoverSupervisor, ActionRestack, SupervisorClock}
	}
	if !f.Graded() {
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
	if f.Hold != "" {
		return Verdict{StateAwaitingTap, f.Hold, ActionLift, OwnerClock}
	}
	if !f.Tapped() {
		return Verdict{StateAwaitingTap, MoverOwner, ActionTap, OwnerClock}
	}
	return Verdict{StateReady, MoverSupervisor, ActionEnqueue, SupervisorClock}
}
