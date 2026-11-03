package watch

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const metaHeader = "| Item | Owner's words with time | Lane | State | PR | Blocks or next | Due |\n|---|---|---|---|---|---|---|\n"

func metaTable(rows ...string) string {
	return "# Meta items\n\n" + metaHeader + strings.Join(rows, "\n") + "\n"
}

func paged(t *testing.T, s *store.Store, r *recorder, items string, written, now time.Time) []pass.Escalated {
	t.Helper()
	escalated, err := Meta(s, items, written, now, r.post)
	if err != nil {
		t.Fatal(err)
	}
	return escalated
}

func TestAPastDueMetaRowPagesTheCommunicatorAndMetaLaneOnceWithItsNextStepAndLateness(t *testing.T) {
	s, r := memory(t), &recorder{}
	written := at(t, "2026-11-03T06:30:00Z")
	items := metaTable("| M35 | measure coordinator tokens, 10:20 PM | coordinator-tokens-1 | blocked | — | relaunch the coordinator | 11:00 PM |")

	first := paged(t, s, r, items, written, at(t, "2026-11-03T07:20:00Z"))
	second := paged(t, s, r, items, written, at(t, "2026-11-03T07:25:00Z"))

	want(t, len(first), 1)
	want(t, first[0].Subject, "meta:M35")
	for _, words := range []string{"relaunch the coordinator", "11:00 PM Nov 2", "20 minutes late"} {
		if !strings.Contains(first[0].Cause, words) {
			t.Fatalf("cause %q lacks %q", first[0].Cause, words)
		}
	}
	want(t, len(r.posts), 1)
	if !slices.Equal(r.posts[0].to, []string{"owner-relay", "meta"}) {
		t.Fatalf("to = %v", r.posts[0].to)
	}
	want(t, len(second), 0)
}

func TestAMetaRowDueLaterIsSilent(t *testing.T) {
	s, r := memory(t), &recorder{}
	written := at(t, "2026-11-03T06:30:00Z")
	items := metaTable(
		"| M36 | due later tonight | lane-b | building | #12 | CI | 11:30 PM |",
		"| M37 | due tomorrow morning | lane-c | building | #13 | review | 9:00 AM Nov 3 |",
	)

	want(t, len(paged(t, s, r, items, written, at(t, "2026-11-03T07:20:00Z"))), 0)
}

func TestAnOpenMetaRowWithNoDuePagesOnce(t *testing.T) {
	s, r := memory(t), &recorder{}
	written := at(t, "2026-11-03T06:30:00Z")
	items := metaTable(
		"| M38 | empty cell | lane-d | building | | open the PR | |",
		"| M39 | unreadable cell | lane-e | building | | open the PR | soon |",
	) + "\n## Later\n\n| Item | State | Blocks or next |\n|---|---|---|\n| M40 | queued | brief it |\n"

	first := paged(t, s, r, items, written, at(t, "2026-11-03T06:35:00Z"))
	second := paged(t, s, r, items, written, at(t, "2026-11-03T06:40:00Z"))

	want(t, len(first), 3)
	for _, page := range first {
		if !strings.Contains(page.Cause, "has no Due") {
			t.Fatalf("cause %q", page.Cause)
		}
	}
	want(t, len(second), 0)
}

func TestAMetaRowInTheDoneSectionIsNeverPaged(t *testing.T) {
	s, r := memory(t), &recorder{}
	written := at(t, "2026-11-03T06:30:00Z")
	items := metaTable() + "\n## Done\n\n" + metaHeader +
		"| M1 | merged long ago | lane-a | installed | #1 | none | 1:00 AM Nov 1 |\n" +
		"| M2 | merged, no due | lane-a | installed | #2 | none | |\n"

	want(t, len(paged(t, s, r, items, written, at(t, "2026-11-03T07:20:00Z"))), 0)
}

func TestAMetaRowWhoseDueChangesAfterItsPageReArmsWithTheNewDue(t *testing.T) {
	s, r := memory(t), &recorder{}
	written := at(t, "2026-11-03T06:30:00Z")
	before := metaTable("| M35 | measure coordinator tokens | coordinator-tokens-1 | blocked | — | relaunch the coordinator | 11:00 PM |")
	after := metaTable("| M35 | measure coordinator tokens | coordinator-tokens-1 | relaunching | — | measure | 11:30 PM |")

	want(t, len(paged(t, s, r, before, written, at(t, "2026-11-03T07:05:00Z"))), 1)
	want(t, len(paged(t, s, r, after, written, at(t, "2026-11-03T07:10:00Z"))), 0)
	again := paged(t, s, r, after, written, at(t, "2026-11-03T07:35:00Z"))

	want(t, len(again), 1)
	if !strings.Contains(again[0].Cause, "11:30 PM Nov 2") {
		t.Fatalf("cause %q", again[0].Cause)
	}
}

func TestAMetaRowWithNoDateIsDueTheDayTheTableWasWritten(t *testing.T) {
	s, r := memory(t), &recorder{}
	dir := t.TempDir()
	path := filepath.Join(dir, "meta", "items.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(metaTable("| M35 | tokens | coordinator-tokens-1 | blocked | — | relaunch | 11:00 PM |")), 0o600); err != nil {
		t.Fatal(err)
	}
	written := at(t, "2026-11-03T06:30:00Z")
	if err := os.Chtimes(path, written, written); err != nil {
		t.Fatal(err)
	}

	items, read, err := ReadMeta(dir)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(paged(t, s, r, items, read, at(t, "2026-11-04T01:00:00Z"))), 1)
}
