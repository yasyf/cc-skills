package choices_test

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/choices"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	testDrive   = "d"
	boardDesk   = "board-refresher-7"
	metaDesk    = "meta-desk-2"
	mainSubject = "0c1d6f0e9b2a4c3d8e7f6a5b4c3d2e1f"
	mainSlug    = "drive-board--1a2b3c4d"
)

func post(t *testing.T, draft client.Draft) int64 {
	t.Helper()
	draft.Drive, draft.Force = testDrive, true
	record, err := client.Post(context.Background(), draft)
	if err != nil {
		t.Fatal(err)
	}
	return record.Seq
}

func newCheck(now time.Time, asks ...board.OpenAsk) *choices.Check {
	return &choices.Check{
		Drive:    testDrive,
		Asks:     func(context.Context) ([]board.OpenAsk, error) { return asks, nil },
		Desk:     func() (string, error) { return boardDesk, nil },
		Main:     func(context.Context) ([]string, error) { return []string{mainSubject, mainSlug}, nil },
		Owners:   func(lane string) ([]string, error) { return []string{lane + "-launcher", metaDesk}, nil },
		Now:      func() time.Time { return now },
		Patience: time.Hour,
	}
}

func said(t *testing.T) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Lanes: []string{lanes.Self}})
	if err != nil {
		t.Fatal(err)
	}
	return records
}

