package owner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	hisEvent = `{"type":"slack.message","channel_id":"D0B6Z","thread_ts":"1.0","ts":"1.1","user":"U1","from_claude":false,"text":"why no reply"}`
	bot      = `{"type":"slack.message","channel_id":"D0B6Z","thread_ts":"1.0","ts":"1.2","user":"","from_claude":true,"text":"a lane's card"}`
	asClaude = `{"type":"slack.message","channel_id":"D0B6Z","thread_ts":"1.0","ts":"1.5","user":"U1","from_claude":false,"text":"_(Yasyf's Claude)_ sent through his login"}`
	someone  = `{"type":"slack.message","channel_id":"C1","thread_ts":"1.0","ts":"1.3","user":"U2","from_claude":false,"text":"a teammate"}`
	tapped   = `{"type":"slack.action","channel_id":"D0B6Z","ts":"1.4","label":"Land"}`
	started  = "owner Slack watch running for U1: each of his messages gets :eyes: and wakes this window\n"
)

type streamed struct {
	*shelltest.Fake
	lines []string
	ended error
}

func (s streamed) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if !slices.Equal(c.Argv, []string{"cc-slack", "watch", "--only-delivery", "--whole"}) {
		return s.Fake.Run(ctx, c)
	}
	if _, err := io.WriteString(c.Stdout, strings.Join(s.lines, "\n")+"\n"); err != nil {
		return shell.Result{}, err
	}
	return shell.Result{}, s.ended
}

func noThreads(string) (store.SlackThread, bool, error) {
	return store.SlackThread{}, false, nil
}

func noRows(context.Context, threadWord) ([]string, error) {
	return nil, nil
}

func watched(lines ...string) streamed {
	fake := &shelltest.Fake{}
	fake.Reply(`{"user_id":"U1"}`, "cc-slack", "whoami")
	fake.Reply(`{"ok":true}`, "cc-slack", "react")
	return streamed{Fake: fake, lines: lines}
}

func reactions(fake *shelltest.Fake) [][]string {
	var found [][]string
	for _, call := range fake.Calls() {
		if call.Argv[1] == "react" {
			found = append(found, call.Argv[2:])
		}
	}
	return found
}

func TestHisMessageGetsEyesBeforeItWakesTheWindow(t *testing.T) {
	runner := watched(hisEvent)
	var out bytes.Buffer

	if err := watch(context.Background(), runner, noThreads, noRows, &out); err != nil {
		t.Fatal(err)
	}

	want := [][]string{{"--channel", "D0B6Z", "--ts", "1.1", "--name", "eyes"}}
	if got := reactions(runner.Fake); !slices.EqualFunc(got, want, slices.Equal[[]string]) {
		t.Fatalf("reactions = %q, want eyes on his message", got)
	}
	if out.String() != started+hisEvent+"\n" {
		t.Fatalf("printed %q, want the start line then his event", out.String())
	}
}

func TestOnlyHisOwnWordsGetEyesAndABotsPostWakesNothing(t *testing.T) {
	runner := watched(bot, asClaude, someone, tapped)
	var out bytes.Buffer

	if err := watch(context.Background(), runner, noThreads, noRows, &out); err != nil {
		t.Fatal(err)
	}

	if got := reactions(runner.Fake); len(got) != 0 {
		t.Fatalf("reactions = %q, want none", got)
	}
	if want := started + someone + "\n" + tapped + "\n"; out.String() != want {
		t.Fatalf("printed %q, want a person's message and a tap, with neither bot post", out.String())
	}
}

