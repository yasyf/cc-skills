package store

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

type fixedClock struct{ at time.Time }

func (c *fixedClock) Now() time.Time { return c.at }

func newTestBook(t *testing.T, containers ...string) *Book {
	t.Helper()
	book := NewBook(New(t.TempDir()), &fixedClock{at: time.Date(2026, 11, 2, 8, 0, 0, 0, time.UTC)})
	for _, container := range containers {
		if err := book.Ensure(context.Background(), container, container); err != nil {
			t.Fatal(err)
		}
	}
	return book
}

func TestTransactionStateClosesWithItsTransaction(t *testing.T) {
	ctx := context.Background()
	book := newTestBook(t, "desk-runner")
	var kept *Transaction
	var view *Facts
	if _, err := book.Edit(ctx, "desk-runner", func(tx *Transaction) (any, error) {
		facts, err := tx.Facts()
		if err != nil {
			return nil, err
		}
		if err := facts.Set("mapping", pyjson.ObjectOf("x", 1)); err != nil {
			return nil, err
		}
		item, err := facts.Get("mapping")
		kept, view = tx, item.(*Facts)
		return nil, err
	}); err != nil {
		t.Fatal(err)
	}
	var closed *RuntimeError
	if _, err := view.Get("x"); !errors.As(err, &closed) {
		t.Errorf("a fact view read after its transaction gives %v, not a RuntimeError", err)
	}
	if _, err := kept.Facts(); !errors.As(err, &closed) {
		t.Errorf("Facts after the transaction gives %v, not a RuntimeError", err)
	}
	if _, err := kept.HasAction("x"); !errors.As(err, &closed) {
		t.Errorf("HasAction after the transaction gives %v, not a RuntimeError", err)
	}
}

func TestReplacedMappingRefusesItsOldView(t *testing.T) {
	book := newTestBook(t, "desk-runner")
	_, err := book.Edit(context.Background(), "desk-runner", func(tx *Transaction) (any, error) {
		facts, err := tx.Facts()
		if err != nil {
			return nil, err
		}
		if err := facts.Set("a", pyjson.ObjectOf("x", 1)); err != nil {
			return nil, err
		}
		item, err := facts.Get("a")
		if err != nil {
			return nil, err
		}
		if err := facts.Set("a", pyjson.ObjectOf("y", 2)); err != nil {
			return nil, err
		}
		_, err = item.(*Facts).Get("y")
		return nil, err
	})
	var replaced *RuntimeError
	if !errors.As(err, &replaced) || err.Error() != "fact mapping was replaced or removed during the transaction" {
		t.Errorf("a replaced mapping's old view reads with %v", err)
	}
}

func TestAttemptRefusesValueErrorsAndUnknownActionsOnly(t *testing.T) {
	ctx := context.Background()
	book := newTestBook(t, "desk-runner")
	if _, _, err := book.Accept(ctx, "desk-runner", "a", "escalation", "X a b: c", "runner", nil); err != nil {
		t.Fatal(err)
	}
	refusals := map[string]func(*Transaction) error{
		"a refused transition": func(tx *Transaction) error { _, err := tx.Complete("a", pyjson.NewObject(), ""); return err },
		"an unknown action":    func(tx *Transaction) error { _, err := tx.Action("missing"); return err },
		"an unencodable key": func(tx *Transaction) error {
			facts, err := tx.Facts()
			if err != nil {
				return err
			}
			return facts.Set("\xed\xa0\xbd", 1)
		},
	}
	for name, change := range refusals {
		if applied, err := book.Attempt(ctx, "desk-runner", change); applied || err != nil {
			t.Errorf("%s: Attempt = %v, %v; want a refusal", name, applied, err)
		}
	}
	_, err := book.Attempt(ctx, "desk-runner", func(tx *Transaction) error {
		facts, err := tx.Facts()
		if err != nil {
			return err
		}
		_, err = facts.Get("missing")
		return err
	})
	var missing *KeyError
	if !errors.As(err, &missing) || err.Error() != "'missing'" {
		t.Errorf("a missing fact reaches Attempt's caller as %v, not the KeyError", err)
	}
	if facts, err := book.Facts(ctx, "desk-runner", nil); err != nil || facts.Len() != 0 {
		t.Errorf("refused attempts left facts %v, %v", facts, err)
	}
	var unencodable *ValueError
	_, err = book.Edit(ctx, "desk-runner", func(tx *Transaction) (any, error) {
		facts, err := tx.Facts()
		if err != nil {
			return nil, err
		}
		return nil, facts.Set("\xed\xa0\xbd", 1)
	})
	if !errors.As(err, &unencodable) || err.Error() != `'utf-8' codec can't encode character '\ud83d' in position 1: surrogates not allowed` {
		t.Errorf("a surrogate key writes with %v", err)
	}
}