func passTwice(t *testing.T, check *choices.Check) {
	t.Helper()
	for range 2 {
		if err := check.Pass(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
}

func TestACardRecordStillOpenAfterAnHourGoesToTheBoardDeskOnce(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	seq := post(t, client.Draft{Lane: "lane-a", Kind: "ask", To: []string{boardDesk}, Text: "CARD: card-x: Which table?"})

	passTwice(t, newCheck(time.Now().Add(2*time.Hour)))

	records := said(t)
	if len(records) != 1 {
		t.Fatalf("said %d records, want one", len(records))
	}
	if !strings.Contains(records[0].Text, fmt.Sprintf("NO-CARD #%d", seq)) || records[0].Re != seq || records[0].To[0] != boardDesk {
		t.Fatalf("said %+v", records[0])
	}
}

func TestACardRecordDrawnOrYoungOrSentElsewhereSaysNothing(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	drawn := post(t, client.Draft{Lane: "lane-a", Kind: "ask", To: []string{boardDesk}, Text: "CARD: card-drawn: Which table?"})
	post(t, client.Draft{Lane: boardDesk, Kind: "answer", To: []string{"lane-a"}, Resolves: drawn, Text: "Drawn."})
	post(t, client.Draft{Lane: "lane-a", Kind: "ask", To: []string{"root"}, Text: "CARD: card-elsewhere: Which table?"})

	passTwice(t, newCheck(time.Now().Add(2*time.Hour)))
	if records := said(t); len(records) != 0 {
		t.Fatalf("said %q", records[0].Text)
	}

	post(t, client.Draft{Lane: "lane-a", Kind: "ask", To: []string{boardDesk}, Text: "CARD: card-young: Which table?"})
	passTwice(t, newCheck(time.Now().Add(30*time.Minute)))
	if records := said(t); len(records) != 0 {
		t.Fatalf("said %q", records[0].Text)
	}
}

func TestACardWaitingWithNoSlackCopyIsNamedAfterAnHourAndOneWithACopyNever(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	at := time.Now()
	check := newCheck(at, board.OpenAsk{ID: "card-unsent", Title: "Where does the table go?"}, board.OpenAsk{ID: "card-sent", Title: "Sent", Slack: true})
	check.Now = func() time.Time { return at }

	passTwice(t, check)
	if records := said(t); len(records) != 0 {
		t.Fatalf("said %q before the hour", records[0].Text)
	}

	at = at.Add(time.Hour)
	passTwice(t, check)
	records := said(t)
	if len(records) != 1 || !strings.Contains(records[0].Text, "NO-SLACK card-unsent") || records[0].To[0] != boardDesk {
		t.Fatalf("said %+v", records)
	}
}

func TestProseReadingAsAChoiceIsNamedToItsLaneOnceADay(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	first := post(t, client.Draft{Lane: "lane-a", Kind: "ask", To: []string{"root"}, Text: "One owner pick before that build: what may it reach?"})
	post(t, client.Draft{Lane: "lane-a", Kind: "report", Text: "Waiting on his pick for the reach."})
	post(t, client.Draft{Lane: "lane-b", Kind: "decide", To: []string{"root"}, Text: "A (recommended): rename the table. B: copy it."})
	post(t, client.Draft{Lane: "lane-e", Kind: "ask", To: []string{metaDesk}, Text: "Codex is not logged in here. Options: A (recommended) a Mac lane runs the pass. B: skip it."})
	post(t, client.Draft{Lane: "lane-c", Kind: "note", Text: "A (recommended): rename the table. B: copy it."})
	post(t, client.Draft{Lane: "lane-d", Kind: "ask", To: []string{"root"}, Text: "Needs his pick on the table card."})
	post(t, client.Draft{Lane: "root", Kind: "ask", To: []string{boardDesk}, Text: "Needs his pick on the reach."})

	check := newCheck(time.Now())
	passTwice(t, check)

	lanesTold := map[string]int{}
	for _, record := range said(t) {
		lanesTold[record.To[0]]++
		if record.To[0] == "lane-a" && (record.Re != first || !strings.Contains(record.Text, "UNTYPED")) {
			t.Errorf("told lane-a %+v", record)
		}
	}
	if len(lanesTold) != 2 || lanesTold["lane-a"] != 1 || lanesTold["lane-b"] != 1 {
		t.Fatalf("told %v, want lane-a and lane-b once each", lanesTold)
	}
	if listed := check.Findings(context.Background()); len(listed) != 3 {
		t.Fatalf("listed %d findings, want every hit", len(listed))
	}
}

func TestALaneBlockedOnHisPickWithNothingBuiltIsToldToBuildOnceADay(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	first := post(t, client.Draft{Lane: "lane-a", Kind: "blocked", Text: "Waits on his 14 picks on the apps board. Its doc is at 40c96f8; this lane has built and merged nothing."})
	post(t, client.Draft{Lane: "lane-a", Kind: "blocked", Text: "Still waits on his picks."})
	post(t, client.Draft{Lane: "lane-b", Kind: "blocked", Text: "Waits on the owner's pick on the card. A built at lane-b/branch."})
	post(t, client.Draft{Lane: "lane-c", Kind: "blocked", Text: "Waits on the owner's pick on the card; #48941 at 5dfaa22 is green."})
	post(t, client.Draft{Lane: "lane-d", Kind: "blocked", Text: "Green; waits on his tap on #49055."})
	post(t, client.Draft{Lane: "lane-f", Kind: "blocked", Text: "Waits on his picks. Branch lane-f/build 64c3a08, no PR until he picks."})
	post(t, client.Draft{Lane: "board-refresher-7", Kind: "blocked", Text: "Waits on his picks."})
	post(t, client.Draft{Lane: "lane-e", Kind: "ask", To: []string{"root"}, Text: "Is the build done?"})

	passTwice(t, newCheck(time.Now()))

	parked := 0
	for _, record := range said(t) {
		if !strings.Contains(record.Text, "PARKED") {
			continue
		}
		parked++
		if record.To[0] != "lane-a" || record.Re < first {
			t.Errorf("told %+v", record)
		}
	}
	if parked != 1 {
		t.Fatalf("told %d lanes, want lane-a once", parked)
	}
}

func TestAParkOnASideBoardOrAnUnsentCardIsADefectToItsLauncherAndMetaDeskOnceADay(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	side := post(t, client.Draft{Lane: "dashboard-content-1", Kind: "blocked", To: []string{"meta-desk-1", metaDesk}, Text: "Waiting on the owner's taps on board f02f306df1d09223c6ea40de1a5a8097: http://yasyf-forge-studio:61118/p/dashboard-mockups-for-your-tap--7b8a71f4 . No taps yet; the cards are not mirrored to Slack, so he only sees it once the communicator links it."})
	unsent := post(t, client.Draft{Lane: "lane-a", Kind: "blocked", Text: "Waits on his pick on card-unsent; nothing built."})
	post(t, client.Draft{Lane: "lane-b", Kind: "blocked", Text: "Waits on his pick on card-sent on board " + mainSubject + ": http://127.0.0.1:57524/p/" + mainSlug + " ; nothing built."})

	passTwice(t, newCheck(time.Now(), board.OpenAsk{ID: "card-unsent", Title: "Where does the table go?"}, board.OpenAsk{ID: "card-sent", Title: "Sent", Slack: true}))

	told := map[string]client.Record{}
	for _, record := range said(t) {
		if strings.Contains(record.Text, "OFF-BOARD") {
			told[strings.Fields(record.Text)[3]] = record
		}
	}
	if len(told) != 2 {
		t.Fatalf("told %v, want dashboard-content-1 and lane-a once each", told)
	}
	for lane, want := range map[string]struct {
		re  int64
		why string
	}{
		"dashboard-content-1": {side, "board f02f306df1d09223c6ea40de1a5a8097, dashboard-mockups-for-your-tap--7b8a71f4 is not his main board and its cards have no Slack copy"},
		"lane-a":              {unsent, "card-unsent has no Slack copy"},
	} {
		record := told[lane]
		if record.Re != want.re || !strings.Contains(record.Text, want.why) || len(record.To) != 2 || record.To[0] != lane+"-launcher" || record.To[1] != metaDesk {
			t.Errorf("told %s %+v", lane, record)
		}
	}
	for _, record := range said(t) {
		if strings.Contains(record.Text, "PARKED") && record.To[0] != "lane-b" {
			t.Errorf("told a defect lane to build on its park: %+v", record)
		}
	}
}

func TestACardOnASideBoardOutsideAParkIsTheSameDefect(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	shown := post(t, client.Draft{Lane: "dashboard-content-1", Kind: "report", To: []string{metaDesk}, Text: "13 cards are up for his taps: http://yasyf-forge-studio:61118/p/dashboard-mockups-for-your-tap--7b8a71f4 ."})
	post(t, client.Draft{Lane: "owner-relay", Kind: "note", To: []string{"root"}, Text: "Linked him http://yasyf-forge-studio:61118/p/dashboard-mockups-for-your-tap--7b8a71f4 ."})
	post(t, client.Draft{Lane: "lane-b", Kind: "report", Text: "card-sent is on http://127.0.0.1:57524/p/" + mainSlug + " ."})

	passTwice(t, newCheck(time.Now()))

	records := said(t)
	if len(records) != 1 || records[0].Re != shown || !strings.Contains(records[0].Text, "OFF-BOARD") || records[0].To[1] != metaDesk {
		t.Fatalf("said %+v", records)
	}
}

func TestAParkNamingABoardIsNoDefectWhenTheMainBoardIsUnread(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	post(t, client.Draft{Lane: "dashboard-content-1", Kind: "blocked", To: []string{metaDesk}, Text: "Parked on the owner's taps on the dashboard mockups board, 13 cards, none tapped when last checked: http://yasyf-forge-studio:61118/p/dashboard-mockups-for-your-tap--7b8a71f4 ."})

	check := newCheck(time.Now())
	check.Main = func(context.Context) ([]string, error) { return nil, errors.New("no daemon") }
	passTwice(t, check)

	for _, record := range said(t) {
		if strings.Contains(record.Text, "OFF-BOARD") {
			t.Fatalf("said %q", record.Text)
		}
	}
}

func TestARepeatedOrderAsksRootWhatHoldsItAndNamesWhatSharesItsWords(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	holding := post(t, client.Draft{Lane: "lane-a", Kind: "blocked", Text: "Old cloud agent rows wait on the agent runner release."})
	unrelated := post(t, client.Draft{Lane: "lane-b", Kind: "blocked", Text: "Tests wait on the build."})
	order := post(t, client.Draft{Lane: "owner-relay", Kind: "ask", To: []string{"meta"}, Text: "Owner correction, a repeat: delete the old cloud agent rows behind the agent runner release."})

	passTwice(t, newCheck(time.Now()))

	records := said(t)
	if len(records) != 1 {
		t.Fatalf("said %d records, want one", len(records))
	}
	got := records[0]
	if string(got.Kind) != "ask" || got.To[0] != lanes.Root || got.Re != order || !strings.Contains(got.Text, fmt.Sprintf("REPEAT #%d", order)) {
		t.Fatalf("said %+v", got)
	}
	if !strings.Contains(got.Text, fmt.Sprintf("#%d", holding)) || strings.Contains(got.Text, fmt.Sprintf("#%d", unrelated)) {
		t.Fatalf("named the wrong blocks: %q", got.Text)
	}
}

func TestARepeatedOrderNothingSharesStillAsksRoot(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	post(t, client.Draft{Lane: "lane-b", Kind: "blocked", Text: "Tests wait on the build."})
	post(t, client.Draft{Lane: "owner-relay", Kind: "ask", To: []string{"root"}, Text: "He has now given the delete order three times."})

	passTwice(t, newCheck(time.Now()))

	records := said(t)
	if len(records) != 1 || !strings.Contains(records[0].Text, "Nothing open shares its words") {
		t.Fatalf("said %+v", records)
	}
}

func TestTheCommunicatorsOwnReplyToHimIsNoRepeatOfHisOrder(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	post(t, client.Draft{Lane: "owner-relay", Kind: "note", To: []string{"owner"}, Text: "The test that catches a repeat of the Gemini outage is green."})

	passTwice(t, newCheck(time.Now()))

	if records := said(t); len(records) != 0 {
		t.Fatalf("said %+v", records)
	}
}

func docsFake(now time.Time, register string) *shelltest.Fake {
	const ref = "refs/remotes/origin/main:"
	fake := &shelltest.Fake{}
	fake.Reply("", "git", "-C", "/docs", "fetch")
	fake.Reply(register, "git", "-C", "/docs", "rev-parse")
	fake.Reply("design-docs/x/registers.json\ndesign-docs/x/index.html\ndesign-docs/old/registers.json\none-pagers/y/index.html\n", "git", "-C", "/docs", "ls-tree")
	fake.Reply(register, "git", "-C", "/docs", "show", ref+"design-docs/x/registers.json")
	fake.Reply(fmt.Sprintf(`{"meta":{"date":%q},"open":[{"g":"owner","id":"Q9","t":"Long gone"}]}`, now.Add(-60*24*time.Hour).Format(time.DateOnly)), "git", "-C", "/docs", "show", ref+"design-docs/old/registers.json")
	fake.Reply("<p>nothing here</p>\n<p>Your pick: keep it</p>\n<p>Your call: keep it or drop it</p>\n<p>Your call on card-z</p>", "git", "-C", "/docs", "show", ref+"one-pagers/y/index.html")
	return fake
}

func TestADesignDocsOpenChoiceWithNoCardReachesTheDeskOnceAndOnlyANewOneSaysMore(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	now := time.Now()
	register := fmt.Sprintf(`{"meta":{"date":%q},
		"open":[{"g":"owner","id":"Q1","t":"Pick the route"},{"g":"picks","id":"Q2","t":"Board card card-a: choose"},{"g":"owner","id":"Q3","t":"Picked","s":"resolved"},{"g":"spikes","id":"V1","t":"Measure it"}],
		"decisions":[{"id":"DQ1","s":"resolved","r":"Provisional owner pick; the owner's choice is still open."},{"id":"DQ2","s":"superseded","r":"The owner's choice is still open."},{"id":"DQ3","s":"resolved","r":"Settled."}]}`, now.Format(time.DateOnly))
	check := newCheck(now)
	check.Docs = &choices.Docs{Runner: docsFake(now, register), Dir: "/docs"}

	passTwice(t, check)

	records := said(t)
	if len(records) != 1 || records[0].To[0] != boardDesk {
		t.Fatalf("said %+v", records)
	}
	text := records[0].Text
	if !strings.Contains(text, "x (Q1, DQ1)") || !strings.Contains(text, "y (Your call: keep it or drop it.)") {
		t.Fatalf("said %q", text)
	}
	for _, closed := range []string{"Q2", "Q3", "V1", "DQ2", "DQ3", "Q9", "card-z", "Your pick"} {
		if strings.Contains(text, closed) {
			t.Errorf("named %s in %q", closed, text)
		}
	}

	closedOne := strings.ReplaceAll(register, `"t":"Pick the route"`, `"t":"Pick the route","s":"resolved"`)
	check.Docs = &choices.Docs{Runner: docsFake(now, closedOne), Dir: "/docs"}
	passTwice(t, check)
	if records := said(t); len(records) != 1 {
		t.Fatalf("said %d records after a choice closed, want the first alone", len(records))
	}

	added := strings.ReplaceAll(register, `{"g":"spikes"`, `{"g":"owner","id":"Q7","t":"Pick the store"},{"g":"spikes"`)
	check.Docs = &choices.Docs{Runner: docsFake(now, added), Dir: "/docs"}
	passTwice(t, check)
	records = said(t)
	if len(records) != 2 || !strings.Contains(records[1].Text, "x (Q7)") || strings.Contains(records[1].Text, "Q1") || strings.Contains(records[1].Text, "keep it") {
		t.Fatalf("said %+v after a choice was added, want a second record naming Q7 alone", records)
	}
}

func TestAReadThatFailsSaysNothing(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	failed := errors.New("read failed")
	post(t, client.Draft{Lane: "lane-a", Kind: "ask", To: []string{boardDesk}, Text: "CARD: card-x: Which table?"})

	unreadBoard := newCheck(time.Now())
	unreadBoard.Asks = func(context.Context) ([]board.OpenAsk, error) { return nil, failed }
	unreadBoard.Docs = &choices.Docs{Runner: &shelltest.Fake{}, Dir: "/docs"}
	passTwice(t, unreadBoard)

	unroutable := newCheck(time.Now().Add(2 * time.Hour))
	unroutable.Desk = func() (string, error) { return "", failed }
	passTwice(t, unroutable)

	if records := said(t); len(records) != 0 {
		t.Fatalf("said %q", records[0].Text)
	}
}
