package store

import (
	"reflect"
	"testing"
	"time"
)

func TestALedgerMirrorReplacesItsRowsAndKeepsOtherLedgers(t *testing.T) {
	s, err := Memory()
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	at := time.Date(2026, 11, 2, 19, 0, 0, 0, time.UTC)
	first := map[string]map[string]string{"ask/000001": {"lane": "a"}, "7": {"head": "abc"}}
	if err := s.PutLedgerRows("one", first, at); err != nil {
		t.Fatal(err)
	}
	if err := s.PutLedgerRows("two", map[string]map[string]string{"8": {"head": "def"}}, at); err != nil {
		t.Fatal(err)
	}
	later := at.Add(time.Hour)
	if err := s.PutLedgerRows("one", map[string]map[string]string{"7": {"head": "new"}}, later); err != nil {
		t.Fatal(err)
	}
	if got, want := read(t, s, "one"), (map[string]map[string]string{"7": {"head": "new"}}); !reflect.DeepEqual(got, want) {
		t.Fatalf("one = %v", got)
	}
	if got, want := read(t, s, "two"), (map[string]map[string]string{"8": {"head": "def"}}); !reflect.DeepEqual(got, want) {
		t.Fatalf("two = %v", got)
	}
	synced, err := s.LedgerSynced("one")
	if err != nil || !synced.Equal(later) {
		t.Fatalf("synced = %v, %v", synced, err)
	}
}

func TestOneRowWritesThroughAndAnUnsyncedLedgerHasNoTime(t *testing.T) {
	s, err := Memory()
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	synced, err := s.LedgerSynced("one")
	if err != nil || !synced.IsZero() {
		t.Fatalf("synced = %v, %v", synced, err)
	}
	at := time.Date(2026, 11, 2, 19, 0, 0, 0, time.UTC)
	for _, head := range []string{"abc", "def"} {
		if err := s.PutLedgerRow("one", "7", map[string]string{"head": head}, at); err != nil {
			t.Fatal(err)
		}
	}
	if got, want := read(t, s, "one"), (map[string]map[string]string{"7": {"head": "def"}}); !reflect.DeepEqual(got, want) {
		t.Fatalf("one = %v", got)
	}
	fields, found, err := s.LedgerRow("one", "7")
	if err != nil || !found || fields["head"] != "def" {
		t.Fatalf("row = %v, %v, %v", fields, found, err)
	}
	if _, found, err := s.LedgerRow("one", "8"); err != nil || found {
		t.Fatalf("an absent row read: %v, %v", found, err)
	}
}

func read(t *testing.T, s *Store, ledger string) map[string]map[string]string {
	t.Helper()
	rows, err := s.LedgerRows(ledger)
	if err != nil {
		t.Fatal(err)
	}
	return rows
}
