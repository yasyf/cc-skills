package owner

import (
	"context"
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	cardBoard = `{"doc":{"blocks":[
		{"id":"note","type":"markdown"},
		{"id":"card-under-a-thread","type":"card","title":"Under his thread?","slack":{"url":"https://acme.slack.com/archives/D0B6Z/p1793000000000009?thread_ts=1793000000.000002"}},
		{"id":"card-chat","type":"card","title":"Put Chat live?","slack":{"url":"https://acme.slack.com/archives/D0B6Z/p1793817344717479"},
		 "children":[{"id":"card-chat-why","type":"markdown"},{"id":"card-chat-pick","type":"choice"}]}
	]}}`
	yesWords = "Yes, ship this one"
)

type answerBook struct {
	*shelltest.Fake
	rows []string
}

func (b *answerBook) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	switch {
	case len(c.Argv) > 4 && c.Argv[3] == "answer" && c.Argv[4] == "list":
		proof := c.Argv[slices.Index(c.Argv, "--label")+1]
		for _, row := range b.rows {
			if strings.Contains(row, proof) {
				return shell.Result{Stdout: `[{"id":"a1"}]`}, nil
			}
		}
		return shell.Result{Stdout: "[]"}, nil
	case len(c.Argv) > 4 && c.Argv[3] == "answer" && c.Argv[4] == "add":
		b.rows = append(b.rows, strings.Join(c.Argv, "\x00"))
		return shell.Result{Stdout: `{"id":"a1"}`}, nil
	}
	return b.Fake.Run(ctx, c)
}

func booked(t *testing.T) (*answerBook, drive.Entry) {
	t.Helper()
	fake := &shelltest.Fake{}
	fake.Reply(cardBoard, "cc-present", "outcomes")
	return &answerBook{Fake: fake}, drive.Entry{Checkout: "/drive", StateDir: t.TempDir(), Program: "rebuild"}
}

func wordOn(thread string) threadWord {
	return threadWord{Channel: "D0B6Z", Thread: thread, TS: "1793818260.000100", Text: yesWords}
}

func argsAfter(row, flag string) []string {
	argv := strings.Split(row, "\x00")
	var found []string
	for i, arg := range argv[:len(argv)-1] {
		if arg == flag {
			found = append(found, argv[i+1])
		}
	}
	return found
}

func TestHisWordsInACardsThreadWriteTheRowTheLiveGateReads(t *testing.T) {
	book, entry := booked(t)

	if err := wordOn("1793817344.717479").record(context.Background(), book, noThreads, entry, "s1"); err != nil {
		t.Fatal(err)
	}

	if len(book.rows) != 1 {
		t.Fatalf("rows = %q, want one", book.rows)
	}
	labels := argsAfter(book.rows[0], "--label")
	for _, want := range []string{"from:owner", "board-answer", "program:rebuild", "board-session:s1", "board-card:card-chat", "proof:slack-thread:1793818260.000100"} {
		if !slices.Contains(labels, want) {
			t.Errorf("labels = %q, want %s", labels, want)
		}
	}
	if slices.ContainsFunc(labels, func(label string) bool { return strings.HasPrefix(label, "board-block:") }) {
		t.Errorf("labels = %q, a typed word must not stand as the row of a later tap on the block", labels)
	}
	argv := strings.Split(book.rows[0], "\x00")
	if !slices.Equal(argv[:6], []string{"ccn", "-R", "/drive", "answer", "add", "Put Chat live?"}) {
		t.Errorf("argv = %q, want an add in the drive checkout titled with the card's question", argv[:6])
	}
	body := argsAfter(book.rows[0], "--body")[0]
	if !strings.HasPrefix(body, yesWords+"\n") {
		t.Errorf("body = %q, want his words verbatim first", body)
	}
	for _, want := range []string{"Card card-chat", "block card-chat-pick", "board s1", "Nov 4 10:51 AM PT"} {
		if !strings.Contains(body, want) {
			t.Errorf("body = %q, want %q", body, want)
		}
	}
}

func TestAReplayOfTheSameMessageWritesNoSecondRow(t *testing.T) {
	book, entry := booked(t)

	for range 2 {
		if err := wordOn("1793817344.717479").record(context.Background(), book, noThreads, entry, "s1"); err != nil {
			t.Fatal(err)
		}
	}

	if len(book.rows) != 1 {
		t.Fatalf("rows = %q, want one across the replay", book.rows)
	}
}

func TestAThreadWithNoCardOfItsOwnWritesNoRow(t *testing.T) {
	cases := map[string]string{
		"his own thread":               "1793000000.000001",
		"a thread a card only sits in": "1793000000.000002",
		"a thread named by a later ts": "1793817344.717480",
	}
	for name, thread := range cases {
		t.Run(name, func(t *testing.T) {
			book, entry := booked(t)

			if err := wordOn(thread).record(context.Background(), book, noThreads, entry, "s1"); err != nil {
				t.Fatal(err)
			}

			if len(book.rows) != 0 {
				t.Fatalf("rows = %q, want none", book.rows)
			}
		})
	}
}

