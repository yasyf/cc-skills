package store

import (
	"reflect"
	"testing"
)

func TestCheckedRulesReadsOnlyRulesWithACheck(t *testing.T) {
	s, err := Memory()
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.PutRule("unchecked", "words", "2026-11-02", "", ""); err != nil {
		t.Fatal(err)
	}
	if err := s.PutRule("pick/1", "no R2 deletion", "2026-11-02", "predicate", "answers:rule 29", "org/repo#7", "org/repo#8"); err != nil {
		t.Fatal(err)
	}
	rules, err := s.CheckedRules()
	if err != nil {
		t.Fatal(err)
	}
	if want := []Rule{{"pick/1", "no R2 deletion", "2026-11-02", "predicate", "answers:rule 29", []string{"org/repo#7", "org/repo#8"}}}; !reflect.DeepEqual(rules, want) {
		t.Fatalf("rules = %+v", rules)
	}
}
