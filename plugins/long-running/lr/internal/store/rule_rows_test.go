package store

import "testing"

func TestCheckedRulesReadsOnlyRulesWithACheck(t *testing.T) {
	s, err := Memory()
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.PutRule("unchecked", "words", "2026-11-02", "", ""); err != nil {
		t.Fatal(err)
	}
	if err := s.PutRule("pick/1", "no R2 deletion", "2026-11-02", "predicate", "answers:rule 29"); err != nil {
		t.Fatal(err)
	}
	rules, err := s.CheckedRules()
	if err != nil {
		t.Fatal(err)
	}
	if len(rules) != 1 || rules[0] != (Rule{"pick/1", "no R2 deletion", "2026-11-02", "predicate", "answers:rule 29"}) {
		t.Fatalf("rules = %+v", rules)
	}
}
