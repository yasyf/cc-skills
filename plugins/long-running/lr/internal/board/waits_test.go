package board

import (
	"reflect"
	"strconv"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

func TestAPRWaitsOnHisTapOnlyOnARowAtItsHeadElseItNamesWhatHoldsIt(t *testing.T) {
	pr := strconv.Itoa(number)
	cases := []struct {
		name    string
		live    Live
		change  func(*machine.Facts)
		waiting bool
		line    string
	}{
		{
			name:    "a row at its head with no verdict",
			live:    Live{Rows: []MergeRow{{MergeBlock, pr, head}}},
			waiting: true,
			line:    "#45583 at e718a74: waits on your tap on merge-prs, up since just now",
		},
		{
			name: "approved at its head",
			live: Live{Rows: []MergeRow{{MergeBlock, pr, head}}, Taps: map[string]Verdict{pr: {verdictApproved, head, "2026-11-02T19:00:00Z"}}},
			line: "#45583 at e718a74: not waiting on you: you approved it at e718a74 at 11:00 AM",
		},
		{
			name: "rejected at an older head its row still names",
			live: Live{Rows: []MergeRow{{MergeBlock, pr, moved}}, Taps: map[string]Verdict{pr: {verdictRejected, moved, ""}}},
			line: "#45583 at e718a74: not waiting on you: its board row names aa11bb2, not its head; you rejected it at aa11bb2, an older head",
		},
		{
			name:   "a land order from a card, its row not yet off the board",
			live:   Live{Rows: []MergeRow{{MergeBlock, pr, head}}},
			change: func(f *machine.Facts) { f.Tap = machine.TapTapped },
			line:   "#45583 at e718a74: not waiting on you: a land order stands at e718a74",
		},
		{
			name:   "no row and a failing gate",
			change: func(f *machine.Facts) { f.CI = machine.CIFailure },
			line:   "#45583 at e718a74: not waiting on you: the board holds no row for it; CI is not green",
		},
	}
	for _, c := range cases {
		got := c.live.Explain(number, facts(c.change))
		want := PRWait{number, head, c.waiting, c.line}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s = %+v", c.name, got)
		}
	}
}

func TestAPRThatIsNotOpenWaitsOnNobody(t *testing.T) {
	got := Live{}.Explain(7, nil)
	if want := (PRWait{PR: 7, Line: "#7: not waiting on you: it is no open PR of this drive's landing ledger"}); got != want {
		t.Fatalf("explain = %+v", got)
	}
}

func TestAHeldPRLeavesTheBoardSoARejectionPostsOneHold(t *testing.T) {
	if found := rows(t, withItem(t), facts(func(f *machine.Facts) { f.Hold = machine.MoverOwner })); len(found) != 0 {
		t.Fatalf("rows = %+v", found)
	}
}

func TestAPRAboveOneThatCannotLandNamesItAndWhy(t *testing.T) {
	snapshot := facts(func(f *machine.Facts) { f.Branch, f.Base = "top", "bottom" })
	snapshot[7] = machine.Facts{Number: 7, Head: moved, Branch: "bottom", Base: "dev", CI: machine.CIFailure, Mergeable: machine.MergeableClean, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass, Tap: machine.TapTapped}

	got := Live{}.Explain(number, snapshot)

	want := "#45583 at e718a74: not waiting on you: the board holds no row for it; the PR below it, #7, cannot land yet: CI is not green"
	if got.Line != want {
		t.Fatalf("line = %q", got.Line)
	}
}
