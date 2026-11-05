package update

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func awayInputs(t *testing.T) Inputs {
	t.Helper()
	in := inputs(t)
	in.Before.At = ago(8 * 60)
	release := heard("release-6", "desired", 5)
	in.Lanes = append(in.Lanes, release, heard("dd-334-fix", "desired", 5), heard("orphan-1", "desired", 5))
	in.PRRows = []store.ReviewRow{
		{Repo: "o/r", Number: 48401, Lane: "ship-2", Title: "journal readers move to the sealed format", ForUsers: "old chats open on every Mac", State: "landed", LandedAt: store.Stamp(ago(5 * 60))},
		{Repo: "o/r", Number: 48402, Lane: "ship-2", Title: "the journal seals on close", State: "landed", LandedAt: store.Stamp(ago(3 * 60))},
		{Repo: "o/r", Number: 48403, Lane: "ship-2", Title: "the journal drops a stale reader", State: "landed", LandedAt: store.Stamp(ago(30))},
		{Repo: "o/r", Number: 48399, Lane: "ship-2", Title: "landed before he left", State: "landed", LandedAt: store.Stamp(ago(9 * 60))},
		{Repo: "o/r", Number: 48404, Lane: "orphan-1", Title: "the board counts taps", State: "open", CI: "red"},
		{Repo: "o/r", Number: 48405, Lane: "release-6", Title: "the release reads its plan", State: "open", ReadyForTap: true},
		{Repo: "o/r", Number: 48406, Lane: "orphan-1", Title: "the board drops a stale tap", State: "open", CI: "red", Hold: "root"},
	}
	in.All = []Record{
		{Seq: 40, Drive: testDrive, Lane: "supervisor", Kind: "state", Topic: lanes.NotLiveTopic, Text: "LANDED-NOT-LIVE sand-next: #48401, #48399", At: ago(6 * 60)},
		{Seq: 41, Drive: testDrive, Lane: "supervisor", Kind: "state", Topic: lanes.NotLiveTopic, Text: "LANDED-NOT-LIVE sand-next: #48401", At: ago(2 * 60)},
		{Seq: 42, Drive: testDrive, Lane: "dd-334-fix", Kind: "incident", Status: statusOpen, Text: "INCIDENT OPEN dd-334: run assignment has no worker. On it: dd-334-fix", At: ago(60), To: []string{root}},
	}
	in.Cards = []board.Wait{
		{Kind: "pick", Block: "card-ship-seal", Subject: "seal", Title: "When does ship seal a journal?", Since: "3:10 AM", URL: "https://slack.example/p1", Options: []string{"On close (recommended)", "On idle"}},
		{Kind: "tap", PR: "48405", Since: "4:00 AM", Title: "the release reads its plan"},
	}
	in.Streams = []Workstream{
		{Name: "Sealed journal", Delivers: "every reader on the sealed format", Stems: []string{"ship"}},
		{Name: "Releases", Delivers: "every change released", Stems: []string{"release"}},
		{Name: "Incidents", Delivers: "every open alert with a worker on it", Stems: []string{"dd"}},
	}
	return in
}

func awaySection(t *testing.T, text, title string) string {
	t.Helper()
	_, after, found := strings.Cut(text, "\n## "+title+"\n")
	if !found {
		t.Fatalf("the away update has no %q section:\n%s", title, text)
	}
	section, _, _ := strings.Cut(after, "\n## ")
	return section
}

func TestTheAwayUpdateSaysByWorkstreamWhatLandedIsLiveWaitsOnHimAndBroke(t *testing.T) {
	text := Away(awayInputs(t))
	if !strings.HasPrefix(text, "# While you were away: "+pacific(ago(8*60))+" to "+pacific(now)+"\n\nWaits on you: 2. Landed: 3, live or releasing now: 1. Broke: 3. Plan steps with no owner: 1.\n") {
		t.Fatalf("headline:\n%s", text)
	}
	waits := awaySection(t, text, waitsTitle)
	for _, want := range []string{
		"### Sealed journal\n- When does ship seal a journal?\n  Buttons: On close (recommended) / On idle. Answer in Slack: https://slack.example/p1.\n",
		"### Releases\n- Approve [the release reads its plan](https://github.com/o/r/pull/48405), ready since 4:00 AM",
	} {
		if !strings.Contains(waits, want) {
			t.Errorf("waits lack %q:\n%s", want, waits)
		}
	}
	journal := awaySection(t, text, "Sealed journal")
	for _, want := range []string{
		"On it: ship\n",
		"Landed (3):\n- [journal readers move to the sealed format](https://github.com/o/r/pull/48401); for users: old chats open on every Mac, landed " + pacific(ago(5*60)) + ": not live yet on sand-next\n",
		"- [the journal seals on close](https://github.com/o/r/pull/48402), landed " + pacific(ago(3*60)) + ": live or releasing now\n",
		"- [the journal drops a stale reader](https://github.com/o/r/pull/48403), landed " + pacific(ago(30)) + ": landed; the release check reads it at " + pacific(ago(30).Add(lanes.NotLiveOwed+lanes.NotLiveEvery)),
	} {
		if !strings.Contains(journal, want) {
			t.Errorf("journal lacks %q:\n%s", want, journal)
		}
	}
	if strings.Contains(text, "landed before he left") {
		t.Errorf("a change that landed before he left is in:\n%s", text)
	}
	if incidents := awaySection(t, text, "Incidents"); !strings.Contains(incidents, "Broke (1):\n- Incident dd-334, fired "+pacific(ago(60))+": open since "+pacific(ago(60))+", cause not found yet; on it: dd-334-fix") {
		t.Errorf("incidents:\n%s", incidents)
	}
	if rest := awaySection(t, text, elseFeature); !strings.Contains(rest, "- [the board counts taps](https://github.com/o/r/pull/48404): its head is red; owner orphan") || !strings.Contains(rest, "- [the board drops a stale tap](https://github.com/o/r/pull/48406): its head is red; owner orphan") {
		t.Errorf("everything else:\n%s", rest)
	}
	plan := awaySection(t, text, planTitle)
	if !strings.Contains(plan, "- The readers: 0 of 1 tasks done. Owner: ship\n") || !strings.Contains(plan, "- The writers: 0 of 1 tasks done. Owner: **nobody**; no lane's brief has named it") || !strings.Contains(plan, "- Done: The base") {
		t.Errorf("plan:\n%s", plan)
	}
}

