package board

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const rowsRepo = "o/r"

func rowFacts(number int, change func(*machine.Facts)) machine.Facts {
	f := machine.Facts{Repo: rowsRepo, Number: number, Title: "a change", Head: "aaaaaaaaaa", Lane: "lane-7", Branch: "b", CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
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
