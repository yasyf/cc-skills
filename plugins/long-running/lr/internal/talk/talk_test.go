package talk

import (
	"context"
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talkboard"
)

const started = "session: sub1\nurl: http://127.0.0.1:61118/p/talk--abcd\ntailnet: https://mac.ts.net:61118/p/talk--abcd\ntailnet: https://other.ts.net:61118/p/talk--abcd\nchannel: inactive\n"

func follower(t *testing.T, runner shell.Runner) *Follower {
	t.Helper()
	return &Follower{
		Runner:  runner,
		Entry:   drive.Entry{Drive: "d", CCIDrive: "dar", StateDir: t.TempDir()},
		Writers: []string{"yphone"},
		Now:     func() time.Time { return time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC) },
		Log:     slog.Default(),
	}
}

func noted(id, block, text string) []byte {
	line, err := json.Marshal(map[string]string{"type": "feedback.created", "id": id, "blockId": block, "text": text})
	if err != nil {
		panic(err)
	}
	return line
}

func posts(fake *shelltest.Fake) []shell.Command {
	return slices.DeleteFunc(fake.Calls(), func(c shell.Command) bool { return c.Argv[0] != "cci" })
}

func TestANoteBecomesAnOpenRowAndARecordThatWakesTheCommunicator(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("#1", "cci", "post")
	f := follower(t, fake)
	if err := f.Handle(context.Background(), noted("n1", Section, "how is the gateway?")); err != nil {
		t.Fatal(err)
	}
	rows, err := owner.Rows(owner.Ledger(f.Entry.StateDir), false)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || !strings.Contains(rows[0], "how is the gateway?") || !strings.HasSuffix(rows[0], "| open |") {
		t.Fatalf("rows = %q, want one open row holding the note", rows)
	}
	sent := posts(fake)
	if len(sent) != 1 {
		t.Fatalf("records = %+v, want one", sent)
	}
	for _, want := range []string{"--kind owner", "--to owner-relay", "--drive dar", "--id talk:n1"} {
		if !strings.Contains(strings.Join(sent[0].Argv, " "), want) {
			t.Errorf("record argv %q lacks %q", sent[0].Argv, want)
		}
	}
	if !strings.Contains(sent[0].Stdin, "how is the gateway?") {
		t.Errorf("record body = %q, want the note", sent[0].Stdin)
	}
	if got := talkboard.LastNote(f.Entry); got != "n1" {
		t.Errorf("LastNote = %q, want n1", got)
	}
}

func TestANoteWithTheCommunicatorDownStillLeavesAnOpenRow(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "cci unavailable", "cci", "post")
	f := follower(t, fake)
	if err := f.Handle(context.Background(), noted("n1", Section, "still there?")); err == nil {
		t.Fatal("Handle returned nil, want the failed post named")
	}
	rows, err := owner.Rows(owner.Ledger(f.Entry.StateDir), false)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || !strings.Contains(rows[0], "still there?") {
		t.Fatalf("rows = %q, want the note waiting on disk", rows)
	}
}

func TestARedeliveredNoteAddsNoSecondRowAcrossARestart(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("#1", "cci", "post")
	f := follower(t, fake)
	for range 2 {
		if err := f.Handle(context.Background(), noted("n1", Section, "once")); err != nil {
			t.Fatal(err)
		}
	}
	restarted := &Follower{Runner: fake, Entry: f.Entry, Now: f.Now, Log: f.Log}
	if err := restarted.Handle(context.Background(), noted("n1", Section, "once")); err != nil {
		t.Fatal(err)
	}
	rows, err := owner.Rows(owner.Ledger(f.Entry.StateDir), false)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || len(posts(fake)) != 1 {
		t.Fatalf("rows = %q, records = %d, want one of each", rows, len(posts(fake)))
	}
}

func TestOtherEventsAndOtherBlocksAddNothing(t *testing.T) {
	fake := &shelltest.Fake{}
	f := follower(t, fake)
	for _, line := range [][]byte{noted("n1", "reply-r1", "on a card"), []byte(`{"type":"choice.selected","blockId":"talk"}`)} {
		if err := f.Handle(context.Background(), line); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := os.Stat(owner.Ledger(f.Entry.StateDir)); !os.IsNotExist(err) {
		t.Fatalf("ledger stat err = %v, want no ledger", err)
	}
	if len(fake.Calls()) != 0 {
		t.Fatalf("calls = %+v, want none", fake.Calls())
	}
}

func TestOpenKeepsTheBoardAndWritesItsLinks(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(started, "cc-present", "start")
	fake.Reply("updated talk\n", "cc-present", "update-block")
	f := follower(t, fake)
	if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "dashboard.json"), []byte(`{"host":"100.64.0.1","port":61210}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := f.Open(context.Background()); err != nil {
		t.Fatal(err)
	}
	calls := fake.Calls()
	if len(calls) != 2 || slices.Contains(calls[0].Argv, "--doc") {
		t.Fatalf("calls = %+v, want start with no document, which would drop every reply, then one section write", calls)
	}
	var section struct {
		ID      string   `json:"id"`
		Writers []string `json:"writers"`
		Href    string   `json:"href"`
	}
	if err := json.Unmarshal([]byte(calls[1].Stdin), &section); err != nil {
		t.Fatal(err)
	}
	if section.ID != Section || !slices.Equal(section.Writers, []string{"yphone"}) || section.Href != "http://100.64.0.1:61210/" {
		t.Fatalf("section = %+v, want the talk section naming its writers and its dashboard", section)
	}
	data, err := os.ReadFile(filepath.Join(f.Entry.StateDir, Links))
	if err != nil {
		t.Fatal(err)
	}
	var links struct {
		URL     string `json:"url"`
		Tailnet string `json:"tailnet"`
		Thread  string `json:"thread"`
	}
	if err := json.Unmarshal(data, &links); err != nil {
		t.Fatal(err)
	}
	if links.URL != "http://127.0.0.1:61118/p/talk--abcd" || links.Tailnet != "https://mac.ts.net:61118/p/talk--abcd" || links.Thread != Section {
		t.Fatalf("links = %+v", links)
	}
}
