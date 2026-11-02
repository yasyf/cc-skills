package store

import (
	"context"
	"fmt"
	"time"
)

const headerColumns = "incident_id, owner, owner_generation, opened_at, input_revision, status, pending_owner"

type actionMap struct {
	ctx        context.Context
	conn       *conn
	loaded     map[string]*Action
	order      []string
	original   map[string]string
	added      map[string]*Action
	addedOrder []string
	closed     bool
}

func (m *actionMap) active() (*conn, error) {
	if m.closed {
		return nil, &RuntimeError{Message: "unloaded transaction state requires an active edit context; use Store.load for a detached snapshot"}
	}
	return m.conn, nil
}

func (m *actionMap) remember(key, record string) (*Action, error) {
	if action, ok := m.loaded[key]; ok {
		return action, nil
	}
	action, err := loadAction(record)
	if err != nil {
		return nil, err
	}
	m.loaded[key], m.original[key] = action, record
	m.order = append(m.order, key)
	return action, nil
}

func (m *actionMap) get(key string) (*Action, error) {
	if action, ok := m.added[key]; ok {
		return action, nil
	}
	if action, ok := m.loaded[key]; ok {
		return action, nil
	}
	c, err := m.active()
	if err != nil {
		return nil, err
	}
	row, err := c.row(m.ctx, "SELECT action_key, ordinal, record FROM action WHERE action_key = ?", key)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, &KeyError{Key: key}
	}
	record, err := asText(row[2])
	if err != nil {
		return nil, err
	}
	return m.remember(key, record)
}

func (m *actionMap) contains(key string) (bool, error) {
	if _, ok := m.added[key]; ok {
		return true, nil
	}
	if _, ok := m.loaded[key]; ok {
		return true, nil
	}
	c, err := m.active()
	if err != nil {
		return false, err
	}
	row, err := c.row(m.ctx, "SELECT 1 FROM action WHERE action_key = ?", key)
	return row != nil, err
}

func (m *actionMap) set(key string, action *Action) error {
	if _, err := m.active(); err != nil {
		return err
	}
	if _, ok := m.added[key]; ok {
		m.added[key] = action
		return nil
	}
	held, err := m.contains(key)
	if err != nil {
		return err
	}
	if !held {
		m.added[key] = action
		m.addedOrder = append(m.addedOrder, key)
		return nil
	}
	if _, err := m.get(key); err != nil {
		return err
	}
	m.loaded[key] = action
	return nil
}

func (m *actionMap) flush() error {
	c, err := m.active()
	if err != nil {
		return err
	}
	for _, key := range m.order {
		action := m.loaded[key]
		record, err := action.record()
		if err != nil {
			return err
		}
		if record == m.original[key] {
			continue
		}
		if err := c.exec(m.ctx, "UPDATE action SET kind = ?, status = ?, record = ? WHERE action_key = ?", action.Kind, action.Status, record, key); err != nil {
			return err
		}
	}
	for _, key := range m.addedOrder {
		action := m.added[key]
		record, err := action.record()
		if err != nil {
			return err
		}
		if err := c.exec(m.ctx, "INSERT INTO action(action_key, kind, status, record) VALUES (?, ?, ?, ?)", key, action.Kind, action.Status, record); err != nil {
			return err
		}
	}
	return nil
}

type header struct {
	incidentID      string
	owner           string
	ownerGeneration int64
	openedAt        string
	inputRevision   int64
	status          string
	pendingOwner    *string
}

func (h header) equal(other header) bool {
	samePending := h.pendingOwner == nil && other.pendingOwner == nil ||
		h.pendingOwner != nil && other.pendingOwner != nil && *h.pendingOwner == *other.pendingOwner
	return samePending && h.incidentID == other.incidentID && h.owner == other.owner && h.ownerGeneration == other.ownerGeneration &&
		h.openedAt == other.openedAt && h.inputRevision == other.inputRevision && h.status == other.status
}

// Transaction is one incident's unit of work inside Store.Transaction, as sqlite_store.Transaction is: its header
// fields, its lazy facts and its actions, written back when the change returns without error.
type Transaction struct {
	IncidentID      string
	Owner           string
	OwnerGeneration int64
	OpenedAt        string
	InputRevision   int64
	Status          string
	PendingOwner    *string

	ctx      context.Context
	conn     *conn
	original header
	tree     *factTree
	facts    *Facts
	actions  *actionMap
}

func newTransaction(ctx context.Context, c *conn) (*Transaction, error) {
	row, err := c.row(ctx, "SELECT "+headerColumns+" FROM incident WHERE singleton = 1")
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, fmt.Errorf("the incident table holds no header row")
	}
	t := &Transaction{ctx: ctx, conn: c}
	for _, assign := range []func() error{
		func() (err error) { t.IncidentID, err = asText(row[0]); return err },
		func() (err error) { t.Owner, err = asText(row[1]); return err },
		func() (err error) { t.OwnerGeneration, err = asInteger(row[2]); return err },
		func() (err error) { t.OpenedAt, err = asText(row[3]); return err },
		func() (err error) { t.InputRevision, err = asInteger(row[4]); return err },
		func() (err error) { t.Status, err = asText(row[5]); return err },
		func() (err error) { t.PendingOwner, err = asOptionalText(row[6]); return err },
	} {
		if err := assign(); err != nil {
			return nil, err
		}
	}
	t.original = t.header()
	t.tree = newFactTree(ctx, c)
	t.facts = &Facts{tree: t.tree}
	t.actions = &actionMap{ctx: ctx, conn: c, loaded: map[string]*Action{}, original: map[string]string{}, added: map[string]*Action{}}
	return t, nil
}

