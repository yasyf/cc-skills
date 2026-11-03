package machine

import (
	"slices"
	"strings"
	"time"
)

const (
	StateNeedsFix State = "needs-fix"
	StateAppeal   State = "appeal"
	StateChecks   State = "checks"
	StateBelow    State = "below"
	StateRejected State = "rejected"
	StateReleased State = "released"
	StateLive     State = "live"
)

const (
	MoverAppeals Mover = "appeals"
	MoverChecks  Mover = "checks"
	MoverBeneath Mover = "beneath"
	MoverDesk    Mover = "desk"
	MoverQueue   Mover = "queue"
	MoverRelease Mover = "release"
)

const TapRejected Tap = "rejected"

const AppealClock = 15 * time.Minute

type Finding string

const (
	FindingOpen     Finding = "open"
	FindingUpheld   Finding = "upheld"
	FindingConflict Finding = "conflict"
)

type Review string

const (
	ReviewApproved Review = "approved"
	ReviewChanges  Review = "changes"
)

type Eviction string

const (
	EvictionConflict Eviction = "conflict"
	EvictionOwnRed   Eviction = "own-red"
	EvictionBatchRed Eviction = "batch-red"
	EvictionFlaky    Eviction = "flaky"
	EvictionOurs     Eviction = "ours"
)

type Hold struct {
	Lifter Mover
	Reason string
}

type PR struct {
	Closed      bool
	Merged      bool
	Landed      bool
	Released    bool
	Live        bool
	Queued      bool
	Eviction    Eviction
	Reentered   bool
	Graded      bool
	Finding     Finding
	Checks      CI
	Failing     []string
	Review      Review
	Unmergeable bool
	Hold        Hold
	Tap         Tap
	Exempt      string
	TapFree     bool
	Incident    bool
}

type Place struct {
	State    State
	Mover    Mover
	Reason   string
	Deadline time.Duration
}

var (
	clearing   = []State{StateReady, StateQueued, StateLanded, StateReleased, StateLive}
	reentering = map[Eviction]string{
		EvictionBatchRed: "a batch-mate's check was red, so it re-enters the queue at this head",
		EvictionFlaky:    "the queue cancelled it as flaky or with no reason, so it re-enters once",
		EvictionConflict: "the queue evicted it on a conflict, so it is restacked",
	}
)

func Decide(pr PR, beneath *Place) Place {
	switch {
	case pr.Live:
		return Place{StateLive, MoverNobody, "live on every target and tenant it touches", 0}
	case pr.Released:
		return Place{StateReleased, MoverRelease, "a started release carries it", 0}
	case pr.Landed:
		return Place{StateLanded, MoverRelease, "its squash is on the landing branch", 0}
	case pr.Merged:
		return Place{StateQueued, MoverQueue, "merged, with its squash not yet seen on the landing branch", QueueClock}
	case pr.Closed:
		return Place{StateClosed, MoverNobody, "closed without landing", 0}
	}
	tapped := pr.Tap != TapRejected && (pr.TapFree || pr.Incident || pr.Exempt != "" || pr.Tap == TapTapped)
	if pr.Queued && tapped && pr.Hold.Lifter == "" && clears(beneath) {
		return Place{StateQueued, MoverQueue, "in the merge queue at this head", QueueClock}
	}
	if !pr.Incident && !pr.Graded {
		return Place{StateGrading, MoverPool, "this head has no finder or fidelity grade yet", GradeClock}
	}
	if pr.Tap == TapRejected {
		return Place{StateRejected, MoverLane, "the owner refused this change", 0}
	}
	if fix := needsFix(pr, beneath); fix != "" {
		return Place{StateNeedsFix, MoverLane, fix, LaneClock}
	}
	if !pr.Incident && pr.Finding == FindingOpen {
		return Place{StateAppeal, MoverAppeals, "a finding is open and not yet judged", AppealClock}
	}
	if !pr.Incident && pr.Finding == FindingConflict {
		return Place{StateAppeal, MoverRoot, "a finding conflicts with the design and waits on the coordinator", LaneClock}
	}
	if pr.Checks != CISuccess {
		return Place{StateChecks, MoverChecks, "required checks are still running", CIClock}
	}
	if pr.Review != ReviewApproved {
		return Place{StateChecks, MoverChecks, "waiting on the approving review", LaneClock}
	}
	if pr.Hold.Lifter != "" {
		clock := LaneClock
		if pr.Hold.Lifter == MoverOwner {
			clock = OwnerClock
		}
		return Place{StateHeld, pr.Hold.Lifter, "held: " + pr.Hold.Reason, clock}
	}
	if why, found := reentering[pr.Eviction]; found {
		return Place{StateEvicted, MoverDesk, why, SupervisorClock}
	}
	if !tapped {
		if clears(beneath) || beneath.Mover == MoverOwner && (beneath.State == StateAwaitingTap || beneath.State == StateHeld) {
			return Place{StateAwaitingTap, MoverOwner, "clean, green and approved, with no tap on this change", OwnerClock}
		}
		return below(beneath)
	}
	if !clears(beneath) {
		return below(beneath)
	}
	if pr.Exempt != "" {
		return Place{StateReady, MoverDesk, "every gate passes, exempt as " + pr.Exempt, SupervisorClock}
	}
	return Place{StateReady, MoverDesk, "every gate passes", SupervisorClock}
}

func needsFix(pr PR, beneath *Place) string {
	switch {
	case pr.Checks == CIFailure:
		return "a required check is red: " + strings.Join(pr.Failing, ", ")
	case pr.Unmergeable:
		return "a conflict the replay could not solve"
	case pr.Review == ReviewChanges:
		return "a review asks for changes"
	case !pr.Incident && pr.Finding == FindingUpheld:
		return "a finding was upheld as real"
	case pr.Eviction == EvictionOwnRed:
		return "the queue evicted it for its own red check"
	case pr.Eviction == EvictionFlaky && pr.Reentered:
		return "the queue cancelled it again after its one re-entry"
	case beneath != nil && beneath.State == StateClosed:
		return "the PR beneath closed without landing"
	}
	return ""
}

func clears(beneath *Place) bool {
	return beneath == nil || slices.Contains(clearing, beneath.State)
}

func below(beneath *Place) Place {
	return Place{StateBelow, MoverBeneath, "the PR beneath is " + string(beneath.State) + ": " + beneath.Reason, 0}
}
