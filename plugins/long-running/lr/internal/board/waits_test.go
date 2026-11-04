package board

import (
	"reflect"
	"strconv"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestAPRWaitsOnHisTapOnlyWhenItsRecordSaysReadyElseItNamesWhatHoldsIt(t *testing.T) {
	pr := strconv.Itoa(number)
	ready := map[int]store.ReviewRow{number: {Number: number, Head: head, ReadyForTap: true}}
	unready := map[int]store.ReviewRow{number: {Number: number, Head: head, WaitsOn: "fidelity grade missing at head"}}
	cases := []struct {
		name    string
		live    Live
		rows    map[int]store.ReviewRow
		change  func(*machine.Facts)
		waiting bool
		line    string
	}{
		{
			name:    "a ready row with no tap",
			rows:    ready,
			waiting: true,
			line:    "#45583 at e718a74: waits on your tap on the review tab",
		},
		{
			name: "approved at its head",
			live: Live{Taps: map[string]Verdict{pr: {verdictApproved, head, "2026-11-02T19:00:00Z"}}},
			rows: map[int]store.ReviewRow{number: {Number: number, Head: head, Tapped: true}},
			line: "#45583 at e718a74: not waiting on you: you approved it at e718a74 at 11:00 AM",
		},
		{
			name: "rejected at an older head, its row not ready",
			live: Live{Taps: map[string]Verdict{pr: {verdictRejected, moved, ""}}},
			rows: unready,
			line: "#45583 at e718a74: not waiting on you: its row in lr board rows waits on: fidelity grade missing at head; you rejected it at aa11bb2, an older head",
		},
		{
			name:   "a land order from a card on a row that still reads ready",
			rows:   ready,
			change: func(f *machine.Facts) { f.Tap = machine.TapTapped },
			line:   "#45583 at e718a74: not waiting on you: a land order stands at e718a74",
		},
		{
			name:   "no row and a failing gate",
			change: func(f *machine.Facts) { f.CI = machine.CIFailure },
			line:   "#45583 at e718a74: not waiting on you: it has no row in lr board rows; CI is not green",
		},
		{
			name: "a merge-prs row on the board with no ready record never waits",
			live: Live{Rows: []MergeRow{{MergeBlock, pr, head}}},
			rows: unready,
			line: "#45583 at e718a74: not waiting on you: its row in lr board rows waits on: fidelity grade missing at head",
		},
	}
	for _, c := range cases {
		got := c.live.Explain(number, facts(c.change), c.rows)
		want := PRWait{number, head, c.waiting, c.line}
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s = %+v", c.name, got)
		}
	}
}

func TestAPRThatIsNotOpenWaitsOnNobody(t *testing.T) {
	got := Live{}.Explain(7, nil, nil)
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

	got := Live{}.Explain(number, snapshot, nil)

	want := "#45583 at e718a74: not waiting on you: it has no row in lr board rows; the PR below it, #7, cannot land yet: CI is not green"
	if got.Line != want {
		t.Fatalf("line = %q", got.Line)
	}
}
