package owner

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	repliedAt = "1793831915.566039"
	cardRoot  = "1793831048.292899"
)

func wordIn(channel, thread, text string) threadWord {
	return threadWord{Channel: channel, Thread: thread, TS: repliedAt, Text: text}
}

func wordEntry(t *testing.T) (*shelltest.Fake, drive.Entry) {
	t.Helper()
	fake := &shelltest.Fake{}
	fake.Reply("#1", "cci", "post")
	return fake, drive.Entry{Drive: "brook", StateDir: t.TempDir()}
}

func seedLedger(t *testing.T, entry drive.Entry, rows string) {
	t.Helper()
	path := Ledger(entry.StateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(header+rows), 0o600); err != nil {
		t.Fatal(err)
	}
}

func ledgerRows(t *testing.T, entry drive.Entry) []Row {
	t.Helper()
	ledger, err := os.ReadFile(Ledger(entry.StateDir))
	if err != nil {
		t.Fatal(err)
	}
	return Parse(ledger)
}

func posted(fake *shelltest.Fake) [][]string {
	var argvs [][]string
	for _, call := range fake.Calls() {
		if call.Argv[0] == "cci" && call.Argv[1] == "post" {
			argvs = append(argvs, call.Argv)
		}
	}
	return argvs
}

func TestHisWordsInACardsThreadMakeARowAuditLists(t *testing.T) {
	fake, entry := wordEntry(t)
	said := "1 but you need to first do the faithful port then fix it"

	rows, _, err := wordIn("D0B6Z", cardRoot, said).rows(context.Background(), fake, entry, cardRoute{})
	if err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(rows, []string{"m1"}) {
		t.Fatalf("rows = %q, want m1", rows)
	}
	got := ledgerRows(t, entry)
	if len(got) != 1 || got[0].Text != said || got[0].State != opened || got[0].At != "Nov 4 2:38 PM" {
		t.Fatalf("ledger = %+v, want his words open at the Slack time", got)
	}
	if listed := ids(Undelivered(got, nil, nil)); !slices.Equal(listed, []string{"m1"}) {
		t.Fatalf("audit lists %q, want m1", listed)
	}
	want := []string{"cci", "post", "--drive", entry.Bus(), "--lane", "owner-relay", "--kind", "owner", "--id", "owner:m1", "--attr", "row=m1", "--quoted", said}
	if got := posted(fake); len(got) != 1 || !slices.Equal(got[0], want) {
		t.Fatalf("posted = %q, want %q", got, want)
	}
}

func TestHisWordsAtTheDMRootMakeARow(t *testing.T) {
	fake, entry := wordEntry(t)

	if _, _, err := wordIn("D0B6Z", repliedAt, "evaluate a faster disk").rows(context.Background(), fake, entry, cardRoute{}); err != nil {
		t.Fatal(err)
	}

	if got := ledgerRows(t, entry); len(got) != 1 {
		t.Fatalf("ledger = %+v, want one row", got)
	}
}

