package store

import (
	"fmt"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

// Action is model.Action: one durable record of a side effect an executor owns. Response and VerificationReceipt
// hold decoded JSON, nil for None.
type Action struct {
	IncidentID          string
	ActionID            string
	Kind                string
	Target              string
	AuthorityRef        *string
	InputRevision       int64
	OwnerGeneration     int64
	AcceptedAt          string
	DispatchID          *string
	StartedAt           *string
	Deadline            *string
	Status              string
	Response            any
	VerificationReceipt any
	Reason              *string
}

var terminal = map[string]bool{"verified": true, "failed": true}

var requiredFields = []string{"incident_id", "action_id", "kind", "target", "authority_ref", "input_revision", "owner_generation", "accepted_at"}

func (a *Action) fields() *pyjson.Object {
	return pyjson.ObjectOf(
		"incident_id", a.IncidentID,
		"action_id", a.ActionID,
		"kind", a.Kind,
		"target", a.Target,
		"authority_ref", nullable(a.AuthorityRef),
		"input_revision", a.InputRevision,
		"owner_generation", a.OwnerGeneration,
		"accepted_at", a.AcceptedAt,
		"dispatch_id", nullable(a.DispatchID),
		"started_at", nullable(a.StartedAt),
		"deadline", nullable(a.Deadline),
		"status", a.Status,
		"response", a.Response,
		"verification_receipt", a.VerificationReceipt,
		"reason", nullable(a.Reason),
	)
}

func (a *Action) record() (string, error) {
	return pyjson.DumpsCompact(a.fields())
}

func (a *Action) clone() (*Action, error) {
	copied := *a
	for _, field := range []**string{&copied.AuthorityRef, &copied.DispatchID, &copied.StartedAt, &copied.Deadline, &copied.Reason} {
		if *field != nil {
			text := **field
			*field = &text
		}
	}
	var err error
	if copied.Response, err = detached(a.Response); err != nil {
		return nil, err
	}
	if copied.VerificationReceipt, err = detached(a.VerificationReceipt); err != nil {
		return nil, err
	}
	return &copied, nil
}

func loadAction(record string) (*Action, error) {
	value, err := pyjson.Loads(record)
	if err != nil {
		return nil, err
	}
	object, ok := value.(*pyjson.Object)
	if !ok {
		return nil, fmt.Errorf("Action() argument after ** must be a mapping, not %T", value)
	}
	action := &Action{Status: "accepted"}
	for _, key := range object.Keys() {
		item, _ := object.Get(key)
		switch key {
		case "incident_id":
			err = asField(key, item, &action.IncidentID)
		case "action_id":
			err = asField(key, item, &action.ActionID)
		case "kind":
			err = asField(key, item, &action.Kind)
		case "target":
			err = asField(key, item, &action.Target)
		case "authority_ref":
			err = asOptionalField(key, item, &action.AuthorityRef)
		case "input_revision":
			err = asField(key, item, &action.InputRevision)
		case "owner_generation":
			err = asField(key, item, &action.OwnerGeneration)
		case "accepted_at":
			err = asField(key, item, &action.AcceptedAt)
		case "dispatch_id":
			err = asOptionalField(key, item, &action.DispatchID)
		case "started_at":
			err = asOptionalField(key, item, &action.StartedAt)
		case "deadline":
			err = asOptionalField(key, item, &action.Deadline)
		case "status":
			err = asField(key, item, &action.Status)
		case "response":
			action.Response = item
		case "verification_receipt":
			action.VerificationReceipt = item
		case "reason":
			err = asOptionalField(key, item, &action.Reason)
		default:
			err = fmt.Errorf("Action.__init__() got an unexpected keyword argument %s", pyRepr(key))
		}
		if err != nil {
			return nil, err
		}
	}
	for _, field := range requiredFields {
		if !object.Has(field) {
			return nil, fmt.Errorf("Action.__init__() missing 1 required positional argument: %s", pyRepr(field))
		}
	}
	return action, nil
}

func asField[T any](key string, item any, field *T) error {
	value, ok := item.(T)
	if !ok {
		return fmt.Errorf("action record field %s holds %T, not %T", pyRepr(key), item, *field)
	}
	*field = value
	return nil
}

func asOptionalField(key string, item any, field **string) error {
	if item == nil {
		*field = nil
		return nil
	}
	var text string
	if err := asField(key, item, &text); err != nil {
		return err
	}
	*field = &text
	return nil
}

// Incident is the model.Incident a store creates: its header, its facts, and the decisions and milestones every
// new incident starts with.
type Incident struct {
	IncidentID      string
	Owner           string
	OwnerGeneration int64
	OpenedAt        string
	Facts           *pyjson.Object
	InputRevision   int64
	Status          string
	PendingOwner    *string
	Decisions       []any
	Milestones      []any
}

// NewIncident is Incident(incident_id, owner, owner_generation, opened_at, facts) with the dataclass defaults.
func NewIncident(incidentID, owner string, ownerGeneration int64, openedAt string, facts *pyjson.Object) *Incident {
	return &Incident{
		IncidentID:      incidentID,
		Owner:           owner,
		OwnerGeneration: ownerGeneration,
		OpenedAt:        openedAt,
		Facts:           facts,
		InputRevision:   1,
		Status:          "open",
		Decisions:       []any{},
		Milestones:      []any{},
	}
}
