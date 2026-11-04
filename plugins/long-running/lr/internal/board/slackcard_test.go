package board

import (
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestAnOwnerMergeRowIsTheFullCardWithMergeAndHold(t *testing.T) {
	card, ok := Card(repo, rows(t, withWords(t), facts(nil)), number)
	if !ok {
		t.Fatal("an owner merge row drew no card")
	}
	want := SlackCard{
		Status: "needs_you", Title: "Merge #45583 at e718a74?",
		Body: "*#45583*\n*What changes:* chats load faster\n*Against the plan:* nothing differed\n*Risk:* one cache key\n" +
			"*Review and CI:* every other gate is met\n*Blocks landing:* your approval",
		Refs:    []SlackRef{{URL: "https://github.com/org/repo/pull/45583", Label: "org/repo#45583"}},
		Options: []string{"Merge", "Hold"}, Recommended: "Merge", Waits: MergeWaits(number, head),
	}
	if !reflect.DeepEqual(card, want) {
		t.Fatalf("card = %+v, want %+v", card, want)
	}
}

func TestAPRWithNoOwnerRowDrawsNoCard(t *testing.T) {
	for name, found := range map[string][]Row{
		"another PR":       rows(t, withWords(t), facts(nil)),
		"already tapped":   rows(t, withWords(t), facts(func(f *machine.Facts) { f.Tap = machine.TapTapped })),
		"at an older head": rows(t, withWords(t), facts(func(f *machine.Facts) { f.Head = moved })),
	} {
		t.Run(name, func(t *testing.T) {
			pr := number
			if name == "another PR" {
				pr = number + 1
			}
			if card, ok := Card(repo, found, pr); ok {
				t.Fatalf("drew %+v", card)
			}
		})
	}
}

func TestMergeOnTheCardTapsTheRowAndHoldHoldsIt(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	if err := Respond(s, repo, hooks.hooks(), rows(t, s, facts(nil)), number, MergeWaits(number, head), "Hold", t0); err != nil {
		t.Fatal(err)
	}
	if want := []Key{{number, head}}; !reflect.DeepEqual(hooks.holds, want) || len(hooks.taps) != 0 {
		t.Fatalf("holds = %+v, taps = %+v", hooks.holds, hooks.taps)
	}
	if err := Respond(s, repo, hooks.hooks(), rows(t, s, facts(nil)), number, MergeWaits(number, head), "Merge", t0); err != nil {
		t.Fatal(err)
	}
	if want := []TapRecord{{number, head, "owner", machine.TapTapped}}; !reflect.DeepEqual(hooks.taps, want) {
		t.Fatalf("taps = %+v", hooks.taps)
	}
}

func TestACardAnswerForAMovedHeadOrAnotherLabelIsRefused(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	found := rows(t, s, facts(nil))
	for label, said := range map[string]string{"Merge": "moved to e718a7434fa0", "Ship it": `not "Ship it"`} {
		err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, moved), label, t0)
		if err == nil || !strings.Contains(err.Error(), said) {
			t.Fatalf("%s: err = %v, want it to say %q", label, err, said)
		}
	}
	if len(hooks.taps)+len(hooks.holds) != 0 {
		t.Fatalf("taps = %+v, holds = %+v", hooks.taps, hooks.holds)
	}
}

func TestABareNumberNamesEveryDrivePRHoldingItAndNoOther(t *testing.T) {
	s := withItem(t)
	for _, pr := range []store.PR{{Repo: "yasyf/cc-skills", Number: 2475}, {Repo: "yasyf/cc-slack", Number: 66}, {Repo: "Forge-AI/monorepo", Number: 66}, {Repo: "Forge-AI/monorepo", Number: 49032}} {
		if err := s.PutPR(store.PR{Repo: pr.Repo, Number: pr.Number, Head: head, Base: "main", Lane: "lane-7", State: machine.StateGrading, ReadAt: t0}); err != nil {
			t.Fatal(err)
		}
	}
	held, err := Holders(s, []int{2475, 66, 7}, []SlackPR{{"yasyf/cc-skills", 2475}})
	if err != nil {
		t.Fatal(err)
	}
	want := []SlackPR{{"yasyf/cc-skills", 2475}, {"Forge-AI/monorepo", 66}, {"yasyf/cc-slack", 66}}
	if !reflect.DeepEqual(held, want) {
		t.Fatalf("held = %+v, want %+v", held, want)
	}
}