func TestHisWordsInAWatchedChannelThreadMakeARowAndAChannelRootDoesNot(t *testing.T) {
	fake, entry := wordEntry(t)

	if _, _, err := wordIn("C0TEAM", repliedAt, "a post at the channel root").rows(context.Background(), fake, entry, cardRoute{}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(Ledger(entry.StateDir)); !os.IsNotExist(err) {
		t.Fatalf("a channel root wrote a ledger: %v", err)
	}

	if _, _, err := wordIn("C0TEAM", cardRoot, "fix this and make it durable").rows(context.Background(), fake, entry, cardRoute{}); err != nil {
		t.Fatal(err)
	}
	if got := ledgerRows(t, entry); len(got) != 1 {
		t.Fatalf("ledger = %+v, want the thread reply's row", got)
	}
}

func TestAMessageWithNoWordsMakesNoRow(t *testing.T) {
	fake, entry := wordEntry(t)

	rows, _, err := wordIn("D0B6Z", cardRoot, "").rows(context.Background(), fake, entry, cardRoute{})

	if err != nil || rows != nil {
		t.Fatalf("rows = %q, err = %v, want none", rows, err)
	}
	if len(fake.Calls()) != 0 {
		t.Fatalf("calls = %v, want none", fake.Calls())
	}
}

func TestASecondDeliveryOfTheSameMessageReturnsItsRowsAndWritesNone(t *testing.T) {
	fake, entry := wordEntry(t)
	word := wordIn("D0B6Z", cardRoot, "make the smoke less flaky")

	first, _, err := word.rows(context.Background(), fake, entry, cardRoute{})
	if err != nil {
		t.Fatal(err)
	}
	if err := Mark(Ledger(entry.StateDir), "m1", addressed, 0); err != nil {
		t.Fatal(err)
	}
	second, _, err := word.rows(context.Background(), fake, entry, cardRoute{})
	if err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(first, second) {
		t.Fatalf("second delivery = %q, want %q", second, first)
	}
	if got := ledgerRows(t, entry); len(got) != 1 {
		t.Fatalf("ledger = %+v, want one row", got)
	}
	if got := posted(fake); len(got) != 1 {
		t.Fatalf("posted = %q, want one record", got)
	}
}

func TestAnOrderRelayForwardedOrAPromptAlreadyCarriedIsBookedToThatRow(t *testing.T) {
	cases := map[string]string{
		"his words, addressed": "| m9 | Nov 4 2:38 PM | once we cut over delete all this code | 433556 | addressed |\n",
		"his permalink":        "| m9 | Nov 4 2:39 PM | https://acme.slack.com/archives/D0B6Z/p1793831915566039?thread_ts=1793831048.292899 what is this |  | responded |\n",
	}
	for name, row := range cases {
		t.Run(name, func(t *testing.T) {
			fake, entry := wordEntry(t)
			seedLedger(t, entry, row)

			rows, _, err := wordIn("D0B6Z", cardRoot, "Once we cut over delete all this code").rows(context.Background(), fake, entry, cardRoute{})
			if err != nil {
				t.Fatal(err)
			}

			if !slices.Equal(rows, []string{"m9"}) || len(ledgerRows(t, entry)) != 1 || len(posted(fake)) != 0 {
				t.Fatalf("rows = %q, ledger = %+v, posted = %q; want booked to m9", rows, ledgerRows(t, entry), posted(fake))
			}
		})
	}
}

func TestTheSameShortWordsOnALaterMessageGetTheirOwnRow(t *testing.T) {
	fake, entry := wordEntry(t)
	if _, _, err := wordIn("D0B6Z", cardRoot, "yes").rows(context.Background(), fake, entry, cardRoute{}); err != nil {
		t.Fatal(err)
	}
	later := wordIn("D0B6Z", cardRoot, "yes")
	later.TS = "1793831975.000100"

	rows, _, err := later.rows(context.Background(), fake, entry, cardRoute{})
	if err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(rows, []string{"m2"}) {
		t.Fatalf("rows = %q, want a row of its own", rows)
	}
}

func TestABoardTapRowCarriesNoThreadWord(t *testing.T) {
	fake, entry := wordEntry(t)
	seedLedger(t, entry, "| m9 | Nov 4 2:37 PM | Owner on board d50e80e8, Land it? (c): picked “Keep the nightly report running” |  | addressed |\n")

	rows, _, err := wordIn("D0B6Z", cardRoot, "keep the nightly report running").rows(context.Background(), fake, entry, cardRoute{})
	if err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(rows, []string{"m10"}) {
		t.Fatalf("rows = %q, want a pick with words read by its own words", rows)
	}
}

func cardReply(t *testing.T, session string) threadWord {
	t.Helper()
	var word threadWord
	message := `{"channel_id":"D0B6Z","thread_ts":"1793831048.292899","ts":"1793831915.566039","text":"tag the engines","thread_owner":{"session":"` + session + `","card":"c"}}`
	if err := json.Unmarshal([]byte(message), &word); err != nil {
		t.Fatal(err)
	}
	return word
}

func TestAReplyOnACardOfASessionOffThisDriveMakesNoRow(t *testing.T) {
	fake, entry := wordEntry(t)
	fake.Reply("", "cci", "who")

	rows, _, err := cardReply(t, "other").rows(context.Background(), fake, entry, cardRoute{})

	if err != nil || rows != nil {
		t.Fatalf("rows = %q, err = %v, want none", rows, err)
	}
}

func TestAReplyOnALanesCardOnThisDriveMakesARow(t *testing.T) {
	fake, entry := wordEntry(t)
	fake.Reply(`{"session":"lane"}`, "cci", "who")

	rows, _, err := cardReply(t, "lane").rows(context.Background(), fake, entry, cardRoute{})
	if err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(rows, []string{"m1"}) {
		t.Fatalf("rows = %q, want m1", rows)
	}
}

func TestSeveralAsksGetOneRowEach(t *testing.T) {
	fake, entry := wordEntry(t)

	rows, _, err := wordIn("D0B6Z", cardRoot, "ship the relay fix, also fix the stop hook so it waits").rows(context.Background(), fake, entry, cardRoute{})
	if err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(rows, []string{"m1.1", "m1.2"}) {
		t.Fatalf("rows = %q, want one per ask", rows)
	}
	if got := len(posted(fake)); got != 2 {
		t.Fatalf("posted %d records, want one per ask", got)
	}
}

func TestAsksSplitsAMessageAsTheTerminalPathDoes(t *testing.T) {
	seven := strings.TrimSuffix(strings.Repeat("fix the bug number in the relay\n\n", 7), "\n\n")
	cases := map[string][]string{
		"ship the relay fix, also fix the stop hook so it waits":                                                                                 {"ship the relay fix", "also fix the stop hook so it waits"},
		"ok so two separate issues, for the first chat explain how a new build started it, then for the second user explain what their issue is": {"ok so two separate issues, for the first chat explain how a new build started it", "then for the second user explain what their issue is"},
		"is the old failure the same bug or another? also recap the last hour with an ETA":                                                       {"is the old failure the same bug or another?", "also recap the last hour with an ETA"},
		"two issues here, the relay drops a write, the stop hook waits, and the dashboard lags":                                                  {"two issues here, the relay drops a write, the stop hook waits, and the dashboard lags"},
		"is it live? ok": {"is it live? ok"},
		"> why is the old build in the new format?\nyou never answered that one, so answer it now": {"> why is the old build in the new format?\nyou never answered that one, so answer it now"},
		seven: {seven},
	}
	for text, want := range cases {
		if got := asks(text); !reflect.DeepEqual(got, want) {
			t.Errorf("asks(%q) = %q, want %q", text, got, want)
		}
	}
}

func TestACredentialInHisWordsIsRedactedFromTheRowAndTheRecord(t *testing.T) {
	cases := map[string]string{
		"use xoxb-1234567890abcdef for it":                                                        "use [credential redacted] for it",
		"key is aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF, ok":                                             "key is [credential redacted], ok",
		"see https://acme.slack.com/archives/D0B6Z/p1793831915566039?thread_ts=1793831048.292899": "see https://acme.slack.com/archives/D0B6Z/p1793831915566039?thread_ts=1793831048.292899",
		"a long lowercase word like pneumonoultramicroscopicsilicovolcanoconiosis stays":          "a long lowercase word like pneumonoultramicroscopicsilicovolcanoconiosis stays",
	}
	for text, want := range cases {
		if got := redact(text); got != want {
			t.Errorf("redact(%q) = %q, want %q", text, got, want)
		}
	}
}

func TestAShortLineStaysAsCcSlackPrintedIt(t *testing.T) {
	got, err := fitted([]byte(hisEvent), nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != hisEvent {
		t.Fatalf("got %s, want it unchanged", got)
	}
}

func TestEveryLineOfHisFitsAMonitorEventAndStaysWholeJSON(t *testing.T) {
	long := strings.Repeat("word ", 300)
	options := make([]string, 0, 8)
	for range 8 {
		options = append(options, strings.Repeat("an option ", 8))
	}
	event, err := json.Marshal(map[string]any{
		"type": "slack.message", "channel_id": "D0B6Z", "thread_ts": cardRoot, "ts": repliedAt, "user": "U1", "text": long,
		"permalink": "https://acme.slack.com/archives/D0B6Z/p1793831915566039?thread_ts=1793831048.292899&cid=D0B6Z",
		"answers":   map[string]any{"card": "card-x", "lane": "owner-relay", "question": strings.Repeat("why ", 50), "options": options},
		"linked":    map[string]any{"url": "https://acme.slack.com/archives/D0B6Z/p1", "text": long},
	})
	if err != nil {
		t.Fatal(err)
	}

	got, err := fitted(event, []string{"m12"}, nil)
	if err != nil {
		t.Fatal(err)
	}

	if len(got) > eventChars {
		t.Fatalf("line is %d bytes, want at most %d", len(got), eventChars)
	}
	var line struct {
		TS      string   `json:"ts"`
		Thread  string   `json:"thread_ts"`
		Rows    []string `json:"rows"`
		Cut     bool     `json:"cut"`
		Text    string   `json:"text"`
		Answers struct {
			Card     string `json:"card"`
			Question string `json:"question"`
		} `json:"answers"`
	}
	if err := json.Unmarshal(got, &line); err != nil {
		t.Fatalf("line %s: %v", got, err)
	}
	if line.TS != repliedAt || line.Thread != cardRoot || !slices.Equal(line.Rows, []string{"m12"}) || !line.Cut || !strings.HasPrefix(long, line.Text) || line.Answers.Card != "card-x" || line.Answers.Question != "" {
		t.Fatalf("line = %+v", line)
	}
}

func TestALineThatFitsOnceTheCardWordsGoKeepsAllHisWords(t *testing.T) {
	said := strings.Repeat("word ", 40)
	event, err := json.Marshal(map[string]any{
		"type": "slack.message", "ts": repliedAt, "text": said,
		"answers": map[string]any{"card": "card-x", "question": strings.Repeat("why ", 100)},
	})
	if err != nil {
		t.Fatal(err)
	}

	got, err := fitted(event, nil, nil)
	if err != nil {
		t.Fatal(err)
	}

	var line map[string]any
	if err := json.Unmarshal(got, &line); err != nil {
		t.Fatal(err)
	}
	if line["text"] != said || line[cutField] != nil {
		t.Fatalf("line = %v, want his words whole", line)
	}
}

func TestTheWatchWritesARowFromTheWholeMessageAndPrintsItsIDOnAFittedLine(t *testing.T) {
	long := strings.Repeat("word ", 300)
	event, err := json.Marshal(map[string]any{"type": "slack.message", "channel_id": "D0B6Z", "thread_ts": cardRoot, "ts": repliedAt, "user": "U1", "from_claude": false, "text": long})
	if err != nil {
		t.Fatal(err)
	}
	var heard []string
	rows := func(_ context.Context, word threadWord) ([]string, []string, error) {
		heard = append(heard, word.Text)
		return []string{"m12"}, nil, nil
	}
	var out strings.Builder

	if err := watch(context.Background(), watched(string(event)), noThreads, rows, &out); err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(heard, []string{long}) {
		t.Fatalf("heard = %q, want the whole message", heard)
	}
	printed := strings.TrimSpace(strings.TrimPrefix(out.String(), started))
	if len(printed) > eventChars || !strings.Contains(printed, `"rows":["m12"]`) {
		t.Fatalf("printed %s", printed)
	}
}

func TestARowThatDoesNotWriteIsSaidAndTheStreamGoesOn(t *testing.T) {
	rows := func(context.Context, threadWord) ([]string, []string, error) { return nil, nil, os.ErrPermission }
	var out strings.Builder

	if err := watch(context.Background(), watched(hisEvent), noThreads, rows, &out); err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(out.String(), "wrote no ledger row") || !strings.Contains(out.String(), hisEvent) {
		t.Fatalf("printed %s", out.String())
	}
}

func TestAMessageAtTheDMRootWritesNoCardRow(t *testing.T) {
	book, entry := booked(t)
	root := wordOn("1793818260.000100")

	if err := root.record(context.Background(), book, noThreads, entry, "s1"); err != nil {
		t.Fatal(err)
	}

	if len(book.rows) != 0 || len(book.Calls()) != 0 {
		t.Fatalf("rows = %q, calls = %v, want neither", book.rows, book.Calls())
	}
}

const (
	draftCard = "card-chat-tables-draft"
	drafter   = "inbox-outbox-design-1"
)

func drewBy(card, lane string, looked *int) LaneOf {
	return LaneOf{
		Card: func(_ *store.Store, asked string) (string, error) {
			*looked++
			if asked == card {
				return lane, nil
			}
			return "", nil
		},
		PR: func(*store.Store, int) (string, error) {
			*looked++
			return "", nil
		},
	}
}

func ownedBy(owners map[int]string, looked *int) LaneOf {
	return LaneOf{
		Card: func(*store.Store, string) (string, error) {
			*looked++
			return "", nil
		},
		PR: func(_ *store.Store, pr int) (string, error) {
			*looked++
			return owners[pr], nil
		},
	}
}

func routedEntry(t *testing.T) (*shelltest.Fake, drive.Entry, *store.Store) {
	t.Helper()
	fake, entry := wordEntry(t)
	entry.Sessions = []string{"root-session"}
	return fake, entry, threadStore(t)
}

func heard(t *testing.T, fake *shelltest.Fake, s *store.Store, entry drive.Entry, lane LaneOf, word threadWord) []string {
	t.Helper()
	route, err := word.route(s, lane, entry)
	if err != nil {
		t.Fatal(err)
	}
	_, routed, err := word.rows(context.Background(), fake, entry, route)
	if err != nil {
		t.Fatal(err)
	}
	return routed
}

func TestHisWordsInACardsThreadGoOnceToTheLaneThatDrewItNamingTheCard(t *testing.T) {
	fake, entry, s := routedEntry(t)
	put(t, s, store.SlackThread{TS: cardRoot, Card: draftCard, Lane: "board-refresher-7"})
	word := wordIn("D0B6Z", cardRoot, "the draft reads right, but drop the per-table queue")
	looked := 0
	lane := drewBy(draftCard, drafter, &looked)

	first := heard(t, fake, s, entry, lane, word)
	second := heard(t, fake, s, entry, lane, word)

	want := []string{"cci", "post", "--drive", entry.Bus(), "--lane", "owner-relay", "--kind", "owner", "--id", "owner:m1", "--attr", "row=m1", "--attr", "card=" + draftCard, "--text", "Owner's words in the Slack thread of " + draftCard + ":", "--to", drafter, "--to", "owner-relay", "--quoted", word.Text}
	if got := posted(fake); len(got) != 1 || !slices.Equal(got[0], want) {
		t.Fatalf("posted = %q, want one record %q", got, want)
	}
	if !slices.Equal(first, []string{drafter, "owner-relay"}) || second != nil {
		t.Fatalf("routed %q then %q, want the drafter and the relay once, then nothing", first, second)
	}
}

func TestACardCopysThreadNamedBySlackGoesToTheCardsLane(t *testing.T) {
	fake, entry, s := routedEntry(t)
	word := wordIn("D0B6Z", cardRoot, "fine to land once the loader is shared")
	word.Card = draftCard
	looked := 0

	routed := heard(t, fake, s, entry, drewBy(draftCard, drafter, &looked), word)

	if !slices.Equal(routed, []string{drafter, "owner-relay"}) {
		t.Fatalf("routed %q, want the drafter and the relay", routed)
	}
}

func TestACardWhoseLaneIsGoneNamesTheCardAndAddressesNoLane(t *testing.T) {
	fake, entry, s := routedEntry(t)
	put(t, s, store.SlackThread{TS: cardRoot, Card: draftCard})
	word := wordIn("D0B6Z", cardRoot, "where did the shared queue go?")
	looked := 0

	routed := heard(t, fake, s, entry, drewBy("card-other", drafter, &looked), word)

	got := posted(fake)
	if len(got) != 1 || slices.Contains(got[0], "--to") || !slices.Contains(got[0], "card="+draftCard) || routed != nil {
		t.Fatalf("posted = %q, routed = %q, want one record naming the card and addressing no lane", got, routed)
	}
}

func TestHisWordsInAThreadNoCardHoldsPostAsBefore(t *testing.T) {
	fake, entry, s := routedEntry(t)
	put(t, s, store.SlackThread{TS: cardRoot, Reply: "r1", Lane: "owner-relay"})
	word := wordIn("D0B6Z", cardRoot, "make the smoke less flaky")
	looked := 0

	routed := heard(t, fake, s, entry, drewBy(draftCard, drafter, &looked), word)

	want := []string{"cci", "post", "--drive", entry.Bus(), "--lane", "owner-relay", "--kind", "owner", "--id", "owner:m1", "--attr", "row=m1", "--quoted", word.Text}
	if got := posted(fake); len(got) != 1 || !slices.Equal(got[0], want) || routed != nil || looked != 0 {
		t.Fatalf("posted = %q, routed = %q, lane reads = %d, want %q alone", got, routed, looked, want)
	}
}

func TestACardAnotherSessionOnThisDrivePostedGoesToThatSessionAlone(t *testing.T) {
	fake, entry, s := routedEntry(t)
	fake.Reply(`{"session":"lane"}`, "cci", "who")
	looked := 0

	routed := heard(t, fake, s, entry, drewBy("c", drafter, &looked), cardReply(t, "lane"))

	if !slices.Equal(routed, []string{"peer:lane"}) || looked != 0 {
		t.Fatalf("routed %q, lane reads = %d, want the posting session alone", routed, looked)
	}
}

func TestRoutingAMessageReadsOneCardLaneWhateverTheCardCount(t *testing.T) {
	lookups := func(cards int) int {
		fake, entry, s := routedEntry(t)
		for i := range cards {
			put(t, s, store.SlackThread{TS: fmt.Sprintf("%d.5", i), Card: fmt.Sprintf("card-%d", i)})
		}
		put(t, s, store.SlackThread{TS: cardRoot, Card: draftCard})
		looked := 0
		heard(t, fake, s, entry, drewBy(draftCard, drafter, &looked), wordIn("D0B6Z", cardRoot, "send the corrected draft"))
		return looked
	}
	if one, many := lookups(1), lookups(scaletest.N()); one != 1 || many != 1 {
		t.Fatalf("card lane reads %d at 1 card and %d at %d", one, many, scaletest.N())
	}
}

const (
	sidecarPR = 48159
	prLane    = "pr-unblock-18"
)

func TestHisWordsInAReplyThreadOfAPullRequestGoOnceToTheLaneThatOwnsIt(t *testing.T) {
	fake, entry, s := routedEntry(t)
	put(t, s, store.SlackThread{TS: cardRoot, Reply: "rmw5coyh9", Lane: "owner-relay", PRs: []int{sidecarPR}})
	word := wordIn("D0B6Z", cardRoot, "fine to land once the loader is shared")
	looked := 0
	lane := ownedBy(map[int]string{sidecarPR: prLane}, &looked)

	first := heard(t, fake, s, entry, lane, word)
	second := heard(t, fake, s, entry, lane, word)

	want := []string{"cci", "post", "--drive", entry.Bus(), "--lane", "owner-relay", "--kind", "owner", "--id", "owner:m1", "--attr", "row=m1", "--attr", "pr=48159", "--text", "Owner's words in the Slack thread of #48159:", "--to", prLane, "--to", "owner-relay", "--quoted", word.Text}
	if got := posted(fake); len(got) != 1 || !slices.Equal(got[0], want) {
		t.Fatalf("posted = %q, want one record %q", got, want)
	}
	if !slices.Equal(first, []string{prLane, "owner-relay"}) || second != nil {
		t.Fatalf("routed %q then %q, want the PR's lane and the relay once, then nothing", first, second)
	}
}

func TestAReplyThreadOfSeveralPullRequestsGoesToEachOwnerOnce(t *testing.T) {
	fake, entry, s := routedEntry(t)
	put(t, s, store.SlackThread{TS: cardRoot, Reply: "r1", PRs: []int{sidecarPR, 51995, 52013}})
	looked := 0

	routed := heard(t, fake, s, entry, ownedBy(map[int]string{sidecarPR: prLane, 51995: "run-search-shape-2", 52013: prLane}, &looked), wordIn("D0B6Z", cardRoot, "land all three"))

	if !slices.Equal(routed, []string{prLane, "run-search-shape-2", "owner-relay"}) {
		t.Fatalf("routed %q, want each owner once and the relay", routed)
	}
	if got := posted(fake); len(got) != 1 || !slices.Contains(got[0], "pr=48159,51995,52013") {
		t.Fatalf("posted = %q, want the record naming every PR", got)
	}
}

func TestAReplyThreadWhosePullRequestHasNoLiveOwnerNamesItAndAddressesNoLane(t *testing.T) {
	fake, entry, s := routedEntry(t)
	put(t, s, store.SlackThread{TS: cardRoot, Reply: "r1", PRs: []int{sidecarPR}})
	looked := 0

	routed := heard(t, fake, s, entry, ownedBy(nil, &looked), wordIn("D0B6Z", cardRoot, "is this still open?"))

	got := posted(fake)
	if len(got) != 1 || slices.Contains(got[0], "--to") || !slices.Contains(got[0], "pr=48159") || routed != nil {
		t.Fatalf("posted = %q, routed = %q, want one record naming the PR and addressing no lane", got, routed)
	}
}

func TestACardThreadThatAlsoNamesAPullRequestGoesToTheCardsLane(t *testing.T) {
	fake, entry, s := routedEntry(t)
	put(t, s, store.SlackThread{TS: cardRoot, Card: draftCard, PRs: []int{sidecarPR}})
	looked := 0

	routed := heard(t, fake, s, entry, drewBy(draftCard, drafter, &looked), wordIn("D0B6Z", cardRoot, "the second option"))

	if got := posted(fake); !slices.Equal(routed, []string{drafter, "owner-relay"}) || len(got) != 1 || slices.Contains(got[0], "pr=48159") || looked != 1 {
		t.Fatalf("posted = %q, routed = %q, lane reads = %d, want the card's lane alone", got, routed, looked)
	}
}
