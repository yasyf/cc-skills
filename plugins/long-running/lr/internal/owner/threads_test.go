package owner

import (
	"bytes"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	permalink = "https://poetic.slack.com/archives/D0B6Z/p1793822112255849"
	inThread  = permalink + "?thread_ts=1793800000.000100&cid=D0B6Z"
	rootTS    = "1793822112.255849"
)

func threadStore(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func put(t *testing.T, s *store.Store, thread store.SlackThread) {
	t.Helper()
	if err := s.PutSlackThread(thread); err != nil {
		t.Fatal(err)
	}
}

func emitted(t *testing.T, s *store.Store, runner streamed, line string) map[string]any {
	t.Helper()
	runner.lines = []string{line}
	var out bytes.Buffer
	if err := watch(context.Background(), runner, s.SlackThreadAt, noRows, &out); err != nil {
		t.Fatal(err)
	}
	last := strings.TrimSpace(strings.TrimPrefix(out.String(), started))
	var got map[string]any
	if err := json.Unmarshal([]byte(last), &got); err != nil {
		t.Fatalf("printed %q: %v", out.String(), err)
	}
	return got
}

func TestAMessageInAReplysThreadEmitsItsIDRowAndLane(t *testing.T) {
	s := threadStore(t)
	put(t, s, store.SlackThread{TS: "1.0", Reply: "rmw2je2fi", Lane: "owner-relay", Row: "m680"})

	got := emitted(t, s, watched(), hisEvent)

	want := map[string]any{"reply": "rmw2je2fi", "lane": "owner-relay", "row": "m680"}
	if !reflect.DeepEqual(got["answers"], want) {
		t.Fatalf("answers = %v, want %v", got["answers"], want)
	}
	if got["text"] != "why no reply" || got["ts"] != "1.1" {
		t.Fatalf("his event lost its fields: %v", got)
	}
}

func TestAMessageInACardsThreadEmitsTheCardItsQuestionAndOptions(t *testing.T) {
	s := threadStore(t)
	put(t, s, store.SlackThread{TS: "1.0", Card: "card-sofi", Lane: "board-refresher", Question: "Tell the FDE?", Options: []string{"Tell", "Leave"}})

	got := emitted(t, s, watched(), hisEvent)

	want := map[string]any{"card": "card-sofi", "lane": "board-refresher", "question": "Tell the FDE?", "options": []any{"Tell", "Leave"}}
	if !reflect.DeepEqual(got["answers"], want) {
		t.Fatalf("answers = %v, want %v", got["answers"], want)
	}
}

func TestAMessageInAnUnknownThreadEmitsPlainly(t *testing.T) {
	runner := watched(hisEvent)
	var out bytes.Buffer

	if err := watch(context.Background(), runner, threadStore(t).SlackThreadAt, noRows, &out); err != nil {
		t.Fatal(err)
	}

	if out.String() != started+hisEvent+"\n" {
		t.Fatalf("printed %q, want his event unchanged", out.String())
	}
}

func TestAThreadThatDoesNotReadStillDeliversHisMessage(t *testing.T) {
	broken := func(string) (store.SlackThread, bool, error) {
		return store.SlackThread{}, false, fmt.Errorf("database is locked")
	}
	var out bytes.Buffer

	if err := watch(context.Background(), watched(hisEvent), broken, noRows, &out); err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(out.String(), "database is locked") || !strings.Contains(out.String(), hisEvent) {
		t.Fatalf("printed %q, want the miss and his message", out.String())
	}
}

func TestReadingTheJoinStaysFlatAsRepliesGrow(t *testing.T) {
	calls := func(replies int) (runs, reads int) {
		s := threadStore(t)
		for i := range replies {
			put(t, s, store.SlackThread{TS: fmt.Sprintf("%d.5", i), Reply: fmt.Sprintf("r%d", i), Lane: "owner-relay"})
		}
		runner := watched(hisEvent)
		counted := func(ts string) (store.SlackThread, bool, error) {
			reads++
			return s.SlackThreadAt(ts)
		}
		if err := watch(context.Background(), runner, counted, noRows, &bytes.Buffer{}); err != nil {
			t.Fatal(err)
		}
		return len(runner.Calls()), reads
	}
	oneRuns, oneReads := calls(1)
	manyRuns, manyReads := calls(1000)
	if oneReads != 1 || manyReads != 1 || oneRuns != manyRuns {
		t.Fatalf("reads %d at 1 and %d at 1,000; commands %d and %d", oneReads, manyReads, oneRuns, manyRuns)
	}
}

func TestABareSlackLinkEmitsTheMessageItNamesAndWhatItAnswers(t *testing.T) {
	s := threadStore(t)
	put(t, s, store.SlackThread{TS: rootTS, Card: "card-sofi", Question: "Tell the FDE?"})
	for _, text := range []string{"<" + permalink + ">", "<" + permalink + "|a card>", permalink} {
		runner := watched()
		runner.Reply(`{"messages":[{"ts":"1793822112.000001","text":"another"},{"ts":"`+rootTS+`","user_name":"Yasyf (agent)","text":"Four SoFi runs crashed"}]}`, "cc-slack", "thread")
		line, err := json.Marshal(map[string]any{"type": slackMessage, "channel_id": "D0B6Z", "thread_ts": "2.0", "ts": "2.0", "user": "U1", "text": text})
		if err != nil {
			t.Fatal(err)
		}

		got := emitted(t, s, runner, string(line))

		want := map[string]any{"url": permalink, "user_name": "Yasyf (agent)", "text": "Four SoFi runs crashed", "answers": map[string]any{"card": "card-sofi", "question": "Tell the FDE?"}}
		if !reflect.DeepEqual(got["linked"], want) {
			t.Fatalf("%s: linked = %v, want %v", text, got["linked"], want)
		}
		if got["text"] != text {
			t.Fatalf("his words changed to %v", got["text"])
		}
	}
}

func TestWordsAroundALinkReadNoLink(t *testing.T) {
	runner := watched()
	line, err := json.Marshal(map[string]any{"type": slackMessage, "channel_id": "D0B6Z", "thread_ts": "2.0", "ts": "2.0", "user": "U1", "text": "see <" + permalink + "> please"})
	if err != nil {
		t.Fatal(err)
	}

	got := emitted(t, threadStore(t), runner, string(line))

	if got["linked"] != nil || slices.ContainsFunc(runner.Calls(), func(c shell.Command) bool { return c.Argv[1] == "thread" }) {
		t.Fatalf("linked = %v after a read of the thread", got["linked"])
	}
}

func TestALinkThatDoesNotReadStillDeliversHisMessage(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"user_id":"U1"}`, "cc-slack", "whoami")
	fake.Reply(`{"ok":true}`, "cc-slack", "react")
	fake.Fail(1, "channel_not_found", "cc-slack", "thread")
	line := `{"type":"slack.message","channel_id":"D0B6Z","thread_ts":"2.0","ts":"2.0","user":"U1","text":"` + permalink + `"}`
	var out bytes.Buffer

	if err := watch(context.Background(), streamed{Fake: fake, lines: []string{line}}, threadStore(t).SlackThreadAt, noRows, &out); err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(out.String(), "channel_not_found") || !strings.Contains(out.String(), line) {
		t.Fatalf("printed %q, want the miss and his message", out.String())
	}
}

func TestAPermalinkNamesItsThreadsRoot(t *testing.T) {
	for link, want := range map[string]string{permalink: rootTS, inThread: "1793800000.000100", "#4242": "", "": ""} {
		if got := ThreadRoot(link); got != want {
			t.Fatalf("ThreadRoot(%q) = %q, want %q", link, got, want)
		}
	}
}

func TestOnlyAPickReplyAsksAQuestion(t *testing.T) {
	plain := ReplyThread(inThread, "r1", "m7", "Fixed", nil)
	if want := (store.SlackThread{TS: "1793800000.000100", Reply: "r1", Lane: "owner-relay", Row: "m7"}); !reflect.DeepEqual(plain, want) {
		t.Fatalf("plain = %+v", plain)
	}
	picked := ReplyThread(permalink, "r2", "", "Which?", []string{"A", "B"})
	if want := (store.SlackThread{TS: rootTS, Reply: "r2", Card: "card-r2", Lane: "owner-relay", Question: "Which?", Options: []string{"A", "B"}}); !reflect.DeepEqual(picked, want) {
		t.Fatalf("picked = %+v", picked)
	}
}

func cardEvent(lane, url string) string {
	slack := ""
	if url != "" {
		slack = `,"slack":{"url":"` + url + `"}`
	}
	return `{"type":"block.upserted","lane":"` + lane + `","block":{"id":"card-a","type":"card","title":"Which?"` + slack + `,"children":[{"type":"markdown"},{"type":"choice","options":[{"label":"Yes"},{"label":"No"}]}]}}`
}

func TestAPostedCardWritesItsThreadRowWithTheLaneThatDrewIt(t *testing.T) {
	s := threadStore(t)

	if err := indexUpsert(s, []byte(cardEvent("drawer", permalink))); err != nil {
		t.Fatal(err)
	}
	if err := indexUpsert(s, []byte(cardEvent("slack-answer", permalink))); err != nil {
		t.Fatal(err)
	}

	got, found, err := s.SlackThreadAt(rootTS)
	if err != nil || !found {
		t.Fatalf("found %v, err %v", found, err)
	}
	want := store.SlackThread{TS: rootTS, Card: "card-a", Lane: "drawer", Question: "Which?", Options: []string{"Yes", "No"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("row = %+v, want %+v", got, want)
	}
}

func TestABoardEventWithNoSlackPostWritesNoRow(t *testing.T) {
	s := threadStore(t)
	for _, line := range []string{cardEvent("drawer", ""), `{"type":"choice.selected","blockId":"x"}`, `{"type":"block.upserted","block":{"id":"m","type":"markdown","slack":{"url":"` + permalink + `"}}}`} {
		if err := indexUpsert(s, []byte(line)); err != nil {
			t.Fatal(err)
		}
	}
	if _, found, err := s.SlackThreadAt(rootTS); err != nil || found {
		t.Fatalf("found %v, err %v", found, err)
	}
}

func TestTheFirstStartFillsCardsWithLanesAndRepliesAndALaterStartOnlyCards(t *testing.T) {
	s := threadStore(t)
	state := t.TempDir()
	replies := filepath.Join(state, repliesDir)
	if err := os.MkdirAll(replies, 0o750); err != nil {
		t.Fatal(err)
	}
	write := func(id, at, body string) {
		if err := os.WriteFile(filepath.Join(replies, id+".json"), []byte(`{"at":"`+at+`",`+body+`}`), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("older", "2026-11-04T10:00:00Z", `"reply":{"id":"older","order":"m1"},"surfaces":[{"surface":"slack","ref":"`+inThread+`"}]`)
	write("newer", "2026-11-04T11:00:00Z", `"reply":{"id":"newer","order":"m2"},"surfaces":[{"surface":"slack thread","ref":"`+inThread+`"},{"surface":"record","ref":"#9"}]`)
	fake := &shelltest.Fake{}
	fake.Reply(`warning first {"doc":{"blocks":[{"id":"card-a","type":"card","title":"Which?","slack":{"url":"`+permalink+`"},"children":[{"type":"choice","options":[{"label":"Yes"}]}]},{"id":"card-b","type":"card","title":"Unposted"}]}}`, "cc-present", "outcomes")
	fake.Reply("  last written at rev 9166 by lane board-refresher-7\n", "cc-present", "block", "show", "card-a")
	threads := CardThreads{Runner: fake, Entry: drive.Entry{StateDir: state, Sessions: []string{"board"}}}

	for range 2 {
		if err := threads.fill(context.Background(), s); err != nil {
			t.Fatal(err)
		}
	}

	card, _, err := s.SlackThreadAt(rootTS)
	if err != nil {
		t.Fatal(err)
	}
	if want := (store.SlackThread{TS: rootTS, Card: "card-a", Lane: "board-refresher-7", Question: "Which?", Options: []string{"Yes"}}); !reflect.DeepEqual(card, want) {
		t.Fatalf("card row = %+v, want %+v", card, want)
	}
	reply, _, err := s.SlackThreadAt("1793800000.000100")
	if err != nil {
		t.Fatal(err)
	}
	if reply.Reply != "newer" || reply.Row != "m2" {
		t.Fatalf("thread row = %+v, want the newest reply", reply)
	}
	var shown, read int
	for _, call := range fake.Calls() {
		switch call.Argv[1] {
		case "block":
			shown++
		case "outcomes":
			read++
		}
	}
	if shown != 1 || read != 2 {
		t.Fatalf("%d lane reads and %d board reads over two starts, want 1 and 2", shown, read)
	}
}

func TestAFollowerOnAHostWithNoBoardWritesNothing(t *testing.T) {
	s := threadStore(t)
	fake := &shelltest.Fake{}
	fake.Fail(1, noBoard, "cc-present", "outcomes")
	threads := CardThreads{Runner: fake, Entry: drive.Entry{StateDir: t.TempDir(), Sessions: []string{"board"}}}

	if err := threads.fill(context.Background(), s); err != nil {
		t.Fatal(err)
	}
}
