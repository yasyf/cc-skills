package rules

import (
	"reflect"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const onThisPR = "org/repo#7"

func TestAFindingAnOwnerPickAnswersIsSettledByKeyOrByItsWords(t *testing.T) {
	picks := []store.Rule{
		{ID: "pick/1", Words: "no R2 deletion, ever", CheckKind: "predicate", CheckRef: "answers:rule/da49cb1 29", PRs: []string{onThisPR}},
		{ID: "pick/2", Words: "keep the old agents", CheckKind: "predicate", CheckRef: "answers:Old Cloud Agents", PRs: []string{"org/repo#6", onThisPR}},
		{ID: "pick/3", Words: "a diff rule", CheckKind: "predicate", CheckRef: "symbol-kept:Follower", PRs: []string{onThisPR}},
		{ID: "pick/4", Words: "a judgment", CheckKind: "judgment", CheckRef: "answers:never read", PRs: []string{onThisPR}},
	}
	findings := []Finding{
		{"rule/da49cb1 29", "deletes an R2 object"},
		{"plan 7", "the job keeps the old cloud agents' rows"},
		{"plan 8", "Follower is gone, and never read again"},
	}
	open, settled := Settle(findings, picks, onThisPR)
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
	open, settled := Settle(findings, nil, onThisPR)
	if !reflect.DeepEqual(open, findings) || len(settled) != 0 {
		t.Fatalf("open = %+v, settled = %+v", open, settled)
	}
}

func TestAPickSettlesOnlyFindingsOnThePRsItWasRegisteredFor(t *testing.T) {
	picks := []store.Rule{
		{ID: "prompt/1105-143536", Words: "the evals pick", CheckKind: "predicate", CheckRef: "answers:pins unplanned behavior", PRs: []string{"Forge-AI/monorepo#51511"}},
		{ID: "pick/unscoped", Words: "an older pick", CheckKind: "predicate", CheckRef: "answers:pins unplanned behavior"},
	}
	finding := Finding{"plan:81", "infra/rows/tunnel/forge-tunnel-forwarder.test.ts:78 pins unplanned behavior: no plan row names this Role/RoleBinding contract"}
	open, settled := Settle([]Finding{finding}, picks, "Forge-AI/monorepo#51773")
	if !reflect.DeepEqual(open, []Finding{finding}) || len(settled) != 0 {
		t.Fatalf("open = %+v, settled = %+v", open, settled)
	}
	if _, settled := Settle([]Finding{finding}, picks, "Forge-AI/monorepo#51511"); len(settled) != 1 || settled[0].Rule != "prompt/1105-143536" {
		t.Fatalf("on its own PR, settled = %+v", settled)
	}
}
