package board

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	rowsRepo = "o/r"
	rowBody  = "## Context\n\nWhy.\n\n## Summary\n\nChats open faster.\n\n## Motivation\n\nPlan step 7 lists it.\n\n## Risk profile\n\nLow.\n\n## Design\n\nHow.\n"
)

func rowFacts(number int, change func(*machine.Facts)) machine.Facts {
	f := machine.Facts{Repo: rowsRepo, Number: number, Title: "a change", Head: "aaaaaaaaaa", Lane: "lane-7", Branch: "b", CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass, Body: rowBody}
	if change != nil {
		change(&f)
	}
	return f
}

func written(t *testing.T, s *store.Store, snapshot ...machine.Facts) map[int]store.ReviewRow {
	t.Helper()
	if err := WriteReviewRows(s, rowsRepo, snapshot, time.Date(2026, 11, 4, 9, 40, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	rows, err := s.ReviewRows(rowsRepo)
	if err != nil {
		t.Fatal(err)
	}
	found := make(map[int]store.ReviewRow, len(rows))
	for _, row := range rows {
		if _, again := found[row.Number]; again {
			t.Fatalf("#%d twice: %+v", row.Number, rows)
		}
		found[row.Number] = row
	}
	return found
}

func TestEveryOpenDrivePRHasOneRowSayingWhatItWaitsOn(t *testing.T) {
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	rows := written(t, s,
		rowFacts(1, nil),
		rowFacts(2, func(f *machine.Facts) { f.CI = machine.CIFailure; f.Hold = "root" }),
		rowFacts(3, func(f *machine.Facts) { f.Tap = machine.TapTapped }),
		rowFacts(4, func(f *machine.Facts) { f.Lane = ""; f.ForeignAuthor = "someone" }),
		rowFacts(5, func(f *machine.Facts) { f.Merged = true }),
		rowFacts(6, func(f *machine.Facts) { f.Closed = true }),
	)
	if len(rows) != 3 {
		t.Fatalf("rows = %+v, want #1, #2 and #3 only: another person's, merged and closed PRs draw none", rows)
	}
	if row := rows[1]; !row.Ready || row.Waits != onlyHim || row.Title != "a change" || row.Head != "aaaaaaaaaa" {
		t.Fatalf("#1 = %+v, want a ready row at its head", row)
	}
	if row := rows[2]; row.Ready || !strings.Contains(row.Waits, "CI is not green") || !strings.Contains(row.Waits, "held by root") {
		t.Fatalf("#2 = %+v, want its red build and its hold said on the row", row)
	}
	if row := rows[3]; !row.Tapped {
		t.Fatalf("#3 = %+v, want its tap on the row", row)
	}
}

func TestAMergedPRsRowGoesOnTheNextWriteAndAMovedHeadFollows(t *testing.T) {
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	written(t, s, rowFacts(1, nil), rowFacts(2, nil))
	rows := written(t, s, rowFacts(1, func(f *machine.Facts) { f.Head = "bbbbbbbbbb" }), rowFacts(2, func(f *machine.Facts) { f.Merged = true }))
	if _, kept := rows[2]; kept || rows[1].Head != "bbbbbbbbbb" {
		t.Fatalf("rows = %+v, want #2 gone and #1 at its new head", rows)
	}
}

func TestAParentThatCannotLandIsSaidOnTheRowAbove(t *testing.T) {
	below := rowFacts(1, func(f *machine.Facts) { f.Branch = "below"; f.Title = "the base"; f.CI = machine.CIFailure })
	above := rowFacts(2, func(f *machine.Facts) { f.Base = "below" })
	for _, row := range ReviewRows([]machine.Facts{below, above}) {
		if row.Number == 2 && (row.Ready || !strings.Contains(row.Waits, "the change below it, the base")) {
			t.Fatalf("#2 = %+v, want it waiting on its parent", row)
		}
	}
}

func TestAPRWithdrawnAtItsHeadLeavesTheTabAndANewHeadBringsItBack(t *testing.T) {
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if _, err := s.PutFact(rowsRepo, 1, "aaaaaaaaaa", FactWithdrawn, "replaced by a newer change", time.Date(2026, 11, 4, 9, 0, 0, 0, time.UTC)); err != nil {
		t.Fatal(err)
	}
	if rows := written(t, s, rowFacts(1, nil), rowFacts(2, nil)); len(rows) != 1 || rows[2].Number != 2 {
		t.Fatalf("rows = %+v, want only #2 while #1 is withdrawn at its head", rows)
	}
	if rows := written(t, s, rowFacts(1, func(f *machine.Facts) { f.Head = "bbbbbbbbbb" })); rows[1].Head != "bbbbbbbbbb" {
		t.Fatalf("rows = %+v, want #1 back at its new head", rows)
	}
}

func TestARowCarriesEveryCardFieldFromTheBodyAndTheFacts(t *testing.T) {
	row := ReviewRows([]machine.Facts{rowFacts(1, nil)})[0]
	if row.ForUsers != "Chats open faster." || row.Risk != "Low." || !strings.HasPrefix(row.Plan, "Plan step 7 lists it.") {
		t.Fatalf("row = %+v, want Summary, Risk profile and the plan line, each stopping at the next heading", row)
	}
	if !strings.Contains(row.Review, "CI green; approved; graded clear; mergeable: clean") {
		t.Fatalf("review = %q, want each check in words", row.Review)
	}
}

func TestARowWithAnEmptyCardFieldIsNeverReady(t *testing.T) {
	for name, body := range map[string]string{
		"no summary": "## Risk profile\n\nLow.\n",
		"no risk":    "## Summary\n\nChats open faster.\n",
		"no body":    "",
	} {
		row := ReviewRows([]machine.Facts{rowFacts(1, func(f *machine.Facts) { f.Body = body })})[0]
		if row.Ready || !strings.Contains(row.Waits, "its PR body has no") {
			t.Fatalf("%s: row = %+v, want it not ready and saying which field is missing", name, row)
		}
	}
}
