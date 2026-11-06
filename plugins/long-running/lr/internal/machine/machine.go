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

const (
	GradeStoppedFact  = "grade-stopped"
	FidelityWaitsFact = "fidelity-waits"
	EvictionFact      = "eviction"
	EvictionOwnFact   = "eviction-own"
)

func OverrideFact(grader string) string {
	return "override-" + grader
}

func WithdrawnOverrideFact(grader string) string {
	return "withdrawn-" + OverrideFact(grader)
}

func OverrideWithdrawn(stored map[string]string, grader, value string) bool {
	return value != "" && stored[WithdrawnOverrideFact(grader)] == value
}

func OverridesOf(stored map[string]string) (finder, fidelity bool) {
	return stored[OverrideFact("finder")] != "", stored[OverrideFact("fidelity")] != ""
}

func GradesOf(stored map[string]string) (Finder, Fidelity) {
	finder, fidelity := Finder(stored["finder"]), Fidelity(stored["fidelity"])
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
	Repo             string
	Number           int
	Title            string
	Head             string
	Branch           string
	Base             string
	Lane             string
	ForeignAuthor    string
	Draft            bool
	Closed           bool
	Merged           bool
	MergedBy         string
	CI               CI
	Failing          []string
	Approved         bool
	Approvers        []string
	Mergeable        Mergeable
	Uncomputed       bool
	Queue            Queue
	QueuedHead       string
	PersonQueued     bool
	Resubmitted      int
	Eviction         string
	EvictionOwn      bool
	Finder           Finder
	Fidelity         Fidelity
	FinderOverride   bool
	FidelityOverride bool
	Tap              Tap
	TapFrom          string
	CarriedBy        string
	TapReturned      string
	Exempt           bool
	StandingWord     string
	Hold             Mover
	HoldRecord       string
	HoldAwaitsTap    bool
	Carries          []int
	Condition        string
	UnsafeWait       string
	Body             string
	OldTree          string
	NextTree         string
	Port             bool
	GradeStopped     string
	RowWait          string
	TapRefused       string
	Stuck            string
}

func (f Facts) Green() bool {
	return f.CI == CISuccess
}

func (f Facts) Graded() bool {
	return f.Finder != "" && f.Fidelity != ""
}

func (f Facts) Cleared() bool {
	return f.Graded() && f.FinderCleared() && f.FidelityCleared()
}

func (f Facts) FinderCleared() bool {
	return f.Finder == FinderClear || f.FinderOverride
}

func (f Facts) FidelityCleared() bool {
	return f.Fidelity == FidelityPass || f.FidelityOverride
}

func (f Facts) Ours() bool {
	return f.Admitted() || f.Lane != "" && f.ForeignAuthor == ""
}

func (f Facts) Foreign() bool {
	return f.ForeignAuthor != "" && !f.Ours()
}

func (f Facts) Undriven() bool {
	return f.Draft || !f.Ours()
}

func (f Facts) Held() bool {
	return f.Hold != "" && !f.HoldAwaitsTap
}

func (f Facts) Admitted() bool {
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
	return f.Landable() || (f.Queued() && f.Hold == "" && f.Admitted())
}

func (f Facts) Landable() bool {
	return f.Green() && f.Mergeable == MergeableClean && len(f.Carries) == 0 && f.Cleared() && f.Approved && f.Admitted() && f.Hold == "" && f.UnsafeWait == "" && f.RowClear()
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
	verdict := classify(f)
	if f.Queue == QueueEvicted && verdict.OpensItem() && verdict.State != StateRed {
		verdict.State = StateEvicted
	}
	return verdict
}

func classify(f Facts) Verdict {
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
	if f.Queue == QueueEvicted && f.EvictionOwn {
		return Verdict{StateEvicted, MoverLane, ActionFixRed, LaneClock}
	}
	if f.Queue == QueueEvicted && f.Landable() {
		switch {
		case f.Resubmitted >= 1:
			return Verdict{StateRed, MoverLane, ActionFixRed, LaneClock}
		case f.Eviction == "":
			return Verdict{StateEvicted, MoverSupervisor, ActionWatchQueue, QueueClock}
		}
		return Verdict{StateEvicted, MoverSupervisor, ActionEnqueue, SupervisorClock}
	}
	if f.Queue == QueueQueued {
		if f.Undriven() {
			return Verdict{StatePushed, MoverNobody, ActionNone, 0}
		}
		if f.QueuedHead != f.Head || f.Hold != "" || len(f.Carries) > 0 || !f.Admitted() && !f.PersonQueued {
			return Verdict{StateAwaitingTap, MoverSupervisor, ActionDequeue, SupervisorClock}
		}
		return Verdict{StateQueued, MoverSupervisor, ActionWatchQueue, QueueClock}
	}
	if f.Restackable() {
		if (f.Admitted() || f.Port && !f.Draft) && f.Hold == "" && len(f.Carries) == 0 {
			return Verdict{StateNeedsRestack, MoverSupervisor, ActionRestack, SupervisorClock}
		}
		return Verdict{StateNeedsRestack, MoverLane, ActionRestack, LaneClock}
	}
	if f.Undriven() {
		return Verdict{StatePushed, MoverNobody, ActionNone, 0}
	}
	if !f.Graded() {
		if f.Held() {
			return heldVerdict(f)
		}
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
	if f.Held() {
		return heldVerdict(f)
	}
	if !f.RowClear() {
		return Verdict{StateNotReady, MoverLane, ActionRowWait, LaneClock}
	}
	if !f.Admitted() || f.HoldAwaitsTap {
		return Verdict{StateAwaitingTap, MoverOwner, ActionTap, OwnerClock}
	}
	return Verdict{StateReady, MoverSupervisor, ActionEnqueue, SupervisorClock}
}

func heldVerdict(f Facts) Verdict {
	if f.Tap == TapTapped {
		return Verdict{StateHeld, MoverRoot, ActionLift, RootClock}
	}
	return Verdict{StateAwaitingTap, f.Hold, ActionLift, OwnerClock}
}
