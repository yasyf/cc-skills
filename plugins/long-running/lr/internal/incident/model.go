package incident

import (
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type ActionStatus string

const (
	ActionAccepted     ActionStatus = "accepted"
	ActionStarted      ActionStatus = "started"
	ActionCompleted    ActionStatus = "completed"
	ActionVerified     ActionStatus = "verified"
	ActionFailed       ActionStatus = "failed"
	ActionUnverifiable ActionStatus = "unverifiable"
)

var openStatuses = []ActionStatus{ActionAccepted, ActionStarted, ActionCompleted, ActionUnverifiable}

type Status string

const (
	StatusOpen              Status = "open"
	StatusActivationPending Status = "activation_pending"
	StatusRecovering        Status = "recovering"
	StatusRecovered         Status = "recovered"
	StatusClosed            Status = "closed"
)

var (
	ErrStaleGeneration = errors.New("stale owner generation")
	ErrUnknownAction   = errors.New("unknown action")
)

type nullable string

func (n nullable) MarshalJSON() ([]byte, error) {
	if n == "" {
		return []byte("null"), nil
	}
	return json.Marshal(string(n))
}

type Action struct {
	IncidentID          string         `json:"incident_id"`
	ActionID            string         `json:"action_id"`
	Kind                string         `json:"kind"`
	Target              string         `json:"target"`
	AuthorityRef        nullable       `json:"authority_ref"`
	InputRevision       int64          `json:"input_revision"`
	OwnerGeneration     int64          `json:"owner_generation"`
	AcceptedAt          string         `json:"accepted_at"`
	DispatchID          nullable       `json:"dispatch_id"`
	StartedAt           nullable       `json:"started_at"`
	Deadline            nullable       `json:"deadline"`
	Status              ActionStatus   `json:"status"`
	Response            map[string]any `json:"response"`
	VerificationReceipt map[string]any `json:"verification_receipt"`
	Reason              nullable       `json:"reason"`
}

func (a Action) terminal() bool {
	return a.Status == ActionVerified || a.Status == ActionFailed
}

func (a Action) Overdue(now time.Time) (bool, error) {
	if !slices.Contains(openStatuses, a.Status) || a.Deadline == "" {
		return false, nil
	}
	deadline, err := store.Parse(string(a.Deadline))
	if err != nil {
		return false, fmt.Errorf("action %s deadline: %w", a.ActionID, err)
	}
	return !deadline.After(now), nil
}

type Decision struct {
	Key        string   `json:"key"`
	Text       string   `json:"text"`
	At         string   `json:"at"`
	ResolvedAt nullable `json:"resolved_at"`
}

type Milestone struct {
	Name string `json:"name"`
	Text string `json:"text"`
	At   string `json:"at"`
}

type Header struct {
	ID              string   `json:"incident_id"`
	Owner           string   `json:"owner"`
	OwnerGeneration int64    `json:"owner_generation"`
	OpenedAt        string   `json:"opened_at"`
	InputRevision   int64    `json:"input_revision"`
	Status          Status   `json:"status"`
	PendingOwner    nullable `json:"pending_owner"`
}

type Incident struct {
	Header
	Facts      map[string]any    `json:"facts"`
	Actions    map[string]Action `json:"actions"`
	Decisions  []Decision        `json:"decisions"`
	Milestones []Milestone       `json:"milestones"`
}

func (i Incident) OpenDecisions() []Decision {
	var open []Decision
	for _, decision := range i.Decisions {
		if decision.ResolvedAt == "" {
			open = append(open, decision)
		}
	}
	return open
}

func (i Incident) Reached(name string) (Milestone, bool) {
	return reached(i.Milestones, name)
}

func reached(milestones []Milestone, name string) (Milestone, bool) {
	index := slices.IndexFunc(milestones, func(entry Milestone) bool { return entry.Name == name })
	if index < 0 {
		return Milestone{}, false
	}
	return milestones[index], true
}

type RecordIDs struct {
	Investigation string `json:"investigation"`
	Log           string `json:"log"`
}

type Facts struct {
	Kind          string              `json:"kind"`
	Pipeline      string              `json:"pipeline"`
	Target        string              `json:"target"`
	CanaryJob     string              `json:"canary_job"`
	HumanReview   bool                `json:"human_review"`
	ReviewSurface string              `json:"review_surface"`
	Repo          string              `json:"repo"`
	Trunk         string              `json:"trunk"`
	Checkout      string              `json:"checkout"`
	Thread        string              `json:"thread"`
	Alert         string              `json:"alert"`
	CodePath      string              `json:"code_path"`
	Runbook       string              `json:"runbook"`
	Adopted       map[string]string   `json:"adopted"`
	Onset         string              `json:"onset"`
	Reported      string              `json:"reported"`
	Bus           string              `json:"bus"`
	CommsLane     string              `json:"comms_lane"`
	RootLane      string              `json:"root_lane"`
	Grants        map[string]string   `json:"grants"`
	Routes        map[string][]string `json:"routes"`
	ExpectConfig  string              `json:"expect_config"`
	RepairPR      int                 `json:"repair_pr"`
	OrcaRun       string              `json:"orca_run"`
	OrcaRepo      string              `json:"orca_repo"`
	Common        string              `json:"common"`
	Rulings       []string            `json:"rulings"`
	EntryPoint    string              `json:"entry_point"`
	Sources       map[string]int      `json:"sources"`
	Records       *RecordIDs          `json:"records"`
	Mechanism     string              `json:"mechanism"`
	Live          string              `json:"live"`
	NotOurs       string              `json:"not_ours"`
	DesignCheck   string              `json:"design_check"`
	DesignOK      bool                `json:"design_ok"`
	LandedSHA     string              `json:"landed_sha"`
	ActivatedAt   string              `json:"activated_at"`
	Canary        string              `json:"canary"`
	Accounting    map[string]any      `json:"accounting"`
}
