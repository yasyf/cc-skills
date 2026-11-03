package asks

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func writable(t *testing.T) (Mirror, *ledgertest.Fake) {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	source := &ledgertest.Fake{}
	return Mirror{source, s, "l1", func() time.Time { return now }}, source
}

func accepted(context.Context, string) (string, error) {
	return "", nil
}

func ledgerRow(t *testing.T, source *ledgertest.Fake, key string) map[string]string {
	t.Helper()
	rows, err := source.Rows(context.Background(), "l1")
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range rows {
		if row.Key == key {
			return row.Fields
		}
	}
	return nil
}

func TestAnAskWritesToTheLedgerAndTheStoreAtTheNextKey(t *testing.T) {
	m, source := writable(t)
	ctx := context.Background()
	if err := source.SetRow(ctx, "l1", "ask/000009", map[string]string{"lane": "a", "text": "older", "dropped_at": "t"}); err != nil {
		t.Fatal(err)
	}
	line, held, err := m.Add(ctx, NewAsk{Text: "measure idle", Lane: "ship-2", Accept: "a number", Due: "4h", Standing: 2}, accepted)
	if err != nil || held != "" {
		t.Fatal(line, held, err)
	}
	if line != "ask/000010 ship-2 standing 2h: measure idle" {
		t.Fatalf("line = %q", line)
	}
	stored, found, err := m.Store.LedgerRow("l1", "ask/000010")
	if err != nil || !found {
		t.Fatal(found, err)
	}
	if ledgerRow(t, source, "ask/000010")["due"] != "2026-11-02T23:00:00Z" || stored["due"] != "2026-11-02T23:00:00Z" {
		t.Fatalf("store = %v, ledger = %v", stored, ledgerRow(t, source, "ask/000010"))
	}
}

func TestARepeatOfAnOpenAsksTextIsAProgressEntryNotANewRow(t *testing.T) {
	m, source := writable(t)
	ctx := context.Background()
	if _, _, err := m.Add(ctx, NewAsk{Text: "Measure  idle", Lane: "ship-2", Accept: "a", Due: "4h"}, accepted); err != nil {
		t.Fatal(err)
	}
	_, held, err := m.Add(ctx, NewAsk{Text: "measure idle", Lane: "other-3", Accept: "a", Due: "4h"}, func(context.Context, string) (string, error) {
		t.Fatal("a repeat checked its lane")
		return "", nil
	})
	if err != nil || held != "ask/000001" {
		t.Fatalf("held = %q, %v", held, err)
	}
	if progress := ledgerRow(t, source, "ask/000001")["progress_000001"]; !strings.Contains(progress, "asked again for other-3") {
		t.Fatalf("progress = %q", progress)
	}
	if ledgerRow(t, source, "ask/000002") != nil {
		t.Fatal("a repeat opened a new row")
	}
}

func TestAnAskOnADeadLaneIsRefusedAndWritesNothing(t *testing.T) {
	m, source := writable(t)
	_, _, err := m.Add(context.Background(), NewAsk{Text: "t", Lane: "gone-1", Accept: "a", Due: "4h"}, func(context.Context, string) (string, error) {
		return "gone-1 has posted nothing on d1 in 2h", nil
	})
	if err == nil || err.Error() != "ask refused: gone-1 has posted nothing on d1 in 2h" {
		t.Fatalf("err = %v", err)
	}
	if ledgerRow(t, source, "ask/000001") != nil {
		t.Fatal("a refused ask wrote a row")
	}
}

func TestProgressRestartsTheLostClockAndCountsUp(t *testing.T) {
	m, _ := writable(t)
	ctx := context.Background()
	if _, _, err := m.Add(ctx, NewAsk{Text: "t", Lane: "a", Accept: "x", Due: "4h"}, accepted); err != nil {
		t.Fatal(err)
	}
	for _, note := range []string{"first", "second"} {
		if _, err := m.Progress(ctx, "ask/000001", note, []string{"link"}); err != nil {
			t.Fatal(err)
		}
	}
	rows, err := m.Store.LedgerRows("l1")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(rows["ask/000001"]["progress_000002"], `"note":"second"`) {
		t.Fatalf("row = %v", rows["ask/000001"])
	}
	book, err := Read(rows)
	if err != nil {
		t.Fatal(err)
	}
	if state := book.State(book.Asks[0], now.Add(29*time.Minute)); state != InProgress {
		t.Fatalf("state = %s", state)
	}
}

func TestTerminalVerbsCloseAnAskAndAnUnknownOneIsNamed(t *testing.T) {
	m, _ := writable(t)
	ctx := context.Background()
	for range 2 {
		if _, _, err := m.Add(ctx, NewAsk{Text: time.Now().String(), Lane: "a", Accept: "x", Due: "4h"}, accepted); err != nil {
			t.Fatal(err)
		}
	}
	if err := m.Drop(ctx, "ask/000001", "withdrawn"); err != nil {
		t.Fatal(err)
	}
	if err := m.Answer(ctx, "ask/000002", "yes"); err != nil {
		t.Fatal(err)
	}
	rows, err := m.Store.LedgerRows("l1")
	if err != nil {
		t.Fatal(err)
	}
	book, err := Read(rows)
	if err != nil {
		t.Fatal(err)
	}
	if open := book.Open(now); len(open) != 0 {
		t.Fatalf("open = %+v", open)
	}
	if err := m.Drop(ctx, "ask/000099", "x"); err == nil || !strings.Contains(err.Error(), "no ask ask/000099") {
		t.Fatalf("err = %v", err)
	}
}

func TestAStateOverridesTheGradeAndOpenRestoresIt(t *testing.T) {
	m, _ := writable(t)
	ctx := context.Background()
	if _, _, err := m.Add(ctx, NewAsk{Text: "t", Lane: "a", Accept: "x", Due: "4h"}, accepted); err != nil {
		t.Fatal(err)
	}
	if err := m.SetState(ctx, "ask/000001", "backlog", "later"); err != nil {
		t.Fatal(err)
	}
	grade := func() State {
		rows, err := m.Store.LedgerRows("l1")
		if err != nil {
			t.Fatal(err)
		}
		book, err := Read(rows)
		if err != nil {
			t.Fatal(err)
		}
		return book.State(book.Asks[0], now)
	}
	if got := grade(); got != Backlog {
		t.Fatalf("state = %s", got)
	}
	if err := m.SetState(ctx, "ask/000001", "open", ""); err != nil {
		t.Fatal(err)
	}
	if got := grade(); got != Fresh {
		t.Fatalf("state = %s", got)
	}
	if err := m.SetState(ctx, "ask/000001", "done", ""); err == nil {
		t.Fatal("an unknown state was set")
	}
}

func TestAnAskIDReadsWithOrWithoutItsPrefix(t *testing.T) {
	for given, want := range map[string]string{"ask/000104": "ask/000104", "104": "ask/000104"} {
		if got, err := Key(given); err != nil || got != want {
			t.Errorf("%s = %s, %v", given, got, err)
		}
	}
	if _, err := Key("abc"); err == nil {
		t.Fatal("a bad id read")
	}
}

func TestALiveRowGradesADeliveredAskLive(t *testing.T) {
	m, _ := writable(t)
	ctx := context.Background()
	if err := m.Live(ctx, now.Format(time.RFC3339), "release 12"); err != nil {
		t.Fatal(err)
	}
	rows, err := m.Store.LedgerRows("l1")
	if err != nil {
		t.Fatal(err)
	}
	book, err := Read(rows)
	if err != nil || !book.LiveSince.Equal(now) {
		t.Fatalf("live = %v, %v", book.LiveSince, err)
	}
}