func TestAWindowWithNoBoardWritesNoRowAndNoError(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "cc-present: no cc-present artifact for this session", "cc-present", "outcomes")
	book := &answerBook{Fake: fake}

	err := wordOn("1793817344.717479").record(context.Background(), book, noThreads, drive.Entry{Checkout: "/drive", StateDir: t.TempDir()}, "s1")
	if err != nil {
		t.Fatal(err)
	}

	if len(book.rows) != 0 {
		t.Fatalf("rows = %q, want none", book.rows)
	}
}

func TestABoardThatCannotBeReadIsAnError(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "daemon gone", "cc-present", "outcomes")

	err := wordOn("1793817344.717479").record(context.Background(), &answerBook{Fake: fake}, noThreads, drive.Entry{Checkout: "/drive", StateDir: t.TempDir()}, "s1")

	if err == nil || !strings.Contains(err.Error(), "daemon gone") {
		t.Fatalf("err = %v, want the board's failure", err)
	}
}

func TestACardAnotherSessionPostedIsNamedByTheEventWithNoBoardRead(t *testing.T) {
	var word threadWord
	message := `{"channel_id":"D0B6Z","thread_ts":"1793000000.000001","ts":"1793818260.000100","text":"why this one?","thread_owner":{"session":"lane-session","card":"card-lane","title":"Which one?"}}`
	if err := json.Unmarshal([]byte(message), &word); err != nil {
		t.Fatal(err)
	}
	book := &answerBook{Fake: &shelltest.Fake{}}

	if err := word.record(context.Background(), book, noThreads, drive.Entry{Checkout: "/drive", StateDir: t.TempDir(), Program: "rebuild"}, "holder-session"); err != nil {
		t.Fatal(err)
	}

	if len(book.rows) != 1 {
		t.Fatalf("rows = %q, want one", book.rows)
	}
	labels := argsAfter(book.rows[0], "--label")
	if !slices.Contains(labels, "board-card:card-lane") || !slices.Contains(labels, "board-session:lane-session") {
		t.Fatalf("labels = %q, want the card and session the event names", labels)
	}
	if !strings.HasPrefix(argsAfter(book.rows[0], "--body")[0], "why this one?\n") {
		t.Fatalf("row = %q, want a question stays his words on the card, unjudged", book.rows[0])
	}
}

func TestAThreadAnotherSessionPostedWithNoCardWritesNoRow(t *testing.T) {
	var word threadWord
	message := `{"channel_id":"D0B6Z","thread_ts":"1793000000.000001","ts":"1793818260.000100","text":"thanks","thread_owner":{"session":"lane-session"}}`
	if err := json.Unmarshal([]byte(message), &word); err != nil {
		t.Fatal(err)
	}
	book := &answerBook{Fake: &shelltest.Fake{}}

	if err := word.record(context.Background(), book, noThreads, drive.Entry{Checkout: "/drive", StateDir: t.TempDir()}, "holder-session"); err != nil {
		t.Fatal(err)
	}

	if len(book.rows) != 0 {
		t.Fatalf("rows = %q, want none", book.rows)
	}
}

func TestACardOffTheBoardIsStillFoundInTheThreadIndex(t *testing.T) {
	book := &answerBook{Fake: &shelltest.Fake{}}
	indexed := func(ts string) (store.SlackThread, bool, error) {
		return store.SlackThread{TS: ts, Card: "card-gone", Question: "Keep it?"}, true, nil
	}
	entry := drive.Entry{Checkout: "/drive", StateDir: t.TempDir(), Program: "rebuild", Sessions: []string{"old-root", "root"}}

	if err := wordOn("1793817344.717479").record(context.Background(), book, indexed, entry, "s1"); err != nil {
		t.Fatal(err)
	}

	if len(book.rows) != 1 {
		t.Fatalf("rows = %q, want one", book.rows)
	}
	labels := argsAfter(book.rows[0], "--label")
	if !slices.Contains(labels, "board-card:card-gone") || !slices.Contains(labels, "board-session:root") {
		t.Fatalf("labels = %q, want the indexed card on the coordinator's board", labels)
	}
	if !slices.Contains(strings.Split(book.rows[0], "\x00"), "Keep it?") {
		t.Fatalf("row = %q, want the indexed question as its title", book.rows[0])
	}
	if len(book.Calls()) != 0 {
		t.Fatalf("calls = %v, want no board read", book.Calls())
	}
}

func TestAThreadIndexedWithNoCardStillReadsThisWindowsBoard(t *testing.T) {
	book, entry := booked(t)
	replyOnly := func(ts string) (store.SlackThread, bool, error) {
		return store.SlackThread{TS: ts, Reply: "r1"}, true, nil
	}

	if err := wordOn("1793817344.717479").record(context.Background(), book, replyOnly, entry, "lane-session"); err != nil {
		t.Fatal(err)
	}

	if len(book.rows) != 1 || !slices.Contains(argsAfter(book.rows[0], "--label"), "board-session:lane-session") {
		t.Fatalf("rows = %q, want the card on this window's own board", book.rows)
	}
}
