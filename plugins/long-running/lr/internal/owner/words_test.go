package owner

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
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

	rows, err := wordIn("D0B6Z", cardRoot, said).rows(context.Background(), fake, entry)
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

	if _, err := wordIn("D0B6Z", repliedAt, "evaluate a faster disk").rows(context.Background(), fake, entry); err != nil {
		t.Fatal(err)
	}

	if got := ledgerRows(t, entry); len(got) != 1 {
		t.Fatalf("ledger = %+v, want one row", got)
	}
}

func TestHisWordsInAWatchedChannelThreadMakeARowAndAChannelRootDoesNot(t *testing.T) {
	fake, entry := wordEntry(t)

	if _, err := wordIn("C0TEAM", repliedAt, "a post at the channel root").rows(context.Background(), fake, entry); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(Ledger(entry.StateDir)); !os.IsNotExist(err) {
		t.Fatalf("a channel root wrote a ledger: %v", err)
	}

	if _, err := wordIn("C0TEAM", cardRoot, "fix this and make it durable").rows(context.Background(), fake, entry); err != nil {
		t.Fatal(err)
	}
	if got := ledgerRows(t, entry); len(got) != 1 {
		t.Fatalf("ledger = %+v, want the thread reply's row", got)
	}
}

func TestAMessageWithNoWordsMakesNoRow(t *testing.T) {
	fake, entry := wordEntry(t)

	rows, err := wordIn("D0B6Z", cardRoot, "").rows(context.Background(), fake, entry)

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

	first, err := word.rows(context.Background(), fake, entry)
	if err != nil {
		t.Fatal(err)
	}
	if err := Mark(Ledger(entry.StateDir), "m1", addressed, 0); err != nil {
		t.Fatal(err)
	}
	second, err := word.rows(context.Background(), fake, entry)
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

			rows, err := wordIn("D0B6Z", cardRoot, "Once we cut over delete all this code").rows(context.Background(), fake, entry)
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
	if _, err := wordIn("D0B6Z", cardRoot, "yes").rows(context.Background(), fake, entry); err != nil {
		t.Fatal(err)
	}
	later := wordIn("D0B6Z", cardRoot, "yes")
	later.TS = "1793831975.000100"

	rows, err := later.rows(context.Background(), fake, entry)
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

	rows, err := wordIn("D0B6Z", cardRoot, "keep the nightly report running").rows(context.Background(), fake, entry)
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

	rows, err := cardReply(t, "other").rows(context.Background(), fake, entry)

	if err != nil || rows != nil {
		t.Fatalf("rows = %q, err = %v, want none", rows, err)
	}
}

func TestAReplyOnALanesCardOnThisDriveMakesARow(t *testing.T) {
	fake, entry := wordEntry(t)
	fake.Reply(`{"session":"lane"}`, "cci", "who")

	rows, err := cardReply(t, "lane").rows(context.Background(), fake, entry)
	if err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(rows, []string{"m1"}) {
		t.Fatalf("rows = %q, want m1", rows)
	}
}

func TestSeveralAsksGetOneRowEach(t *testing.T) {
	fake, entry := wordEntry(t)

	rows, err := wordIn("D0B6Z", cardRoot, "ship the relay fix, also fix the stop hook so it waits").rows(context.Background(), fake, entry)
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
	got, err := fitted([]byte(hisEvent), nil)
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

	got, err := fitted(event, []string{"m12"})
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

	got, err := fitted(event, nil)
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
	rows := func(_ context.Context, word threadWord) ([]string, error) {
		heard = append(heard, word.Text)
		return []string{"m12"}, nil
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
	rows := func(context.Context, threadWord) ([]string, error) { return nil, os.ErrPermission }
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