func TestTheAwayUpdateReadsEachSourceAsOftenAtOneItemAsAtMany(t *testing.T) {
	calls := func(n int) (map[string]int, string) {
		counted := map[string]int{}
		registry := make([]Lane, 0, n)
		rows := make([]store.ReviewRow, 0, n)
		cards := make([]board.Wait, 0, n)
		steps := make([]*plans.Step, 0, n)
		for i := range n {
			lane := onStepLane(fmt.Sprintf("ship-%d", i+1), "desired", fmt.Sprint(i), 5)
			lane.PRs = []int{50000 + i}
			registry = append(registry, lane)
			rows = append(rows, store.ReviewRow{Repo: "o/r", Number: 50000 + i, Lane: lane.Name, Title: fmt.Sprintf("change %d", i), State: "landed", LandedAt: store.Stamp(ago(90))})
			cards = append(cards, board.Wait{Kind: "pick", Block: fmt.Sprintf("card-%d", i), Title: fmt.Sprintf("pick %d", i), Options: []string{"yes", "no"}})
			steps = append(steps, &plans.Step{Number: fmt.Sprint(i), Title: fmt.Sprintf("%d. Piece %d", i, i), Tasks: []plans.Task{{Title: "x", State: plans.Open}}})
		}
		sources := src(registry, nil, nil, nil)
		count := func(name string) { counted[name]++ }
		lanesOf, recordsOf := sources.Lanes, sources.Records
		sources.Lanes = func(ctx context.Context) ([]Lane, error) { count("lanes"); return lanesOf(ctx) }
		sources.Records = func(ctx context.Context, since time.Time, kinds ...string) ([]Record, error) {
			count("records")
			return recordsOf(ctx, since, kinds...)
		}
		sources.Plans = func(context.Context) ([]*plans.Plan, error) {
			count("plans")
			return []*plans.Plan{{ID: "abc1234", Title: "The rebuild", Status: "approved", Steps: steps}}, nil
		}
		sources.PRRows = func() ([]store.ReviewRow, error) { count("rows"); return rows, nil }
		sources.Board = func(context.Context, []board.Snapshot) (board.Live, error) {
			count("board")
			return board.Live{Picks: cards}, nil
		}
		sources.Book = func(context.Context) (asks.Book, error) {
			count("book")
			return asks.Book{PRs: map[string]asks.PR{}}, nil
		}
		sources.Before = func() (Snapshot, error) { return Snapshot{At: ago(120)}, nil }
		in, err := Collect(context.Background(), sources, testDrive, now)
		if err != nil {
			t.Fatal(err)
		}
		return counted, Away(in)
	}
	many := scaletest.N()
	one, _ := calls(1)
	at, text := calls(many)
	if fmt.Sprint(one) != fmt.Sprint(at) {
		t.Fatalf("source reads at 1 item = %v, at %d = %v", one, many, at)
	}
	last := many - 1
	for _, want := range []string{
		fmt.Sprintf("Waits on you: %d. Landed: %d,", many, many),
		fmt.Sprintf("[change %d](https://github.com/o/r/pull/%d)", last, 50000+last),
		fmt.Sprintf("- pick %d\n  Buttons: yes / no.", last),
		fmt.Sprintf("- Piece %d: 0 of 1 tasks done. Owner: ship", last),
	} {
		if !strings.Contains(text, want) {
			t.Errorf("the update at %d lacks %q", many, want)
		}
	}
}

func TestSinceReadsAStampADurationOrThePacificClockBeforeNow(t *testing.T) {
	for given, want := range map[string]time.Time{
		"2026-11-02T03:00:00Z": time.Date(2026, 11, 2, 3, 0, 0, 0, time.UTC),
		"8h":                   now.Add(-8 * time.Hour),
		"10:30 am":             time.Date(2026, 11, 2, 10, 30, 0, 0, pacificZone),
		"11:30 PM":             time.Date(2026, 11, 1, 23, 30, 0, 0, pacificZone),
	} {
		got, err := sinceOf(given, now)
		if err != nil || !got.Equal(want) {
			t.Errorf("since %q = %v, %v; want %v", given, got, err, want)
		}
	}
	if _, err := sinceOf("last night", now); err == nil {
		t.Error("since read words it cannot place")
	}
}
