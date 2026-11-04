package incident

import (
	"reflect"
	"strings"
	"testing"
)

func TestFactEntriesPreserveOtherEntriesAndEscapeTheirKeys(t *testing.T) {
	s := created(t, map[string]any{"clocks": map[string]any{}, "other": "kept"})
	key := "lane/a~b"
	want := map[string]any{"ended": "", "reached": map[string]any{"mechanism": "R2"}}
	if err := s.Edit("i1", func(tx *Tx) error {
		if err := tx.SetFactEntry("clocks", "closed", map[string]any{"ended": "done"}); err != nil {
			return err
		}
		return tx.SetFactEntry("clocks", key, want)
	}); err != nil {
		t.Fatal(err)
	}
	got, found, err := s.FactEntry("i1", "clocks", key)
	if err != nil || !found || !reflect.DeepEqual(got, want) {
		t.Fatal(got, found, err)
	}
	keys, err := s.MatchingFactEntries("i1", "clocks", "ended", "")
	if err != nil || !reflect.DeepEqual(keys, []string{key}) {
		t.Fatal(keys, err)
	}
	if err := s.Edit("i1", func(tx *Tx) error { return tx.DropFactEntry("clocks", key) }); err != nil {
		t.Fatal(err)
	}
	keys, err = s.FactEntryKeys("i1", "clocks")
	if err != nil || !reflect.DeepEqual(keys, []string{"closed"}) {
		t.Fatal(keys, err)
	}
	other, found, err := s.Fact("i1", "other")
	if err != nil || !found || other != "kept" {
		t.Fatal(other, found, err)
	}
}

func TestMatchingFactEntriesUsesTheValueIndexAndOnlyDirectEntries(t *testing.T) {
	s := created(t, map[string]any{
		"clocks": map[string]any{
			"active": map[string]any{"ended": ""},
			"closed": map[string]any{"ended": "done", "nested": map[string]any{"ended": ""}},
		},
		"elsewhere": map[string]any{"active": map[string]any{"ended": ""}},
	})
	keys, err := s.MatchingFactEntries("i1", "clocks", "ended", "")
	if err != nil || !reflect.DeepEqual(keys, []string{"active"}) {
		t.Fatal(keys, err)
	}
	path, _ := s.Path("i1")
	db, err := openDB(path, readSource)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	parent := factPath("", "clocks")
	rows, err := db.Query("EXPLAIN QUERY PLAN "+matchingFactEntries, "ended", `""`, parent+"/", parent+"0", parent)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var plans []string
	for rows.Next() {
		var id, parent, unused int
		var detail string
		if err := rows.Scan(&id, &parent, &unused, &detail); err != nil {
			t.Fatal(err)
		}
		plans = append(plans, detail)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.Join(plans, "\n"), "fact_value") {
		t.Fatalf("clock query did not use the fact value index: %v", plans)
	}
}

func TestFactEntriesRequireAnInitializedGroup(t *testing.T) {
	s := created(t, nil)
	if _, err := s.FactEntryKeys("i1", "clocks"); err == nil {
		t.Fatal("missing clock group was accepted")
	}
}
