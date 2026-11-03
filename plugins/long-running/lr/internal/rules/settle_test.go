package rules

import (
	"reflect"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestAFindingAnOwnerPickAnswersIsSettledByKeyOrByItsWords(t *testing.T) {
	picks := []store.Rule{
		{ID: "pick/1", Words: "no R2 deletion, ever", CheckKind: "predicate", CheckRef: "answers:rule/da49cb1 29"},
		{ID: "pick/2", Words: "keep the old agents", CheckKind: "predicate", CheckRef: "answers:Old Cloud Agents"},
		{ID: "pick/3", Words: "a diff rule", CheckKind: "predicate", CheckRef: "symbol-kept:Follower"},
		{ID: "pick/4", Words: "a judgment", CheckKind: "judgment", CheckRef: "answers:never read"},
	}
	findings := []Finding{
		{"rule/da49cb1 29", "deletes an R2 object"},
		{"plan 7", "the job keeps the old cloud agents' rows"},
		{"plan 8", "Follower is gone, and never read again"},
	}
	open, settled := Settle(findings, picks)
	if want := []Finding{findings[2]}; !reflect.DeepEqual(open, want) {
		t.Fatalf("open = %+v", open)
	}
	want := []Settled{{findings[0], "pick/1", "no R2 deletion, ever"}, {findings[1], "pick/2", "keep the old agents"}}
	if !reflect.DeepEqual(settled, want) {
		t.Fatalf("settled = %+v", settled)
	}
}

func TestNoPicksSettleNothing(t *testing.T) {
	findings := []Finding{{"plan 7", "a gap"}}
	open, settled := Settle(findings, nil)
	if !reflect.DeepEqual(open, findings) || len(settled) != 0 {
		t.Fatalf("open = %+v, settled = %+v", open, settled)
	}
}
