package facts_test

import (
	"context"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func pushedHeads(t *testing.T, h harness) map[int]facts.Pushed {
	t.Helper()
	pushed, err := h.source.AtPushedHeads(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return pushed
}

func TestARowBehindAPushReadsThePushedHeadAndNoneOfTheOldHeadsGrades(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 7, head: head2, login: "drive-bot"}})
	stale := store.ReviewRow{Repo: repo, Number: 7, Head: head, HeadAt: store.Stamp(time.Now()), State: prrow.StateOpen, CI: prrow.CIGreen, Approvers: "a-reviewer", Lane: "lane-7", Tapped: true}
	if err := h.store.PutReviewRows([]store.ReviewRow{stale}); err != nil {
		t.Fatal(err)
	}
	if _, err := h.store.PutFact(repo, 7, head, "fidelity", "pass", time.Now()); err != nil {
		t.Fatal(err)
	}

	row := pushedHeads(t, h)[7]

	if row.Facts.Head != head2 || row.RowHead != head || row.HeadAt != "" {
		t.Fatalf("row = %+v", row)
	}
	if row.Facts.Fidelity != "" || row.Facts.CI != machine.CIPending || row.Facts.Approved || row.Facts.Admitted() {
		t.Fatalf("the old head's standing carried to the push: %+v", row.Facts)
	}
}

func TestARowAtThePushedHeadKeepsItsStandingAndNamesNoLag(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 7, head: head, login: "drive-bot"}})
	at := store.Stamp(time.Now())
	if err := h.store.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: 7, Head: head[:12], HeadAt: at, State: prrow.StateOpen, CI: prrow.CIGreen, Approvers: "a-reviewer", Lane: "lane-7"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := h.store.PutFact(repo, 7, head, "finder", "clear", time.Now()); err != nil {
		t.Fatal(err)
	}

	row := pushedHeads(t, h)[7]

	if row.Facts.Head != head || row.RowHead != "" || row.HeadAt != at {
		t.Fatalf("row = %+v", row)
	}
	if row.Facts.Finder != machine.FinderClear || row.Facts.CI != machine.CISuccess || !row.Facts.Approved {
		t.Fatalf("facts = %+v", row.Facts)
	}
}

func TestAGradeOnTheBusThePassHasNotFoldedCountsAtItsHeadAndIsNotWritten(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 7, head: head, login: "drive-bot"}, {number: 8, head: head2, login: "drive-bot"}})
	review(t, "fidelity-shard-5", "fidelity: ok "+head[:7]+" checked a names b", 7)
	review(t, "fidelity-shard-5", "fidelity: ok "+head3[:7]+" checked a names b", 8)
	cursor, err := h.store.Cursor(bus.LegacyCursor)
	if err != nil {
		t.Fatal(err)
	}

	pushed := pushedHeads(t, h)

	if pushed[7].Facts.Fidelity != machine.FidelityPass {
		t.Fatalf("#7 = %+v, want the desk's ok counted before the pass folds it", pushed[7].Facts)
	}
	if pushed[8].Facts.Fidelity != "" {
		t.Fatalf("#8 = %+v, want a grade of another head ignored", pushed[8].Facts)
	}
	after, err := h.store.Cursor(bus.LegacyCursor)
	if err != nil {
		t.Fatal(err)
	}
	stored, err := h.store.Facts(repo, 7, head)
	if err != nil {
		t.Fatal(err)
	}
	if after != cursor || stored["fidelity"] != "" {
		t.Fatalf("the read wrote: cursor %d to %d, facts %v", cursor, after, stored)
	}
}

func TestAPRThatEndedSinceItsRowLeavesThePushedHeads(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 7, head: head, login: "drive-bot", state: "MERGED"}, {number: 8, head: head2, login: "drive-bot"}})

	pushed := pushedHeads(t, h)

	if _, listed := pushed[7]; listed {
		t.Fatalf("a merged PR read as open: %+v", pushed[7])
	}
	if _, listed := pushed[8]; !listed {
		t.Fatal("the open PR is gone")
	}
}

func TestPushedHeadsCostOneReadPerHundredRowsAndNoPerPRRead(t *testing.T) {
	for _, c := range []struct{ rows, reads int }{{1, 1}, {1000, 10}} {
		h := newHarness(t, nil)
		rows := make([]store.ReviewRow, 0, c.rows)
		for number := 1; number <= c.rows; number++ {
			rows = append(rows, store.ReviewRow{Repo: repo, Number: number, Head: head, State: prrow.StateOpen})
		}
		if err := h.store.PutReviewRows(rows); err != nil {
			t.Fatal(err)
		}

		pushedHeads(t, h)

		reads := 0
		for _, call := range h.runner.Calls() {
			if slices.Contains(call.Argv, "graphql") {
				reads++
			}
			if slices.Contains(call.Argv, "status") {
				t.Fatalf("a per-PR status read: %v", call.Argv)
			}
		}
		if reads != c.reads {
			t.Fatalf("%d rows took %d reads, want %d", c.rows, reads, c.reads)
		}
	}
}