func TestMissingAndInvalidIncidents(t *testing.T) {
	ctx := context.Background()
	book := newTestBook(t)
	var missing *NoIncident
	if _, err := book.Fact(ctx, "desk-missing", "x", nil); !errors.As(err, &missing) || err.Error() != fmt.Sprintf("no incident desk-missing in %s", book.Store.Root()) {
		t.Errorf("a read of a missing incident gives %v", err)
	}
	if _, err := book.Edit(ctx, "desk-missing", func(*Transaction) (any, error) { return nil, nil }); !errors.As(err, &missing) {
		t.Errorf("an edit of a missing incident gives %v", err)
	}
	var invalid *ValueError
	for _, id := range []string{"", "a/b", "a\x00b"} {
		if _, err := book.Exists(id); !errors.As(err, &invalid) || err.Error() != "incident ID must be a nonempty filename component" {
			t.Errorf("incident ID %q gives %v", id, err)
		}
	}
	if known, err := book.Known(ctx, "desk-missing", "x"); known != nil || err != nil {
		t.Errorf("Known on a missing incident = %v, %v", known, err)
	}
}

func TestAtGenerationRefusesAStaleWriter(t *testing.T) {
	book := newTestBook(t, "desk-runner")
	err := book.Store.Transaction(context.Background(), "desk-runner", func(*Transaction) error { return nil }, AtGeneration(1))
	var stale *StaleGeneration
	var runtime *RuntimeError
	if !errors.As(err, &stale) || !errors.As(err, &runtime) || err.Error() != "desk-runner is at owner generation 0 (desk-runner), not 1" {
		t.Errorf("a stale writer gets %v", err)
	}
	var unknown *UnknownAction
	var key *KeyError
	if err := error(&UnknownAction{ActionID: "it's"}); !errors.As(err, &unknown) || !errors.As(err, &key) || err.Error() != `"it's"` {
		t.Errorf("UnknownAction reads as %v", err)
	}
	for text, want := range map[string]string{"a\nb": `'a\nb'`, "\xed\xa0\xbd": `'\ud83d'`, "ñ": "'ñ'", "\u00a0": `'\xa0'`, "\x7f": `'\x7f'`} {
		if got := pyRepr(text); got != want {
			t.Errorf("pyRepr(%q) = %s, want %s", text, got, want)
		}
	}
	if got := factPath("", "a/b~c"); got != "/a~1b~0c" {
		t.Errorf("factPath escapes as %s", got)
	}
}

func TestReadingReusesConnectionsAndEvictsAsPython(t *testing.T) {
	ctx := context.Background()
	names := make([]string, readerLimit+1)
	for index := range names {
		names[index] = fmt.Sprintf("incident-%02d", index)
	}
	book := newTestBook(t, names...)
	_, outside, err := book.Store.ActionsWithVersion(ctx, names[0])
	if err != nil {
		t.Fatal(err)
	}
	if same, err := book.Store.Unchanged(ctx, names[0], outside); same || err != nil {
		t.Errorf("a version read outside a reading scope stays unchanged: %v, %v", same, err)
	}
	err = book.Store.Reading(ctx, func(ctx context.Context) error {
		_, version, err := book.Store.ActionsWithVersion(ctx, names[0])
		if err != nil {
			return err
		}
		if same, err := book.Store.Unchanged(ctx, names[0], version); !same || err != nil {
			t.Errorf("an untouched incident reads as changed: %v, %v", same, err)
		}
		if err := book.Store.Reading(ctx, func(nested context.Context) error {
			same, err := book.Store.Unchanged(nested, names[0], version)
			if !same || err != nil {
				t.Errorf("a nested reading scope opened its own connections: %v, %v", same, err)
			}
			return err
		}); err != nil {
			return err
		}
		for _, name := range names[1:] {
			if _, err := book.Store.Actions(ctx, name); err != nil {
				return err
			}
		}
		scope := book.Store.scope(ctx)
		if len(scope.order) != readerLimit || slices.Contains(scope.order, filepath.Join(book.Store.Root(), names[49]+".sqlite3")) {
			t.Errorf("after %d incidents the scope holds %d connections: %v", len(names), len(scope.order), scope.order)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
