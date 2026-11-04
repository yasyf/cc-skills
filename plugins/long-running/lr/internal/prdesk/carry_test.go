package prdesk

import (
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const h3 = "3333333333333333333333333333333333333333"

func green(c *clock, minute, number int, branch, base, head, change string) []Event {
	return []Event{
		PullRequest{c.at(minute), number, branch, base, head, true, false, change},
		Check{c.at(minute), head, "ci", "success", int64(minute) + 1},
		Grade{c.at(minute), number, head, "finder", 1, string(machine.FinderClear)},
		Grade{c.at(minute), number, head, "fidelity", 1, string(machine.FidelityPass)},
		Review{c.at(minute), number, "bot", "approved", head},
	}
}

func tapped(c *clock, minute, number int, head string, verdict machine.Tap) Event {
	return Tap{c.at(minute), number, head, verdict}
}

func judged(c *clock, minute, number int, head, since string, same bool, what string) Event {
	return Behavior{c.at(minute), number, head, since, same, what}
}

func carries(t *testing.T, d Desk, number int) []store.Carry {
	t.Helper()
	var found []store.Carry
	if err := d.Store.Update(func(tx *store.Tx) (err error) {
		found, err = tx.Carries(repo, number)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	return found
}

func TestASameDiffHeadKeepsItsTapAndGradesWithOneSameDiffCarry(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(green(c, 0, 1, "lane/a", landing, h1, "c1"), tapped(c, 1, 1, h1, machine.TapTapped))...)
	apply(t, d, PullRequest{c.at(2), 1, "lane/a", landing, h2, true, false, "c1"}, Check{c.at(2), h2, "ci", "success", 9}, Review{c.at(2), 1, "bot", "approved", h2})
	expect(t, d, 1, machine.StateReady)
	if got := carries(t, d, 1); len(got) != 1 || got[0].Proof != store.ProofSameDiff || got[0].FromHead != h1 || got[0].ToHead != h2 {
		t.Fatalf("carries = %+v, want one same-diff carry from the tapped head", got)
	}
}

func TestAFidelityJudgmentCarriesATapAcrossANewDiffOnlyWhenBehaviorIsTheSame(t *testing.T) {
	for _, tc := range []struct {
		name      string
		same      bool
		fidelity  string
		want      machine.State
		reason    string
		carriedBy store.Proof
	}{
		{"the same behavior carries the tap", true, string(machine.FidelityPass), machine.StateReady, "every gate passes", store.ProofFidelity},
		{"changed behavior asks again and says what changed", false, string(machine.FidelityPass), machine.StateAwaitingTap, "clean, green and approved, with no tap on this change; since the tap: rows are now dropped", ""},
		{"the same behavior with a failed plan check carries nothing", true, string(machine.FidelityFail), machine.StateAppeal, "a finding is open and not yet judged", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d, c := memory(t), &clock{}
			apply(t, d, append(green(c, 0, 1, "lane/a", landing, h1, "c1"), tapped(c, 1, 1, h1, machine.TapTapped))...)
			moved := green(c, 2, 1, "lane/a", landing, h2, "c2")
			moved[3] = Grade{c.at(2), 1, h2, "fidelity", 1, tc.fidelity}
			apply(t, d, moved...)
			expect(t, d, 1, machine.StateGrading)
			apply(t, d, judged(c, 3, 1, h2, h1, tc.same, "rows are now dropped"))
			if got := state(t, d, 1); got.State != tc.want || got.Reason != tc.reason {
				t.Fatalf("#1 is %s (%s), want %s (%s)", got.State, got.Reason, tc.want, tc.reason)
			}
			got := carries(t, d, 1)
			if tc.carriedBy == "" && len(got) != 0 || tc.carriedBy != "" && (len(got) != 1 || got[0].Proof != tc.carriedBy || got[0].Grade == "") {
				t.Fatalf("carries = %+v, want proof %q", got, tc.carriedBy)
			}
		})
	}
}

func TestSmallChangesAreJudgedAgainstTheTappedHeadNeverACarriedOne(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(green(c, 0, 1, "lane/a", landing, h1, "c1"), tapped(c, 1, 1, h1, machine.TapTapped))...)
	apply(t, d, append(green(c, 2, 1, "lane/a", landing, h2, "c2"), judged(c, 2, 1, h2, h1, true, ""))...)
	expect(t, d, 1, machine.StateReady)
	apply(t, d, append(green(c, 3, 1, "lane/a", landing, h3, "c3"), judged(c, 3, 1, h3, h2, true, ""))...)
	expect(t, d, 1, machine.StateAwaitingTap)
}

