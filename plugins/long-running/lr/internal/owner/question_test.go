package owner

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const stdinWords = "why by hand?"

type asked struct {
	posts  []shell.Command
	state  string
	ledger string
	out    string
	err    error
}

func flag(c shell.Command, name string) string {
	at := slices.Index(c.Argv, name)
	if at < 0 {
		return ""
	}
	return c.Argv[at+1]
}

func attrsOf(c shell.Command) map[string]string {
	attrs := map[string]string{}
	for i, arg := range c.Argv {
		if arg == "--attr" {
			key, value, _ := strings.Cut(c.Argv[i+1], "=")
			attrs[key] = value
		}
	}
	return attrs
}

func runQuestion(t *testing.T, now time.Time, words string, args ...string) asked {
	t.Helper()
	config := t.TempDir()
	drives := filepath.Join(config, "long-running", "drives")
	if err := os.MkdirAll(drives, 0o750); err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(t.TempDir(), "brook")
	entry, err := json.Marshal(map[string]any{"drive": "d1", "cci_drive": "brook", "state_dir": state, "sessions": []string{"s1"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(drives, "d1.json"), entry, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	fake := &shelltest.Fake{}
	fake.Reply(`{"seq":77,"num":"#77"}`, "cci", "post")
	cmd := Command(&app.App{Now: func() time.Time { return now }, Runner: fake}, noLane)
	var out strings.Builder
	cmd.SetIn(strings.NewReader(words))
	cmd.SetOut(&out)
	cmd.SetErr(io.Discard)
	cmd.SetArgs(append(args, "--drive", "brook"))
	runErr := cmd.Execute()
	ledger, err := os.ReadFile(Ledger(state))
	if err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	return asked{fake.Calls(), state, string(ledger), out.String(), runErr}
}

func TestAReviewPageQuestionReachesBothAndCarriesItsDeadline(t *testing.T) {
	got := runQuestion(t, addedAt, stdinWords, "ask", "--pr", "49162", "--head", "e573d23")
	if got.err != nil {
		t.Fatal(got.err)
	}
	routing := got.posts[0]
	if flag(routing, "--kind") != "ask" || flag(routing, "--to") != "root,owner-relay" || flag(routing, "--quoted") != stdinWords || flag(routing, "--pr") != "49162" {
		t.Fatalf("routing record: %q", routing.Argv)
	}
	attrs := attrsOf(routing)
	if attrs["deadline"] != "2026-11-03T05:25:00Z" || attrs["owner"] != "root" || attrs["step:1"] != "9:25 PM answer his question" || attrs["head"] != "e573d23" {
		t.Fatalf("attrs: %v", attrs)
	}
	if strings.TrimSpace(got.out) != "77" {
		t.Fatalf("printed %q, want the routing record's seq", got.out)
	}
}

func TestAQuestionGetsTheSameLineOnThePageAndInTheChat(t *testing.T) {
	got := runQuestion(t, addedAt, stdinWords, "ask", "--pr", "49162", "--head", "e573d23")
	if got.err != nil {
		t.Fatal(got.err)
	}
	want := "Received, with the coordinator, answer by 9:25 PM"
	wake := got.posts[len(got.posts)-1]
	if flag(wake, "--kind") != "posted" || flag(wake, "--to") != communicator || flag(wake, "--text") != want || flag(wake, "--re") != "77" {
		t.Fatalf("window record: %q", wake.Argv)
	}
	if slices.Contains(batchedKinds, flag(wake, "--kind")) {
		t.Fatalf("kind %s waits up to five minutes in the communicator's batch", flag(wake, "--kind"))
	}
	if _, err := os.Stat(filepath.Join(got.state, "replies")); !os.IsNotExist(err) {
		t.Fatalf("a received line wrote a reply to his Chat tab: %v", err)
	}
}

func TestAnUnansweredPageQuestionGoesPastDueAndBlocksLikeAChatAsk(t *testing.T) {
	got := runQuestion(t, addedAt, stdinWords, "ask", "--pr", "49162", "--head", "e573d23")
	if got.err != nil {
		t.Fatal(got.err)
	}
	if want := "| m1 | Nov 2 9:10 PM | why by hand? | 77 | due Nov 2 9:25 PM |\n"; !strings.Contains(got.ledger, want) {
		t.Fatalf("no %q in:\n%s", want, got.ledger)
	}
	rows := Parse([]byte(got.ledger))
	if late := PastDue(rows, addedAt.Add(14*time.Minute)); len(late) != 0 {
		t.Fatalf("past due before its clock: %+v", late)
	}
	late := PastDue(rows, addedAt.Add(16*time.Minute))
	if len(late) != 1 || !strings.Contains(late[0].Line(), "[PAST DUE NOV 2 9:25 PM]") {
		t.Fatalf("past due rows: %+v", late)
	}
}

func TestADeadlinePastMidnightDatesItsRowTheNextDay(t *testing.T) {
	late := time.Date(2026, 11, 3, 23, 55, 0, 0, pacific)
	got := runQuestion(t, late, stdinWords, "received", "--routed", "77")
	if got.err != nil {
		t.Fatal(got.err)
	}
	if want := "| 77 | due Nov 4 12:10 AM |\n"; !strings.Contains(got.ledger, want) {
		t.Fatalf("no %q in:\n%s", want, got.ledger)
	}
}

func TestABoardQuestionIsHeldByTheChat(t *testing.T) {
	words := "Owner on board 1f2e3d4c, Should a share open? (share-open-pick): wrote “only for the team”"
	got := runQuestion(t, addedAt, words, "received", "--routed", "77")
	if got.err != nil {
		t.Fatal(got.err)
	}
	if want := "| 77 | due Nov 2 9:25 PM |\n"; !strings.Contains(got.ledger, want) {
		t.Fatalf("no %q in:\n%s", want, got.ledger)
	}
	wake := got.posts[len(got.posts)-1]
	if flag(wake, "--text") != "Received, with your chat, answer by 9:25 PM" || flag(wake, "--to") != communicator {
		t.Fatalf("window record: %q", wake.Argv)
	}
}

func TestAnAcknowledgementNamesNoUnknownHolderAndWritesNoRow(t *testing.T) {
	got := runQuestion(t, addedAt, stdinWords, "received", "--routed", "77", "--holder", "gateway-parity-3")
	if got.err == nil || got.ledger != "" || len(got.posts) != 0 {
		t.Fatalf("err %v, ledger %q, posts %d", got.err, got.ledger, len(got.posts))
	}
}
