package watch

import (
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
)

func TestAnOwnerAskNoOneMovedReachesRootOnceAPacificDayAndNeverClearsUntilItIsDone(t *testing.T) {
	s, r := memory(t), &recorder{}
	asked := at(t, "2026-11-01T20:00:00Z")
	book := asks.Book{Asks: []asks.Ask{{Key: "ask/000037", Lane: "slack-bugs-1", Text: "regression tests for each bug", AskedAt: asked}}}
	sweep := func(l Ledgers, now time.Time) int {
		t.Helper()
		waits, err := Unfinished(l, now)
		if err != nil {
			t.Fatal(err)
		}
		escalated, err := Sweep(s, nil, nil, waits, now, r.post)
		if err != nil {
			t.Fatal(err)
		}
		return len(escalated)
	}

	want(t, sweep(Ledgers{Asks: book}, asked.Add(unmoved-time.Minute)), 0)
	want(t, sweep(Ledgers{Asks: book}, asked.Add(unmoved)), 1)
	want(t, sweep(Ledgers{Asks: book}, asked.Add(unmoved+time.Hour)), 0)
	want(t, r.posts[0].to[0], "root")
	want(t, r.posts[0].body.Subject, "ask/000037")
	for _, words := range []string{"owner ask, worker slack-bugs-1", "no progress since Nov 1", "regression tests", "still standing Nov 2", "lr asks progress --ask ask/000037"} {
		if !strings.Contains(r.posts[0].body.Cause, words) {
			t.Fatalf("cause %q lacks %q", r.posts[0].body.Cause, words)
		}
	}

	nextDay := at(t, "2026-11-03T08:01:00Z")
	want(t, sweep(Ledgers{Asks: book}, nextDay), 1)
	if !strings.Contains(r.posts[1].body.Cause, "still standing Nov 3") {
		t.Fatalf("cause = %q", r.posts[1].body.Cause)
	}
	open, err := s.OpenItems(UnfinishedNamespace, 0)
	if err != nil || len(open) != 1 {
		t.Fatalf("open = %+v, %v", open, err)
	}

	book.Asks[0].Answered = true
	want(t, sweep(Ledgers{Asks: book}, nextDay.Add(time.Minute)), 0)
	open, err = s.OpenItems(UnfinishedNamespace, 0)
	if err != nil || len(open) != 0 {
		t.Fatalf("after the answer: %+v, %v", open, err)
	}
}

func TestProgressRestartsAnAsksDayAndADuePastStallsItAtOnce(t *testing.T) {
	now := at(t, "2026-11-03T08:00:00Z")
	moved := asks.Ask{Key: "ask/1", AskedAt: now.Add(-72 * time.Hour), Newest: &asks.Progress{At: now.Add(-time.Hour)}}
	late := asks.Ask{Key: "ask/2", AskedAt: now.Add(-time.Hour), Due: now.Add(-time.Minute)}
	waits, err := Unfinished(Ledgers{Asks: asks.Book{Asks: []asks.Ask{moved, late}}}, now)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(waits), 2)
	want(t, waits[0].overdue(now), false)
	want(t, waits[1].overdue(now), true)
	if !strings.Contains(waits[1].Cause, "past due since Nov 2") || strings.Contains(waits[1].Cause, "11:59") {
		t.Fatalf("cause = %q", waits[1].Cause)
	}
}

