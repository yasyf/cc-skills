package store

import (
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
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

func TestAThreadReadUsesTheKeyAtManyRows(t *testing.T) {
	s := memory(t)
	for i := range scaletest.N() {
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

func TestAnAlertKeepsItsFirstPost(t *testing.T) {
	s := memory(t)
	if _, found, err := s.AlertThreadOf("sentry-1"); err != nil || found {
		t.Fatalf("found %v, err %v before any post", found, err)
	}
	for _, posted := range []AlertThread{{Reply: "r1", TS: "1.0"}, {Reply: "r2", TS: "1.0"}, {Reply: "r3", TS: "2.0"}} {
		if err := s.PutAlertThread("sentry-1", posted); err != nil {
			t.Fatal(err)
		}
	}
	got, found, err := s.AlertThreadOf("sentry-1")
	if err != nil || !found || got != (AlertThread{Reply: "r1", TS: "1.0"}) {
		t.Fatalf("first post = %+v, found %v, err %v; want r1 at 1.0", got, found, err)
	}
}

func TestAnAlertReadUsesTheKeyAtManyAlerts(t *testing.T) {
	s := memory(t)
	for i := range scaletest.N() {
		if err := s.PutAlertThread(fmt.Sprintf("sentry-%d", i), AlertThread{Reply: "r", TS: "1.0"}); err != nil {
			t.Fatal(err)
		}
	}
	var id, parent, unused int
	var plan string
	if err := s.db.QueryRow("EXPLAIN QUERY PLAN "+alertThreadRead, "sentry-7").Scan(&id, &parent, &unused, &plan); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(plan, "USING INDEX") {
		t.Fatalf("plan = %q, want a key lookup", plan)
	}
}
