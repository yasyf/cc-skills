package asks

import (
	"reflect"
	"testing"
	"time"
)

var now = time.Date(2026, 11, 2, 19, 0, 0, 0, time.UTC)

func stamp(ago time.Duration) string {
	return now.Add(-ago).Format(time.RFC3339)
}

func ask(fields map[string]string) map[string]string {
	row := map[string]string{"lane": "lane-1", "text": "measure idle", "asked_at": stamp(5 * time.Minute)}
	for name, value := range fields {
		row[name] = value
	}
	return row
}

func read(t *testing.T, rows map[string]map[string]string) Book {
	t.Helper()
	book, err := Read(rows)
	if err != nil {
		t.Fatal(err)
	}
	return book
}

func TestAnAskGradesFromItsLinkedPRsItsProgressAndItsAge(t *testing.T) {
	landed := map[string]string{"state": "landed", "landed_at": stamp(time.Hour)}
	cases := []struct {
		name string
		rows map[string]map[string]string
		want State
	}{
		{"too fresh to grade", map[string]map[string]string{"ask/000001": ask(nil)}, Fresh},
		{"dropped outranks every other fact", map[string]map[string]string{"ask/000001": ask(map[string]string{"dropped_at": stamp(0), "answered_at": stamp(0)})}, Dropped},
		{"answered", map[string]map[string]string{"ask/000001": ask(map[string]string{"answered_at": stamp(0)})}, Answered},
		{"an override outranks the PRs", map[string]map[string]string{"ask/000001": ask(map[string]string{"state": "backlog", "prs": "7"}), "7": landed}, Backlog},
		{"no PR and no record past 30 minutes", map[string]map[string]string{"ask/000001": ask(map[string]string{"asked_at": stamp(30 * time.Minute)})}, Lost},
		{"progress holds it in progress", map[string]map[string]string{"ask/000001": ask(map[string]string{"asked_at": stamp(3 * time.Hour), "progress_000001": `{"at":"` + stamp(time.Minute) + `","note":"n","artifacts":[]}`})}, InProgress},
		{"the newest progress row decides", map[string]map[string]string{"ask/000001": ask(map[string]string{"asked_at": stamp(3 * time.Hour), "progress_000001": `{"at":"` + stamp(time.Minute) + `","note":"n","artifacts":[]}`, "progress_000002": `{"at":"` + stamp(time.Hour) + `","note":"n","artifacts":[]}`})}, Lost},
		{"standing within its hours", map[string]map[string]string{"ask/000001": ask(map[string]string{"standing_hours": "2", "asked_at": stamp(time.Hour)})}, InProgress},
		{"standing past its hours", map[string]map[string]string{"ask/000001": ask(map[string]string{"standing_hours": "2", "asked_at": stamp(2 * time.Hour)})}, Stale},
		{"an open PR", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "7"}), "7": {"head": "abc"}}, InPR},
		{"a linked PR with no row yet reads as open", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "7"})}, InPR},
		{"landed with no live row", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "7"}), "7": landed}, LandedNotLive},
		{"landed after the live release", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "7"}), "7": landed, "live": {"at": stamp(2 * time.Hour)}}, LandedNotLive},
		{"live once the release covers its landing", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "7"}), "7": landed, "live": {"at": stamp(time.Minute)}}, Live},
		{"one PR not yet landed holds it", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "7,8"}), "7": landed, "8": {"head": "abc"}}, InPR},
		{"another repository's PR row counts", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "org/x#7"}), "org/x#7": landed}, LandedNotLive},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			book := read(t, c.rows)
			if got := book.State(book.Asks[0], now); got != c.want {
				t.Fatalf("state = %s, want %s", got, c.want)
			}
		})
	}
}

func TestOpenSkipsClosedAsksAndLeadsWithLandedNotLive(t *testing.T) {
	book := read(t, map[string]map[string]string{
		"ask/000001": ask(map[string]string{"answered_at": stamp(0)}),
		"ask/000002": ask(nil),
		"ask/000003": ask(map[string]string{"prs": "7"}),
		"7":          {"state": "landed", "landed_at": stamp(time.Hour)},
	})
	open := book.Open(now)
	keys := make([]string, len(open))
	for i, graded := range open {
		keys[i] = graded.Key
	}
	if want := []string{"ask/000003", "ask/000002"}; !reflect.DeepEqual(keys, want) {
		t.Fatalf("keys = %v", keys)
	}
}

func TestAMalformedKeyIsNoRowAndABadStampNamesItsRow(t *testing.T) {
	book := read(t, map[string]map[string]string{"ask/abc": {"lane": "x"}, "12x": {}, "note": {}})
	if len(book.Asks) != 0 || len(book.PRs) != 0 {
		t.Fatalf("book = %+v", book)
	}
	if _, err := Read(map[string]map[string]string{"ask/000001": {"asked_at": "yesterday"}}); err == nil {
		t.Fatal("a bad stamp read")
	}
}

func TestAnAskLineNamesItsStandingHours(t *testing.T) {
	book := read(t, map[string]map[string]string{"ask/000001": ask(map[string]string{"standing_hours": "2"})})
	if line := book.Asks[0].Line(); line != "ask/000001 lane-1 standing 2h: measure idle" {
		t.Fatalf("line = %q", line)
	}
}

func TestAPRWaitsForTheFirstReasonItsRowHolds(t *testing.T) {
	cases := []struct {
		name string
		row  map[string]string
		want string
	}{
		{"untracked", map[string]string{"head": "a"}, ""},
		{"closed", map[string]string{"state": "landed", "reported_head": "a"}, ""},
		{"labelled", map[string]string{"reported_head": "a", "labels": "merge,x"}, ""},
		{"label on its head", map[string]string{"reported_head": "a", "head": "a", "label_head": "a", "labelled_at": "t"}, ""},
		{"label pulled", map[string]string{"reported_head": "a", "head": "a", "label_head": "a", "labelled_at": "t", "label_pulled_at": "t"}, "ungraded"},
		{"hold", map[string]string{"reported_head": "a", "hold_until": "t", "test_state": "failure"}, "held"},
		{"held by its lane", map[string]string{"reported_head": "a", "reported_verdict": "held"}, "held"},
		{"a lane hold on an older head", map[string]string{"head": "b", "reported_head": "a", "reported_verdict": "held"}, "ungraded"},
		{"red", map[string]string{"registered": "1", "test_state": "failure"}, "red"},
		{"conflict", map[string]string{"registered": "1", "mergeable_state": "dirty"}, "red"},
		{"refused", map[string]string{"reported_head": "a", "label_refused_head": "a"}, "refused"},
		{"ungraded", map[string]string{"reported_head": "a"}, "ungraded"},
		{"no head and no refusal", map[string]string{"registered": "1"}, "ungraded"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			book := read(t, map[string]map[string]string{"7": c.row})
			if got := book.PRs["7"].WaitingReason(); got != c.want {
				t.Fatalf("reason = %q, want %q", got, c.want)
			}
		})
	}
}

func TestOpenPRsSortByRepositoryThenNumber(t *testing.T) {
	book := read(t, map[string]map[string]string{"10": {}, "9": {}, "org/x#2": {}, "11": {"state": "landed"}})
	prs := book.OpenPRs()
	keys := make([]string, len(prs))
	for i, pr := range prs {
		keys[i] = pr.Key
	}
	if want := []string{"9", "10", "org/x#2"}; !reflect.DeepEqual(keys, want) {
		t.Fatalf("keys = %v", keys)
	}
}
