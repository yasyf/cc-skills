package board

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	readyRepo = "o/r"
	readyHead = "fa8217f33bf26d96b05780f47ebf5475dc90fae6"
	nextHead  = "1234567890abcdef1234567890abcdef12345678"
)

func tapRow(number int) store.ReviewRow {
	return store.ReviewRow{
		Repo: readyRepo, Number: number, Title: fmt.Sprintf("sand: fix %d", number), Lane: fmt.Sprintf("lane-%d", number), Head: readyHead, ReadHead: readyHead,
		ForUsers: "Chats load.", PlanRow: "Checked.", Risk: "Low.", ReadyForTap: true, State: prrow.StateOpen, WaitsOn: "Only your approval.",
		Hold: prrow.None, SupersededBy: prrow.None,
	}
}

func putRows(t *testing.T, f *linkFixture, rows ...store.ReviewRow) {
	t.Helper()
	if err := f.Cards.Store.PutReviewRows(rows); err != nil {
		t.Fatal(err)
	}
}

func readyPass(t *testing.T, f *linkFixture) {
	t.Helper()
	cards := f.Cards
	cards.Session = "board-1"
	r := ReadyCards{Cards: cards, Repo: readyRepo, Lane: "supervisor"}
	if err := r.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func askCalls(f *linkFixture) []string {
	var calls []string
	for _, call := range f.shell.seen() {
		if strings.HasPrefix(call, "cc-present ask ") {
			calls = append(calls, call)
		}
	}
	return calls
}

func TestARowTurningReadyDrawsOneCardAndASecondPassDrawsNothing(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 || !strings.Contains(calls[0], "--lane supervisor") {
		t.Fatalf("asks = %v, want one card", calls)
	}
	var card struct {
		ID, Title string
		Children  []struct {
			Type, Head string
			Options    []struct{ Label string }
		}
	}
	if err := json.Unmarshal([]byte(f.shell.drawn["card-tap-50287-fa8217f"]), &card); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(card.Title, "Tap: #50287") || card.Children[0].Options[0].Label != approveLabel || card.Children[1].Head != readyHead {
		t.Fatalf("card = %+v", card)
	}
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 {
		t.Fatalf("after a second pass, asks = %v, want still one", calls)
	}
}

func TestARowWithACardAtItsHeadDrawsNone(t *testing.T) {
	for name, block := range map[string]string{
		"a hand-drawn tap": fmt.Sprintf(`{"id":"card-rmw2","type":"card","children":[{"id":"c","type":"choice","options":[{"label":"Approve"}]},{"id":"p","type":"pr","number":50287,"head":%q}]}`, readyHead),
		"a merge card":     `{"id":"merge:50287:fa8217f","type":"card"}`,
	} {
		t.Run(name, func(t *testing.T) {
			liveDrive(t)
			f := newLink(t)
			f.shell.drawn = map[string]string{"existing": block}
			putRows(t, f, tapRow(50287))
			readyPass(t, f)
			if calls := askCalls(f); len(calls) != 0 {
				t.Fatalf("asks = %v, want none", calls)
			}
		})
	}
}

func TestAQuestionCardNamingTheRowIsNotItsTapCard(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.drawn = map[string]string{"existing": fmt.Sprintf(`{"id":"card-q","type":"card","children":[{"id":"c","type":"choice","options":[{"label":"Wait for it"}]},{"id":"p","type":"pr","number":50287,"head":%q}]}`, readyHead)}
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 {
		t.Fatalf("asks = %v, want its own card", calls)
	}
}

func TestARowDrawsItsCardWhileEarlierCardsStandUnanswered(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	for number := 100; number < 112; number++ {
		row := tapRow(number)
		putRows(t, f, row)
		readyPass(t, f)
		f.run(t)
		if _, drawn := f.shell.drawn[tapCardID(row)]; !drawn {
			t.Fatalf("#%d turned ready with %d cards standing unanswered and drew none", number, number-100)
		}
	}
}

func parkedRow(t *testing.T, f *linkFixture, number int, lane, headAt string) store.ReviewRow {
	t.Helper()
	row := tapRow(number)
	row.Lane, row.HeadAt = lane, headAt
	if _, err := f.Cards.Store.PutFact(readyRepo, number, row.Head, gates.MissingFact, onlyNoTap, cardsNow); err != nil {
		t.Fatal(err)
	}
	return row
}

type listRow struct {
	ID      string
	Buttons []string
	PR      SlackPRRow
	PRCard  bool `json:"pr_card"`
}

type listPost struct {
	Status string
	Title  string
	Group  string
	Rows   []listRow
}

func listPosts(t *testing.T, f *linkFixture) []listPost {
	t.Helper()
	var posts []listPost
	for _, call := range f.shell.seen() {
		_, body, found := strings.Cut(call, "--card ")
		if !found {
			continue
		}
		var post listPost
		if err := json.Unmarshal([]byte(strings.TrimSuffix(body, " <")), &post); err != nil {
			t.Fatalf("%s: %v", body, err)
		}
		posts = append(posts, post)
	}
	return posts
}

func burst(t *testing.T, f *linkFixture, count int) []store.ReviewRow {
	t.Helper()
	rows := make([]store.ReviewRow, 0, count)
	for at := range count {
		rows = append(rows, parkedRow(t, f, 100+at, "some-lane", fmt.Sprintf("2026-11-04T%02d:00:00Z", at%24)))
	}
	putRows(t, f, rows...)
	return rows
}

func TestABurstArrivesAsOneListWithNoPostPerRow(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	if _, err := f.Cards.Store.PutFact(readyRepo, tapListNumber, "trunk-head", "trunk-read", "not a list", cardsNow); err != nil {
		t.Fatal(err)
	}
	rows := burst(t, f, 3)
	alert := rows[len(rows)-1]
	alert.Lane = "sentry-7829830715-fix-opus"
	putRows(t, f, alert)
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 0 {
		t.Fatalf("asks = %v, want none for a burst", calls)
	}
	posts := listPosts(t, f)
	if len(posts) != 1 || len(posts[0].Rows) != len(rows) || posts[0].Status != "needs_you" || posts[0].Group != listName(1) {
		t.Fatalf("posts = %+v, want one list of %d, the first, with no other fact read as a list", posts, len(rows))
	}
	row := posts[0].Rows[0]
	if row.ID != MergeWaits(alert.Number, readyHead) || !row.PRCard || !slices.Equal(row.Buttons, []string{approveLabel, holdLabel}) || row.PR.Change == "" || row.PR.Plan == "" || row.PR.Blocks == "" {
		t.Fatalf("row = %+v, want its Approve, what it does, its plan and what it waits on", row)
	}
	readyPass(t, f)
	f.run(t)
	if got := listPosts(t, f); len(got) != 1 {
		t.Fatalf("a second pass with nothing moved posted %d lists, want still one", len(got))
	}
}

func TestAListPastSlacksLimitSplitsIntoAFewPosts(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	burst(t, f, 2*listRowsPerPost+1)
	readyPass(t, f)
	posts := listPosts(t, f)
	if len(posts) != 3 || len(posts[0].Rows) != listRowsPerPost || len(posts[2].Rows) != 1 || posts[0].Group == posts[1].Group {
		t.Fatalf("posts = %+v, want %d, %d and 1 rows on three groups", posts, listRowsPerPost, listRowsPerPost)
	}
	if listRowsPerPost*listRowBlocks+listFrameBlocks > slackMessageBlocks {
		t.Fatalf("%d rows draw past Slack's %d blocks", listRowsPerPost, slackMessageBlocks)
	}
	words := strings.Repeat("every word counts here ", 100)
	row := tapRow(900)
	row.Title, row.ForUsers, row.PlanRow, row.PlanLine, row.WaitsOn = words, words, words, words, words
	r := ReadyCards{Cards: f.Cards, Repo: readyRepo}
	card := r.listCard(context.Background(), listName(1), slices.Repeat([]store.ReviewRow{row}, listRowsPerPost))
	encoded, err := json.Marshal(card["rows"])
	if err != nil {
		t.Fatal(err)
	}
	var drawn []struct{ PR SlackPRRow }
	if err := json.Unmarshal(encoded, &drawn); err != nil {
		t.Fatal(err)
	}
	total := 0
	for _, one := range drawn {
		total += len([]rune(one.PR.Title + one.PR.Change + one.PR.Plan + one.PR.Blocks))
	}
	if total+listRowsPerPost*listRowFrameChars > cardVisibleChars {
		t.Fatalf("a full list of the longest rows carries %d characters of words, past %d", total, cardVisibleChars)
	}
}

func TestIncidentFixesLeadThenTheOldest(t *testing.T) {
	due := []store.ReviewRow{
		{Number: 3, Lane: "some-lane", HeadAt: "2026-11-04T01:00:00Z"},
		{Number: 1, Lane: "other-lane", HeadAt: "2026-11-04T03:00:00Z"},
		{Number: 2, Lane: "sentry-7829830715-fix-opus", HeadAt: "2026-11-04T09:00:00Z"},
		{Number: 4, Lane: "dd-331844288-fix", HeadAt: "2026-11-04T08:00:00Z"},
		{Number: 5, Lane: "some-lane", HeadAt: "2026-11-04T01:00:00Z"},
	}
	inTapOrder(due)
	got := make([]int, len(due))
	for at, row := range due {
		got[at] = row.Number
	}
	if !slices.Equal(got, []int{4, 2, 3, 5, 1}) {
		t.Fatalf("order = %v", got)
	}
}

func TestAListIsEditedAsRowsLeaveAndARowOnANewHeadDrawsItsOwnCard(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	rows := burst(t, f, 4)
	readyPass(t, f)
	f.run(t)
	tapped := rows[0]
	tapped.Tapped = true
	moved := rows[1]
	moved.Head = nextHead
	putRows(t, f, tapped, moved)
	readyPass(t, f)
	f.run(t)
	posts := listPosts(t, f)
	if len(posts) != 2 || posts[1].Group != posts[0].Group || len(posts[1].Rows) != len(rows)-2 {
		t.Fatalf("posts = %+v, want the same list edited down to %d", posts, len(rows)-2)
	}
	if _, drawn := f.shell.drawn[tapCardID(moved)]; !drawn {
		t.Fatalf("drawn = %v, want the new head's own card", f.shell.drawn)
	}
	rest := make([]store.ReviewRow, 0, len(rows)-2)
	for _, row := range rows[2:] {
		row.Tapped = true
		rest = append(rest, row)
	}
	putRows(t, f, rest...)
	readyPass(t, f)
	f.run(t)
	posts = listPosts(t, f)
	if last := posts[len(posts)-1]; len(last.Rows) != 0 || last.Status != "done" || last.Group != posts[0].Group {
		t.Fatalf("last post = %+v, want the list closed in place", last)
	}
	readyPass(t, f)
	if again := listPosts(t, f); len(again) != len(posts) {
		t.Fatalf("a closed list posted again: %d after %d", len(again), len(posts))
	}
}

func TestARowTurningReadyAfterAListDrawsItsOwnCard(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	burst(t, f, 3)
	readyPass(t, f)
	f.run(t)
	late := parkedRow(t, f, 900, "some-lane", "2026-11-05T01:00:00Z")
	putRows(t, f, late)
	readyPass(t, f)
	f.run(t)
	if _, drawn := f.shell.drawn[tapCardID(late)]; !drawn {
		t.Fatal("a row turning ready after the list drew no card")
	}
	if posts := listPosts(t, f); len(posts) != 1 {
		t.Fatalf("lists = %d, want the first alone", len(posts))
	}
}

func TestARowNotParkedOnlyOnHisApprovalStaysOffTheListAndStillDraws(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	burst(t, f, 3)
	other := tapRow(900)
	if _, err := f.Cards.Store.PutFact(readyRepo, other.Number, other.Head, gates.MissingFact, `["no tap","mergeable"]`, cardsNow); err != nil {
		t.Fatal(err)
	}
	putRows(t, f, other)
	readyPass(t, f)
	f.run(t)
	posts := listPosts(t, f)
	if len(posts) != 1 || slices.ContainsFunc(posts[0].Rows, func(row listRow) bool { return strings.HasPrefix(row.ID, "merge:900:") }) {
		t.Fatalf("posts = %+v, want #900 off the list", posts)
	}
	if _, drawn := f.shell.drawn[tapCardID(other)]; !drawn {
		t.Fatal("a row left off the list drew no card, so nothing shows it to him")
	}
}

func TestAFailedListPostStopsThePassAndNoRowReadsAsShown(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	rows := burst(t, f, 3)
	f.shell.refuse = "cc-slack refused it"
	cards := f.Cards
	cards.Session = "board-1"
	if err := (ReadyCards{Cards: cards, Repo: readyRepo, Lane: "supervisor"}).Pass(context.Background()); err == nil {
		t.Fatal("a refused list post read as a clean pass, so nothing raises it")
	}
	facts, err := f.Cards.Store.FactsAtRowHeads(readyRepo)
	if err != nil {
		t.Fatal(err)
	}
	if said := facts[rows[0].Number][FactTapCard]; said != "" {
		t.Fatalf("fact = %q, want none, so the next pass lists it again", said)
	}
	readyPass(t, f)
	if posts := listPosts(t, f); len(posts) == 0 || posts[len(posts)-1].Group != "tap-list-1" {
		t.Fatalf("posts = %+v, want the next pass to send the list", posts)
	}
}

func TestARowWhoseCardStandsInSlackDrawsNothingAgain(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	rows := []store.ReviewRow{tapRow(100), tapRow(101)}
	for _, row := range rows {
		if _, err := f.Cards.Store.PutFact(readyRepo, row.Number, row.Head, gates.MissingFact, onlyNoTap, cardsNow); err != nil {
			t.Fatal(err)
		}
		if err := MarkShown(f.Cards.Store, readyRepo, rows, []SlackShown{{SlackPR{readyRepo, row.Number}, MergeWaits(row.Number, row.Head)}}, cardsNow); err != nil {
			t.Fatal(err)
		}
	}
	putRows(t, f, rows...)
	readyPass(t, f)
	f.run(t)
	if calls, posts := askCalls(f), listPosts(t, f); len(calls) != 0 || len(posts) != 0 {
		t.Fatalf("asks = %v and lists = %+v, want no second post for a card that stands", calls, posts)
	}
}

func TestARefusedDrawAlarmsOnceNamingThePR(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.refuse = "the daemon is down"
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	alarms := addressedTo(t, linkRefusalKind, communicator)
	if len(alarms) != 1 || !strings.Contains(alarms[0].Text, readyRepo+"#50287") || !strings.Contains(alarms[0].Text, "the daemon is down") {
		t.Fatalf("alarms = %+v", alarms)
	}
	readyPass(t, f)
	f.run(t)
	if alarms := addressedTo(t, linkRefusalKind, communicator); len(alarms) != 1 {
		t.Fatalf("after a second pass, alarms = %d, want one", len(alarms))
	}
	facts, err := f.Cards.Store.FactsAtRowHeads(readyRepo)
	if err != nil {
		t.Fatal(err)
	}
	if facts[50287][FactTapCard] != tapCardRefused {
		t.Fatalf("fact = %q", facts[50287][FactTapCard])
	}
}

func TestANewHeadTakesTheOldCardDownAndDrawsItsOwn(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	row := tapRow(50287)
	putRows(t, f, row)
	readyPass(t, f)
	f.run(t)
	row.Head = nextHead
	putRows(t, f, row)
	readyPass(t, f)
	f.run(t)
	if _, old := f.shell.drawn["card-tap-50287-fa8217f"]; old {
		t.Fatal("the old head's card stayed on the board")
	}
	if _, drawn := f.shell.drawn["card-tap-50287-1234567"]; !drawn {
		t.Fatalf("drawn = %v, want the new head's card", f.shell.drawn)
	}
}

func redRow(row store.ReviewRow) store.ReviewRow {
	row.CI, row.ReadyForTap, row.WaitsOn = prrow.CIRed, false, prrow.RedAtHead
	return row
}

func TestARedHeadTakesItsTapCardDownAndTurningGreenDrawsItAgain(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	row := tapRow(50485)
	putRows(t, f, row)
	readyPass(t, f)
	f.run(t)
	putRows(t, f, redRow(row))
	readyPass(t, f)
	f.run(t)
	if _, onBoard := f.shell.drawn[tapCardID(row)]; onBoard {
		t.Fatal("a red PR's tap card stayed on the board")
	}
	putRows(t, f, row)
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 2 {
		t.Fatalf("asks = %v, want the first draw and one once green", calls)
	}
	if _, onBoard := f.shell.drawn[tapCardID(row)]; !onBoard {
		t.Fatalf("drawn = %v, want the card back", f.shell.drawn)
	}
}

func TestARedNewHeadTakesTheOldHeadsCardDownAndDrawsNone(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	row := tapRow(49059)
	putRows(t, f, row)
	readyPass(t, f)
	f.run(t)
	pushed := redRow(row)
	pushed.Head, pushed.ReadHead = nextHead, nextHead
	putRows(t, f, pushed)
	readyPass(t, f)
	f.run(t)
	if len(f.shell.drawn) != 0 {
		t.Fatalf("drawn = %v, want no card for a red head", f.shell.drawn)
	}
	readyPass(t, f)
	if calls := askCalls(f); len(calls) != 1 {
		t.Fatalf("asks = %v, want only the first draw", calls)
	}
}

func TestOneRedRowAmongReadyRowsReadsTheBoardOnceAtOneRowOrAThousand(t *testing.T) {
	for _, n := range []int{1, 1000} {
		t.Run(fmt.Sprint(n), func(t *testing.T) {
			liveDrive(t)
			f := newLink(t)
			rows := []store.ReviewRow{tapRow(1)}
			for number := 2; number <= n; number++ {
				row := tapRow(number)
				row.Tapped = true
				rows = append(rows, row)
			}
			putRows(t, f, rows...)
			readyPass(t, f)
			f.run(t)
			rows[0] = redRow(rows[0])
			putRows(t, f, rows...)
			before := len(f.shell.seen())
			readyPass(t, f)
			calls := slices.DeleteFunc(f.shell.seen()[before:], func(call string) bool { return !strings.HasPrefix(call, "cc-present ") })
			if len(calls) != 1 || !strings.HasPrefix(calls[0], "cc-present remove-block "+tapCardID(rows[0])) {
				t.Fatalf("board writes = %v, want one removal", calls)
			}
		})
	}
}

func TestATapCardDrawsAtHisDMRootEvenWhenAReplyNamedItsPRInAThread(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	replies := filepath.Join(f.state, "replies")
	if err := os.MkdirAll(replies, 0o700); err != nil {
		t.Fatal(err)
	}
	sent := `{"reply":{"prs":["https://github.com/o/r/pull/50287"],"thread":"2.2"},"at":"2026-11-04T11:00:00Z"}`
	if err := os.WriteFile(filepath.Join(replies, "testers.json"), []byte(sent), 0o600); err != nil {
		t.Fatal(err)
	}
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 || strings.Contains(calls[0], "--slack") {
		t.Fatalf("asks = %v, want one at the root, naming no thread", calls)
	}
}

func redrawCalls(f *linkFixture) []string {
	var calls []string
	for _, call := range f.shell.seen() {
		if strings.HasPrefix(call, "cc-slack redraw-pr-cards ") {
			calls = append(calls, call)
		}
	}
	return calls
}

func drawnAt(t *testing.T, f *linkFixture, row store.ReviewRow) {
	t.Helper()
	if _, err := f.Cards.Store.PutFact(readyRepo, row.Number, row.Head, FactCardDrawn, drawnSignature(Card(readyRepo, row)), f.Cards.Now()); err != nil {
		t.Fatal(err)
	}
}

func TestADrawnCardFollowsItsRowAndTheRowTurningReadyDrawsNoSecondPost(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.redrawing = 1
	pending := tapRow(50287)
	pending.ReadyForTap, pending.CI = false, prrow.CIPending
	drawnAt(t, f, pending)
	putRows(t, f, pending)
	readyPass(t, f)
	if calls := redrawCalls(f); len(calls) != 0 {
		t.Fatalf("redraws = %v, want none for a row that has not changed", calls)
	}
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	if calls := redrawCalls(f); len(calls) != 1 || !strings.HasSuffix(calls[0], "--pr https://github.com/o/r/pull/50287") {
		t.Fatalf("redraws = %v, want this PR's post redrawn", calls)
	}
	if calls := askCalls(f); len(calls) != 0 {
		t.Fatalf("asks = %v, want no second post", calls)
	}
}

func TestARowWhosePostIsGoneDrawsItsOwnCard(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	pending := tapRow(50287)
	pending.ReadyForTap, pending.CI = false, prrow.CIPending
	drawnAt(t, f, pending)
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 {
		t.Fatalf("asks = %v, want the row's own card once its post is gone", calls)
	}
}

func TestARowRedrawsAfterItsTapCardIsWithdrawnByID(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	row := tapRow(50287)
	putRows(t, f, row)
	readyPass(t, f)
	f.run(t)
	cards := f.Cards
	cards.Session = "board-1"
	if err := cards.Retire(context.Background(), "owner-relay", "it landed in the wrong place", []string{tapCardID(row)}, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 2 {
		t.Fatalf("asks = %v, want the first draw and a redraw", calls)
	}
	if _, onBoard := f.shell.drawn[tapCardID(row)]; !onBoard {
		t.Fatalf("drawn = %v, want the card back", f.shell.drawn)
	}
}

func TestAPassTellsTheLaneOncePerHeadWhenItsSummaryGivesNoUserSentence(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	var told []laneReport
	f.Cards.Report = func(_ context.Context, by string, to []string, topic, text string) error {
		told = append(told, laneReport{by, strings.Join(to, ","), topic, text})
		return nil
	}
	plain, planOnly, unread := tapRow(50830), tapRow(50831), tapRow(50832)
	planOnly.ForUsers = "Plan: the class fix for the smoke order.\n\nPlan row: owner:m835"
	unread.Head, unread.ReadHead, unread.ForUsers, unread.ReadyForTap = "", "", "", false
	putRows(t, f, plain, planOnly, unread)
	readyPass(t, f)
	readyPass(t, f)
	if len(told) != 1 || told[0].to != "lane-50831" || told[0].by != "supervisor" || !strings.Contains(told[0].text, "only plan text") {
		t.Fatalf("told = %+v, want lane-50831 told once and nothing for the row GitHub has not read", told)
	}
	if change := Card(readyRepo, planOnly).PRs[0].Change; !strings.HasPrefix(change, "Not written yet: lane-50831") {
		t.Fatalf("What changes = %q, want it to say the lane owes one", change)
	}
	planOnly.Head, planOnly.ReadHead = nextHead, nextHead
	putRows(t, f, planOnly)
	readyPass(t, f)
	if len(told) != 2 {
		t.Fatalf("told = %+v, want the new head told again", told)
	}
}

func TestAgainstThePlanSaysThePlanLineOnce(t *testing.T) {
	row := tapRow(50831)
	row.PlanLine, row.PlanRow = "Plan: stop this red job.", "Plan: stop this red job. Checked against the plan by the fidelity review at this version."
	if got := PlanWords(row); got != row.PlanRow {
		t.Fatalf("Against the plan = %q, want %q", got, row.PlanRow)
	}
}

func TestAListRowShowsAPortRefusalWhole(t *testing.T) {
	f := newLink(t)
	row := tapRow(50871)
	row.WaitsOn = "Only your approval: its source #49144 has not merged yet, and your approval lands this port anyway."
	card := ReadyCards{Cards: f.Cards, Repo: readyRepo}.listCard(context.Background(), listName(1), []store.ReviewRow{row})
	if blocks := card["rows"].([]map[string]any)[0]["pr"].(SlackPRRow).Blocks; blocks != row.WaitsOn {
		t.Fatalf("Blocks landing = %q, want %q whole", blocks, row.WaitsOn)
	}
}

func TestEveryApprovalPostOnAPortOpensWithItsSource(t *testing.T) {
	f := newLink(t)
	row := tapRow(50888)
	row.Port = prrow.PortOf + "49144"
	source := "Port of [#49144](https://github.com/o/r/pull/49144)"
	if card := Card(readyRepo, row); !strings.HasPrefix(card.Title, "Port of #49144: ") || card.PRs[0].Port != source {
		t.Fatalf("card title %q, port %q", card.Title, card.PRs[0].Port)
	}
	block, err := TapCard(readyRepo, row)
	if err != nil {
		t.Fatal(err)
	}
	var tap struct{ Summary string }
	if err := json.Unmarshal(block, &tap); err != nil || !strings.HasPrefix(tap.Summary, source+". ") {
		t.Fatalf("tap card summary = %q, %v", tap.Summary, err)
	}
	list := ReadyCards{Cards: f.Cards, Repo: readyRepo}.listCard(context.Background(), listName(1), []store.ReviewRow{row})
	if change := list["rows"].([]map[string]any)[0]["pr"].(SlackPRRow).Change; !strings.HasPrefix(change, source+". ") {
		t.Fatalf("list row = %q", change)
	}
	if child := FillPRChild(map[string]any{}, Card(readyRepo, row).PRs[0]); !strings.HasPrefix(child["summary"].(string), "**"+source+".**") {
		t.Fatalf("pr child summary = %q", child["summary"])
	}
}
