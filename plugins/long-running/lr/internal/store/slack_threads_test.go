package store

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
)

func TestAThreadRowMergesEachPostAndKeepsTheLaneThatDrewIt(t *testing.T) {
	s := memory(t)
	card := SlackThread{TS: "1.0", Card: "card-a", Lane: "drawer", Question: "Which?", Options: []string{"Yes", "No"}}
	if err := s.PutSlackThread(card); err != nil {
		t.Fatal(err)
	}
	if err := s.PutSlackThread(SlackThread{TS: "1.0", Reply: "r1", Lane: "owner-relay", Row: "m7"}); err != nil {
		t.Fatal(err)
	}
	got, found, err := s.SlackThreadAt("1.0")
	if err != nil || !found {
		t.Fatalf("found %v, err %v", found, err)
	}
	want := SlackThread{TS: "1.0", Reply: "r1", Card: "card-a", Lane: "drawer", Row: "m7", Question: "Which?", Options: []string{"Yes", "No"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("row = %+v, want %+v", got, want)
	}
}

func TestAThreadNoPostNamedHasNoRow(t *testing.T) {
	if _, found, err := memory(t).SlackThreadAt("9.9"); err != nil || found {
		t.Fatalf("found %v, err %v", found, err)
	}
}

func TestAThreadReadUsesTheKeyAtAThousandRows(t *testing.T) {
	s := memory(t)
	for i := range 1000 {
		if err := s.PutSlackThread(SlackThread{TS: fmt.Sprintf("%d.0", i), Reply: "r"}); err != nil {
			t.Fatal(err)
		}
	}
	var id, parent, unused int
	var plan string
	err := s.db.QueryRow("EXPLAIN QUERY PLAN "+threadRead, "7.0").Scan(&id, &parent, &unused, &plan)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(plan, "USING INDEX") {
		t.Fatalf("plan = %q, want a key lookup", plan)
	}
}
