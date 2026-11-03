package store

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
)

func TestCardsDependingOnReadsTheDependencyIndex(t *testing.T) {
	s := memory(t)
	for i := range 100 {
		if err := s.PutCardDeps(CardRef{"s1", fmt.Sprintf("card-%d", i), "lane-a"}, []string{fmt.Sprintf("#%d", i), "lookups"}); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.PutCardDeps(CardRef{"s1", "card-7", "lane-b"}, []string{"#7", "postgres"}); err != nil {
		t.Fatal(err)
	}
	got, err := s.CardsDependingOn([]string{"postgres", "#3"})
	if err != nil {
		t.Fatal(err)
	}
	if want := []CardRef{{"s1", "card-3", "lane-a"}, {"s1", "card-7", "lane-b"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("dependents = %v", got)
	}
	if deps, err := s.CardDeps(CardRef{Session: "s1", Card: "card-7"}); err != nil || !reflect.DeepEqual(deps, []string{"#7", "postgres"}) {
		t.Fatalf("a redrawn card keeps its old dependencies: %v, %v", deps, err)
	}
	rows, err := s.db.Query("EXPLAIN QUERY PLAN "+dependentsQuery(2), "postgres", "#3")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var plan []string
	for rows.Next() {
		var id, parent, unused int
		var detail string
		if err := rows.Scan(&id, &parent, &unused, &detail); err != nil {
			t.Fatal(err)
		}
		plan = append(plan, detail)
	}
	if joined := strings.Join(plan, "; "); !strings.Contains(joined, "SEARCH card_deps USING") || !strings.Contains(joined, "dep=?") {
		t.Fatalf("the dependents read scans: %s", joined)
	}
}

func TestADroppedCardNoLongerDepends(t *testing.T) {
	s := memory(t)
	ref := CardRef{"s1", "card-a", "lane-a"}
	if err := s.PutCardDeps(ref, []string{"#1"}); err != nil {
		t.Fatal(err)
	}
	if err := s.DropCardDeps(ref); err != nil {
		t.Fatal(err)
	}
	if got, err := s.CardsDependingOn([]string{"#1"}); err != nil || len(got) != 0 {
		t.Fatalf("dependents = %v, %v", got, err)
	}
}