func TestAFailedReactionStillDeliversHisMessage(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"user_id":"U1"}`, "cc-slack", "whoami")
	fake.Fail(1, "channel_not_found", "cc-slack", "react")
	var out bytes.Buffer

	if err := watch(context.Background(), streamed{Fake: fake, lines: []string{hisEvent}}, noThreads, noRows, &out); err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(out.String(), "eyes did not land on his message 1.1") || !strings.Contains(out.String(), hisEvent) {
		t.Fatalf("printed %q, want the failure and his message", out.String())
	}
}

func TestAWatchThatDiesEndsTheMonitorWithItsCause(t *testing.T) {
	runner := watched(hisEvent)
	runner.ended = errors.New("daemon gone")

	err := watch(context.Background(), runner, noThreads, noRows, io.Discard)

	if err == nil || !strings.Contains(fmt.Sprint(err), "daemon gone") {
		t.Fatalf("err = %v, want the watch's cause", err)
	}
}

func fitsAMonitorEvent(t *testing.T, event any, target any) string {
	t.Helper()
	raw, err := json.Marshal(event)
	if err != nil {
		t.Fatal(err)
	}
	var out strings.Builder
	if err := watch(context.Background(), watched(string(raw)), noThreads, noRows, &out); err != nil {
		t.Fatal(err)
	}
	printed := strings.TrimSuffix(strings.TrimPrefix(out.String(), started), "\n")
	if len(printed) > eventChars {
		t.Fatalf("line is %d bytes, want at most %d: %s", len(printed), eventChars, printed)
	}
	if err := json.Unmarshal([]byte(printed), target); err != nil {
		t.Fatalf("line %s: %v", printed, err)
	}
	return printed
}

func realTap(label string) map[string]any {
	question := "Would a faster disk shorten production runs, and which of these should the next change measure first?"
	return map[string]any{
		"type": "slack.action", "watch_id": 719, "channel_id": "D0C4HQWPLCC", "thread_ts": "1793816299.111929", "ts": "1793816299.111929", "action_ts": "1793819436.927939",
		"label": label, "id": "card-faster-disk-runs", "waits": true, "question": question,
		"options":   []string{"Keep gp3 and measure write waits first", "Raise gp3's limits", "Move journals to io2", "Move to local NVMe nodes"},
		"capture":   `Yasyf answered "` + question + `" with "` + label + `" at 2026-11-04T23:43:21Z on https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1793816299111929`,
		"permalink": "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1793816299111929",
	}
}

type tapLine struct {
	TS       string   `json:"ts"`
	Thread   string   `json:"thread_ts"`
	Channel  string   `json:"channel_id"`
	Label    string   `json:"label"`
	ID       string   `json:"id"`
	Question string   `json:"question"`
	Options  []string `json:"options"`
	Capture  string   `json:"capture"`
	Cut      bool     `json:"cut"`
}

func TestATapKeepsItsCardAndAnswerWholeAndDropsItsRepeatedWordsFirst(t *testing.T) {
	var line tapLine

	fitsAMonitorEvent(t, realTap("Keep gp3 and measure write waits first"), &line)

	if line.ID != "card-faster-disk-runs" || line.Label != "Keep gp3 and measure write waits first" || line.Cut {
		t.Fatalf("line = %+v, want the card id and the answer whole", line)
	}
	if line.Thread != "1793816299.111929" || line.TS != "1793816299.111929" || line.Channel != "D0C4HQWPLCC" {
		t.Fatalf("line = %+v, want where the card is", line)
	}
	if line.Capture != "" || line.Options != nil {
		t.Fatalf("line = %+v, want the capture and options gone before the question", line)
	}
	if !strings.HasPrefix(line.Question, "Would a faster disk") {
		t.Fatalf("line = %+v, want the question kept when it fits", line)
	}
}

func TestATapWithALongQuestionStillFitsAndNamesItsCard(t *testing.T) {
	tap := realTap("Yes")
	tap["question"] = strings.Repeat("why ", 200)
	var line tapLine

	fitsAMonitorEvent(t, tap, &line)

	if line.ID != "card-faster-disk-runs" || line.Label != "Yes" || line.Question != "" || line.Cut {
		t.Fatalf("line = %+v, want the question gone and the answer whole", line)
	}
}

func TestATypedAnswerTooLongForAnEventIsCutAndSaysSo(t *testing.T) {
	typed := strings.Repeat("port it first ", 60)
	tap := realTap(typed)
	tap["other"] = true
	var line tapLine

	fitsAMonitorEvent(t, tap, &line)

	if line.ID != "card-faster-disk-runs" || !line.Cut || !strings.HasPrefix(typed, line.Label) || len(line.Label) < 200 {
		t.Fatalf("line = %+v, want the card id whole and as much of his answer as fits, marked cut", line)
	}
}

func TestATeammatesLongMessageFits(t *testing.T) {
	said := strings.Repeat("a teammate says ", 60)
	var line struct {
		TS   string `json:"ts"`
		Text string `json:"text"`
		Cut  bool   `json:"cut"`
	}

	fitsAMonitorEvent(t, map[string]any{"type": "slack.message", "watch_id": 574, "kind": "thread", "channel_id": "C1", "thread_ts": "1.0", "ts": "1.3", "user": "U2", "user_name": "Ria", "from_claude": false, "text": said, "permalink": "https://acme.slack.com/archives/C1/p13"}, &line)

	if line.TS != "1.3" || !line.Cut || !strings.HasPrefix(said, line.Text) {
		t.Fatalf("line = %+v", line)
	}
}

func TestALongWatchErrorFitsAndNamesItsWatch(t *testing.T) {
	var line struct {
		Watch int64  `json:"watch_id"`
		Error string `json:"error"`
		Cut   bool   `json:"cut"`
	}

	fitsAMonitorEvent(t, map[string]any{"type": "slack.watch_error", "watch_id": 742, "kind": "thread", "channel_id": "C1", "thread_ts": "1.0", "error": strings.Repeat("channel_not_found ", 60)}, &line)

	if line.Watch != 742 || !line.Cut || !strings.HasPrefix(line.Error, "channel_not_found") {
		t.Fatalf("line = %+v", line)
	}
}

func TestAStoppedOwnerWithManyWatchesFitsAndSaysSomeAreGone(t *testing.T) {
	watches := make([]map[string]any, 0, 12)
	for range 12 {
		watches = append(watches, map[string]any{"watch_id": 742, "kind": "thread", "channel_id": "C0B75AL4XEW", "thread_ts": "1793774674.452900"})
	}
	var line struct {
		Agent   string `json:"agent_id"`
		Watches []struct {
			Watch int64 `json:"watch_id"`
		} `json:"watches"`
		Cut bool `json:"cut"`
	}

	fitsAMonitorEvent(t, map[string]any{"type": "slack.owner_stopped", "agent_id": "a1b2", "watches": watches}, &line)

	if line.Agent != "a1b2" || !line.Cut || len(line.Watches) == 0 || len(line.Watches) == len(watches) {
		t.Fatalf("line = %+v", line)
	}
}

func TestARelayLineStaysAsCcSlackPrintedIt(t *testing.T) {
	var line map[string]any

	printed := fitsAMonitorEvent(t, map[string]any{"agent_id": "a1b2", "type": "agent.relay"}, &line)

	if printed != `{"agent_id":"a1b2","type":"agent.relay"}` {
		t.Fatalf("printed %s", printed)
	}
}

func TestAFailureLongerThanAnEventIsClippedAndHisMessageStillArrives(t *testing.T) {
	long := errors.New(strings.Repeat("channel_not_found ", 60))
	var out strings.Builder
	rows := func(context.Context, threadWord) ([]string, error) { return nil, long }

	if err := watch(context.Background(), watched(hisEvent), noThreads, rows, &out); err != nil {
		t.Fatal(err)
	}

	failure, delivered, ok := strings.Cut(strings.TrimPrefix(out.String(), started), "\n")
	if !ok || !strings.HasPrefix(failure, "his message 1.1 wrote no ledger row") || len(failure) != eventChars {
		t.Fatalf("failure line is %d bytes: %q", len(failure), failure)
	}
	if strings.TrimSpace(delivered) != hisEvent {
		t.Fatalf("printed %q, want his message after the failure", delivered)
	}
}
