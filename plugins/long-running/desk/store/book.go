package store

import (
	"context"
	"errors"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

// Clock is the time source a Book stamps with; the runner's Shell satisfies it.
type Clock interface {
	Now() time.Time
}

// Book is book.Book: generation-checked edits over the store, where a change refused with a ValueError writes
// nothing.
type Book struct {
	Store *Store
	clock Clock
}

// NewBook is Book(store, shell).
func NewBook(store *Store, clock Clock) *Book {
	return &Book{Store: store, clock: clock}
}

// Stamp is the clock's now as a stamp.
func (b *Book) Stamp() string {
	return Stamp(b.clock.Now())
}

// Ensure creates container owned by owner at generation 0 unless it exists.
func (b *Book) Ensure(ctx context.Context, container, owner string) error {
	return b.Store.Ensure(ctx, NewIncident(container, owner, 0, b.Stamp(), pyjson.NewObject()))
}

// Exists is whether container has a database.
func (b *Book) Exists(container string) (bool, error) {
	return b.Store.Exists(container)
}

// Fact is Store.Fact.
func (b *Book) Fact(ctx context.Context, container, key string, def ...any) (any, error) {
	return b.Store.Fact(ctx, container, key, def...)
}

// Facts is Store.Facts.
func (b *Book) Facts(ctx context.Context, container string, keys []string) (*pyjson.Object, error) {
	return b.Store.Facts(ctx, container, keys)
}

// Action is Store.Action.
func (b *Book) Action(ctx context.Context, container, key string) (*Action, error) {
	return b.Store.Action(ctx, container, key)
}

// Actions is Store.Actions.
func (b *Book) Actions(ctx context.Context, container string, filters ...Filter) ([]*Action, error) {
	return b.Store.Actions(ctx, container, filters...)
}

// Known is the stored action under actionID, nil when container has no database or no such action.
func (b *Book) Known(ctx context.Context, container, actionID string) (*Action, error) {
	held, err := b.Store.Exists(container)
	if err != nil || !held {
		return nil, err
	}
	return b.Store.FindAction(ctx, container, actionID)
}

// Edit runs change in one transaction on container, retrying it after a StaleGeneration, and returns its result
// detached: a *Facts as a *pyjson.Object, and Objects, lists and actions copied.
func (b *Book) Edit(ctx context.Context, container string, change func(*Transaction) (any, error)) (any, error) {
	for {
		var result any
		err := b.Store.Transaction(ctx, container, func(tx *Transaction) error {
			value, err := change(tx)
			if err != nil {
				return err
			}
			result, err = detached(value)
			return err
		})
		var stale *StaleGeneration
		if errors.As(err, &stale) {
			continue
		}
		return result, err
	}
}

// Attempt is Edit reporting whether change applied: false for a ValueError or UnknownAction, which writes nothing.
func (b *Book) Attempt(ctx context.Context, container string, change func(*Transaction) error) (bool, error) {
	_, err := b.Edit(ctx, container, func(tx *Transaction) (any, error) { return nil, change(tx) })
	if refusesAttempt(err) {
		return false, nil
	}
	return err == nil, err
}

// Accept is the stored action under actionID with false, or else the action accepted now in container with true;
// a new action takes deadline (nil for none).
func (b *Book) Accept(ctx context.Context, container, actionID, kind, target, authority string, deadline *time.Time) (*Action, bool, error) {
	known, err := b.Known(ctx, container, actionID)
	if err != nil || known != nil {
		return known, false, err
	}
	result, err := b.Edit(ctx, container, func(tx *Transaction) (any, error) {
		held, err := tx.HasAction(actionID)
		if err != nil {
			return nil, err
		}
		action, err := tx.Accept(actionID, kind, target, &authority, b.clock.Now())
		if err != nil {
			return nil, err
		}
		if !held && deadline != nil {
			stamped := Stamp(*deadline)
			action.Deadline = &stamped
		}
		return []any{action, !held}, nil
	})
	if err != nil {
		return nil, false, err
	}
	pair := result.([]any)
	return pair[0].(*Action), pair[1].(bool), nil
}