func TestOnlyRetroRowsAndFindingsStillOpenStand(t *testing.T) {
	now := at(t, "2026-11-03T08:00:00Z")
	old := now.Add(-48 * time.Hour)
	row := func(key string, fields map[string]string) ledger.Row {
		return ledger.Row{Key: key, Fields: fields, Updated: old}
	}
	l := Ledgers{
		Retro: []ledger.Row{
			row("retro/1", map[string]string{"state": "building", "owner": "lane-a", "failure": "a stuck queue"}),
			row("retro/2", map[string]string{"state": "live"}),
			row("retro/3", map[string]string{"state": "building", "dup_of": "retro/1"}),
		},
		Findings: []ledger.Row{
			row("finding/1", map[string]string{"state": "open", "sweep": "red-sweep", "seq": "1", "text": "a red"}),
			row("finding/2", map[string]string{"state": "routed", "kind": "pick", "owner": "lane-b", "seq": "2", "text": "a pick"}),
			row("finding/3", map[string]string{"state": "fixed"}),
			row("finding/4", map[string]string{"state": "closed"}),
		},
	}

	waits, err := Unfinished(l, now)
	if err != nil {
		t.Fatal(err)
	}

	keys := make([]string, 0, len(waits))
	for _, wait := range waits {
		keys = append(keys, wait.subject())
		want(t, wait.overdue(now), true)
	}
	want(t, strings.Join(keys, " "), "retro/1 finding/1 finding/2")
	if !strings.Contains(waits[0].Cause, "retro row, state building, owner lane-a, unchanged since") {
		t.Fatalf("cause = %q", waits[0].Cause)
	}
	if !strings.Contains(waits[2].Cause, "owner pick, owner lane-b, routed since") {
		t.Fatalf("cause = %q", waits[2].Cause)
	}
}

func TestAnUnfinishedPickCarriesHisWholeAnswer(t *testing.T) {
	now := at(t, "2026-11-05T20:05:00Z")
	answer := "The new cloud runner has no release Watch: share the executor's monitors, or build its own?, A release Watch for the new cloud runner?: Share the executor's monitors, keyed by\nrunner"
	l := Ledgers{Findings: []ledger.Row{{Key: "pick/426081", Updated: now.Add(-30 * time.Hour), Fields: map[string]string{"state": "routed", "kind": "pick", "owner": "root", "seq": "426081", "text": answer}}}}

	waits, err := Unfinished(l, now)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(waits), 1)
	if whole := strings.ReplaceAll(answer, "\n", " "); !strings.Contains(waits[0].Cause, whole) {
		t.Fatalf("cause %q lacks %q", waits[0].Cause, whole)
	}
}

func TestADayOfUnfinishedRowsIsNotARepeatedDefect(t *testing.T) {
	s, r := memory(t), &recorder{}
	now := at(t, "2026-11-03T08:00:00Z")
	waits := []Wait{
		unfinishedWait("ask/1", "owner ask", "no progress", "one", "step", now.Add(-48*time.Hour), time.Time{}, now),
		unfinishedWait("ask/2", "owner ask", "no progress", "two", "step", now.Add(-48*time.Hour), time.Time{}, now),
		unfinishedWait("ask/3", "owner ask", "no progress", "three", "step", now.Add(-48*time.Hour), time.Time{}, now),
	}

	first, err := Sweep(s, nil, nil, waits, now, r.post)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Sweep(s, nil, nil, waits, now.Add(5*time.Minute), r.post)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(first), 3)
	want(t, len(second), 0)
}

func TestAPickNamingAPRThatWaitsOnHisTapDoesNotEscalateToRoot(t *testing.T) {
	now := at(t, "2026-11-03T08:00:00Z")
	row := func(key, text string) ledger.Row {
		return ledger.Row{Key: key, Updated: now.Add(-48 * time.Hour), Fields: map[string]string{"state": "routed", "kind": "pick", "owner": "root", "text": text}}
	}
	l := Ledgers{
		Findings: []ledger.Row{
			row("pick/1", "Chat tab is live; one storage pick on #49722: keep 30 days"),
			row("pick/2", "Which one on #49999?: A"),
			row("pick/3", "Which one?: A"),
		},
		Waits: []board.Wait{{Kind: "tap", PR: "49722"}, {Kind: "pick", Block: "card-a", Subject: "choice-a"}},
	}

	waits, err := Unfinished(l, now)
	if err != nil {
		t.Fatal(err)
	}

	keys := make([]string, 0, len(waits))
	for _, wait := range waits {
		keys = append(keys, wait.subject())
	}
	want(t, strings.Join(keys, " "), "pick/2 pick/3")
}
