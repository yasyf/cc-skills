package reply

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	threadedLink = "https://brook.slack.com/archives/D0B6Z/p1793831084582629?thread_ts=" + hisThread + "&channel=D0B6Z&message_ts=1793831084.582629"
	threadedRow  = "| m740 | Nov 4 2:29 PM | " + threadedLink + " |  | open |"
	terminalRow  = "| m741 | Nov 4 2:31 PM | why is the build red |  | open |"
	boardRow     = "| m742 | Nov 4 2:34 PM | Owner on board d50e80e8, Land it? (card-x): picked “Land” |  | open |"
	wordsRow     = "| m743 | Nov 4 2:35 PM | these two: " + threadedLink + " and https://brook.slack.com/archives/D0B6Z/p1?thread_ts=1793000000.000001 |  | open |"
	lostRow      = "| m744 | Nov 4 2:36 PM | Slack 2:36 PM: \"is this live\" |  | open |"
)

func ledgerHolds(t *testing.T, entry drive.Entry, rows ...string) {
	t.Helper()
	path := owner.Ledger(entry.StateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(strings.Join(rows, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
}

func throughTheTool(t *testing.T, entry drive.Entry, fake *shelltest.Fake, fields map[string]any) (string, bool) {
	t.Helper()
	config := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	t.Setenv(drive.Env, "d")
	encoded, err := json.Marshal(entry)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(config, "long-running", "drives"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "long-running", "drives", "d.json"), encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	fields["title"], fields["md"] = "The answer", "The body."
	args, err := json.Marshal(fields)
	if err != nil {
		t.Fatal(err)
	}
	return Tool(&app.App{Runner: fake, Now: func() time.Time { return sentAt }}).Handler(context.Background(), args, func(string) {})
}

func TestAReplyPostsWhereItsRowCameFrom(t *testing.T) {
	const atRoot = ""
	for name, tc := range map[string]struct {
		fields map[string]any
		thread string
	}{
		"a pasted link to a thread message":    {map[string]any{"order": "m740", "status": info}, hisThread},
		"his row named by its line":            {map[string]any{"on": "line:m740", "status": info}, hisThread},
		"a terminal row":                       {map[string]any{"order": "m741", "status": info}, atRoot},
		"a progress reply on a terminal row":   {map[string]any{"order": "m741", "status": progress}, atRoot},
		"a board row":                          {map[string]any{"order": "m742", "status": info}, atRoot},
		"a chosen thread beats the row":        {map[string]any{"order": "m741", "status": info, "thread": "1793000000.000002"}, "1793000000.000002"},
		"a row with no place told to the root": {map[string]any{"order": "m744", "status": info, "thread": owner.AtRoot}, atRoot},
		"a row with no place told its thread":  {map[string]any{"order": "m744", "status": info, "thread": hisThread}, hisThread},
	} {
		t.Run(name, func(t *testing.T) {
			f, fake := fanout(t, nil), scripted()
			postedInThread(fake, threadCopy)
			ledgerHolds(t, f.Entry, threadedRow, terminalRow, boardRow, wordsRow, lostRow)
			if text, failed := throughTheTool(t, f.Entry, fake, tc.fields); failed {
				t.Fatalf("reply refused: %s", text)
			}
			posted := calls(fake, "cc-slack")
			if len(posted) != 1 {
				t.Fatalf("cc-slack calls = %+v, want one post", posted)
			}
			at := slices.Index(posted[0].Argv, "--thread")
			switch {
			case tc.thread == atRoot && at >= 0:
				t.Fatalf("posted under a thread, want his DM root: %v", posted[0].Argv)
			case tc.thread != atRoot && flag(t, posted[0].Argv, "--thread") != tc.thread:
				t.Fatalf("posted in %s, want %s: %v", flag(t, posted[0].Argv, "--thread"), tc.thread, posted[0].Argv)
			}
		})
	}
}

func TestARowFromHisThreadWordsPostsInThatThreadInAnotherSession(t *testing.T) {
	f, fake := fanout(t, nil), scripted()
	postedInThread(fake, threadCopy)
	ledgerHolds(t, f.Entry, "| m766.1 | Nov 4 5:58 PM | why are the windows different |  | open |", "| m766.2 | Nov 4 5:58 PM | go retro why it took so long |  | open |")
	wordsFile := filepath.Join(filepath.Dir(owner.Ledger(f.Entry.StateDir)), "word-rows.jsonl")
	if err := os.WriteFile(wordsFile, []byte(`{"ts":"1793843917.843979","thread":"`+hisThread+`","rows":["m766.1","m766.2"]}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if text, failed := throughTheTool(t, f.Entry, fake, map[string]any{"order": "m766.1", "status": info}); failed {
		t.Fatalf("reply refused: %s", text)
	}
	if posted := calls(fake, "cc-slack"); len(posted) != 1 || flag(t, posted[0].Argv, "--thread") != hisThread {
		t.Fatalf("cc-slack calls = %+v, want one post in %s", posted, hisThread)
	}
}

func TestAReplyNamingOnlyHisThreadAnswersItsLatestRow(t *testing.T) {
	f, fake := fanout(t, nil), scripted()
	postedInThread(fake, threadCopy)
	ledgerHolds(t, f.Entry, "| m766 | Nov 4 5:58 PM | why are the windows different |  | open |")
	wordsFile := filepath.Join(filepath.Dir(owner.Ledger(f.Entry.StateDir)), "word-rows.jsonl")
	if err := os.WriteFile(wordsFile, []byte(`{"ts":"1793843917.843979","thread":"`+hisThread+`","rows":["m766"]}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	text, failed := throughTheTool(t, f.Entry, fake, map[string]any{"thread": hisThread, "status": info})
	if failed || !strings.Contains(text, "ledger: m766 responded") {
		t.Fatalf("reply = %q, failed %v; want it to answer m766", text, failed)
	}
}

func TestAReplyWhoseRowHasNoPlaceRefusesAndPostsNothing(t *testing.T) {
	for name, order := range map[string]string{
		"a hand row saying Slack with no thread": "m744",
		"words naming two threads":               "m743",
		"no such row":                            "m999",
	} {
		t.Run(name, func(t *testing.T) {
			f, fake := fanout(t, nil), scripted()
			ledgerHolds(t, f.Entry, threadedRow, terminalRow, boardRow, wordsRow, lostRow)
			text, failed := throughTheTool(t, f.Entry, fake, map[string]any{"order": order, "status": info})
			if !failed || !strings.Contains(text, order+" has no recorded place") || !strings.Contains(text, `thread "root"`) {
				t.Fatalf("reply = %q, failed %v; want a refusal saying what to pass", text, failed)
			}
			if len(fake.Calls()) != 0 {
				t.Fatalf("calls = %+v, want none", fake.Calls())
			}
		})
	}
}

func TestARowWithNoPlaceRefusesNothingThatPostsNothingNew(t *testing.T) {
	t.Run("a progress reply names a thread before it posts", func(t *testing.T) {
		f, fake := fanout(t, nil), scripted()
		ledgerHolds(t, f.Entry, lostRow)
		text, failed := throughTheTool(t, f.Entry, fake, map[string]any{"order": "m744", "status": progress})
		if !failed || len(calls(fake, "cc-slack")) != 0 {
			t.Fatalf("reply = %q, failed %v; slack = %+v; want a refusal that asks for a thread", text, failed, calls(fake, "cc-slack"))
		}
	})
	t.Run("an edit keeps the message it already posted", func(t *testing.T) {
		f, fake := fanout(t, nil), scripted()
		ledgerHolds(t, f.Entry, lostRow)
		f.Runner = fake
		f.Send(context.Background(), Reply{ID: "r1", Title: "t", MD: "first", Status: info})
		text, failed := throughTheTool(t, f.Entry, fake, map[string]any{"id": "r1", "order": "m744", "status": info})
		if failed {
			t.Fatalf("edit refused: %s", text)
		}
	})
}

func TestAnIncidentLineThreadsUnderItsAlertsFirstPost(t *testing.T) {
	f, fake := fanout(t, nil), scripted()
	ledgerHolds(t, f.Entry, terminalRow)
	spawn := map[string]any{"id": "spawn", "status": incident, "alert": "sentry-7830378865", "unprompted": true}
	if text, failed := throughTheTool(t, f.Entry, fake, spawn); failed {
		t.Fatalf("spawn refused: %s", text)
	}
	mechanism := map[string]any{"id": "mechanism", "status": incident, "alert": "sentry-7830378865-fix", "order": "m741"}
	if text, failed := throughTheTool(t, f.Entry, fake, mechanism); failed {
		t.Fatalf("mechanism refused: %s", text)
	}
	other := map[string]any{"id": "other", "status": incident, "alert": "dd-338724738", "unprompted": true}
	if text, failed := throughTheTool(t, f.Entry, fake, other); failed {
		t.Fatalf("another alert refused: %s", text)
	}
	posted := calls(fake, "cc-slack")
	if len(posted) != 3 {
		t.Fatalf("cc-slack calls = %+v, want three posts", posted)
	}
	if slices.Contains(posted[0].Argv, "--thread") || slices.Contains(posted[2].Argv, "--thread") {
		t.Fatalf("each alert's first post went under a thread, want his DM root: %v, %v", posted[0].Argv, posted[2].Argv)
	}
	if got := flag(t, posted[1].Argv, "--thread"); got != owner.MessageTS(permalink) {
		t.Fatalf("mechanism posted in %s, want the spawn post's thread %s", got, owner.MessageTS(permalink))
	}
	data, err := os.ReadFile(filepath.Join(f.Entry.StateDir, storeDir, "mechanism.json"))
	if err != nil {
		t.Fatal(err)
	}
	var saved delivery
	if err := json.Unmarshal(data, &saved); err != nil {
		t.Fatal(err)
	}
	if saved.Reply.On != "reply:spawn" {
		t.Fatalf("mechanism on = %q, want it under reply:spawn on Chat", saved.Reply.On)
	}
}

func TestAnIncidentLineWithNoAlertPostsAndSaysWhatToAdd(t *testing.T) {
	f, fake := fanout(t, nil), scripted()
	text, failed := throughTheTool(t, f.Entry, fake, map[string]any{"status": incident, "unprompted": true})
	if failed || !strings.Contains(text, "names no alert") || !strings.Contains(text, "pass alert") {
		t.Fatalf("reply = %q, failed %v; want it posted with a warning naming alert", text, failed)
	}
	if posted := calls(fake, "cc-slack"); len(posted) != 1 {
		t.Fatalf("cc-slack calls = %+v, want one post", posted)
	}
}