func TestARefusalCarriesToASameDiffHeadWithNoCard(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(green(c, 0, 1, "lane/a", landing, h1, "c1"), tapped(c, 1, 1, h1, machine.TapRejected))...)
	apply(t, d, green(c, 2, 1, "lane/a", landing, h2, "c1")...)
	expect(t, d, 1, machine.StateRejected)
}

func TestAReplacementPRWithTheSameDiffInheritsTheTap(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(green(c, 0, 1, "lane/a", landing, h1, "c1"), tapped(c, 1, 1, h1, machine.TapTapped))...)
	apply(t, d, PullRequest{c.at(2), 1, "lane/a", landing, h1, false, false, ""})
	apply(t, d, green(c, 3, 2, "lane/a2", landing, h2, "c1")...)
	expect(t, d, 2, machine.StateReady)
	if got := carries(t, d, 2); len(got) != 1 || got[0].FromNumber != 1 {
		t.Fatalf("carries = %+v, want one from #1", got)
	}
}

func TestAChildRetargetedWithNoPushRegradesAndCarriesNothing(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(green(c, 0, 1, "lane/a", landing, h1, "c1"), tapped(c, 1, 1, h1, machine.TapTapped))...)
	apply(t, d, append(green(c, 1, 2, "lane/b", "lane/a", h2, "c2"), tapped(c, 1, 2, h2, machine.TapTapped))...)
	expect(t, d, 2, machine.StateReady)
	apply(t, d, PullRequest{c.at(2), 2, "lane/b", landing, h2, true, false, "c2-with-a"})
	expect(t, d, 2, machine.StateGrading)
}

func TestATapOnACardForAHeadSincePushedCountsWhenTheChangeIsTheSame(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, green(c, 0, 1, "lane/a", landing, h1, "c1")...)
	apply(t, d, PullRequest{c.at(1), 1, "lane/a", landing, h2, true, false, "c1"}, Check{c.at(1), h2, "ci", "success", 5}, Review{c.at(1), 1, "bot", "approved", h2})
	expect(t, d, 1, machine.StateAwaitingTap)
	apply(t, d, tapped(c, 2, 1, h1, machine.TapTapped))
	if got := state(t, d, 1); got.State != machine.StateReady || !strings.HasPrefix(got.Reason, "every gate passes") {
		t.Fatalf("#1 is %s (%s)", got.State, got.Reason)
	}
}

func TestAGradingPRSaysWhichGradeItWaitsOn(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, PullRequest{c.at(0), 1, "lane/a", landing, h1, true, false, "c1"})
	if got := state(t, d, 1).Reason; got != "this head waits on its finder grade and its fidelity grade" {
		t.Fatalf("an ungraded head says %q", got)
	}
	apply(t, d, append(green(c, 0, 1, "lane/a", landing, h1, "c1"), tapped(c, 1, 1, h1, machine.TapTapped))...)
	apply(t, d, green(c, 2, 1, "lane/a", landing, h2, "c2")...)
	if got := state(t, d, 1); got.State != machine.StateGrading || got.Reason != "this head waits on the fidelity judgment of its behavior since the tapped change" {
		t.Fatalf("a graded new diff after a tap is %s, %q", got.State, got.Reason)
	}
}

func TestATapAndASettleNamingAShortHeadCountForThatHeadsChange(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, tapped(c, 0, 1, h1[:12], machine.TapTapped))
	apply(t, d, green(c, 1, 1, "lane/a", landing, h1, "c1")...)
	if got := state(t, d, 1); got.State != machine.StateReady {
		t.Fatalf("a tap recorded by short head before the diff hash is %s (%s), want ready", got.State, got.Reason)
	}
	apply(t, d, Grade{c.at(2), 1, h1, "finder", 2, string(machine.FinderAsk)})
	expect(t, d, 1, machine.StateAppeal)
	apply(t, d, Grade{c.at(3), 1, h1[:12], "appeal", 0, "settle"})
	expect(t, d, 1, machine.StateReady)
}