func (t *Transaction) header() header {
	return header{t.IncidentID, t.Owner, t.OwnerGeneration, t.OpenedAt, t.InputRevision, t.Status, t.PendingOwner}
}

// Facts is the incident's top-level fact mapping.
func (t *Transaction) Facts() (*Facts, error) {
	if _, err := t.tree.active(); err != nil {
		return nil, err
	}
	return t.facts, nil
}

// HasAction is action_id in incident.actions.
func (t *Transaction) HasAction(actionID string) (bool, error) {
	return t.actions.contains(actionID)
}

// Action is ActionState.action: the live action, UnknownAction when the incident holds none under actionID.
func (t *Transaction) Action(actionID string) (*Action, error) {
	held, err := t.actions.contains(actionID)
	if err != nil {
		return nil, err
	}
	if !held {
		return nil, &UnknownAction{ActionID: actionID}
	}
	return t.actions.get(actionID)
}

// Accept is ActionState.accept: the action under actionID, accepted at `at` when the incident holds none yet.
func (t *Transaction) Accept(actionID, kind, target string, authorityRef *string, at time.Time) (*Action, error) {
	held, err := t.actions.contains(actionID)
	if err != nil {
		return nil, err
	}
	if !held {
		action := &Action{
			IncidentID:      t.IncidentID,
			ActionID:        actionID,
			Kind:            kind,
			Target:          target,
			AuthorityRef:    authorityRef,
			InputRevision:   t.InputRevision,
			OwnerGeneration: t.OwnerGeneration,
			AcceptedAt:      Stamp(at),
			Status:          "accepted",
		}
		if err := t.actions.set(actionID, action); err != nil {
			return nil, err
		}
	}
	return t.actions.get(actionID)
}

// Start is ActionState.start: an accepted action becomes started at `at`, with deadline (nil for none) and
// dispatchID, an empty dispatchID keeping the one it has.
func (t *Transaction) Start(actionID string, at time.Time, deadline *time.Time, dispatchID string) (*Action, error) {
	action, err := t.Action(actionID)
	if err != nil {
		return nil, err
	}
	if action.Status != "accepted" {
		return nil, &ValueError{Message: fmt.Sprintf("%s is %s, not accepted", actionID, action.Status)}
	}
	started := Stamp(at)
	action.Status, action.StartedAt = "started", &started
	action.Deadline = nil
	if deadline != nil {
		stamped := Stamp(*deadline)
		action.Deadline = &stamped
	}
	if dispatchID != "" {
		action.DispatchID = &dispatchID
	}
	return action, nil
}

// Complete is ActionState.complete: a started or unverifiable action becomes completed with its response, an
// empty dispatchID keeping the one it has.
func (t *Transaction) Complete(actionID string, response any, dispatchID string) (*Action, error) {
	action, err := t.Action(actionID)
	if err != nil {
		return nil, err
	}
	if action.Status != "started" && action.Status != "unverifiable" {
		return nil, &ValueError{Message: fmt.Sprintf("%s is %s, not started", actionID, action.Status)}
	}
	action.Status, action.Response = "completed", response
	if dispatchID != "" {
		action.DispatchID = &dispatchID
	}
	return action, nil
}

// Verify is ActionState.verify: an action not yet verified or failed becomes verified with its receipt.
func (t *Transaction) Verify(actionID string, receipt any) (*Action, error) {
	action, err := t.Action(actionID)
	if err != nil {
		return nil, err
	}
	if terminal[action.Status] {
		return nil, &ValueError{Message: fmt.Sprintf("%s is already %s", actionID, action.Status)}
	}
	action.Status, action.VerificationReceipt = "verified", receipt
	return action, nil
}

// Fail is ActionState.fail: an action not yet verified or failed becomes failed for reason.
func (t *Transaction) Fail(actionID, reason string) (*Action, error) {
	action, err := t.Action(actionID)
	if err != nil {
		return nil, err
	}
	if terminal[action.Status] {
		return nil, &ValueError{Message: fmt.Sprintf("%s is already %s", actionID, action.Status)}
	}
	action.Status, action.Reason = "failed", &reason
	return action, nil
}

func (t *Transaction) flush() error {
	if current := t.header(); !current.equal(t.original) {
		if err := t.conn.exec(t.ctx, "UPDATE incident SET incident_id = ?, owner = ?, owner_generation = ?, opened_at = ?, input_revision = ?, status = ?, pending_owner = ? WHERE singleton = 1",
			current.incidentID, current.owner, current.ownerGeneration, current.openedAt, current.inputRevision, current.status, nullable(current.pendingOwner)); err != nil {
			return err
		}
	}
	if err := t.tree.flush(); err != nil {
		return err
	}
	return t.actions.flush()
}

func (t *Transaction) close() {
	t.tree.closed = true
	t.actions.closed = true
}
