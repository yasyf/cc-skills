package asklane

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	testDrive = "drive-1"
	testAsker = "owner-relay"
)

var testNow = time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)

type fakeBus struct{ posts []client.Draft }

func (b *fakeBus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	b.posts = append(b.posts, d)
	seq := int64(len(b.posts) * 10)
	return client.Record{Seq: seq, Num: fmt.Sprintf("#%d", seq), Lane: d.Lane, Text: d.Text}, nil
}

func (b *fakeBus) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	if len(q.Kinds) > 0 {
		return []client.Record{{Seq: 1, Lane: "desk-runner", Kind: "state", Topic: "orca", At: testNow, Text: "orca alive pass 3"}}, nil
	}
	return nil, nil
}

func harness(t *testing.T) (string, *fakeBus, orca.Poster) {
	t.Helper()
	config := t.TempDir()
	state := filepath.Join(t.TempDir(), testDrive)
	if err := os.MkdirAll(state, 0o750); err != nil {
		t.Fatal(err)
	}
	entry, err := json.Marshal(map[string]string{"drive": testDrive, "cci_drive": testDrive, "state_dir": state, "checkout": state})
	if err != nil {
		t.Fatal(err)
	}
	drives := filepath.Join(config, "long-running", "drives")
	if err := os.MkdirAll(drives, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(drives, testDrive+".json"), entry, 0o600); err != nil {
		t.Fatal(err)
	}
	env := map[string]string{"CLAUDE_CONFIG_DIR": config, "HOME": config}
	bus := &fakeBus{}
	send := orca.Poster{
		Bus: bus, Runner: &shelltest.Fake{}, Env: func(name string) string { return env[name] },
		Now: func() time.Time { return testNow }, Sleep: func(time.Duration) { t.Fatal("the asker waited on the lane") }, Out: io.Discard, Drive: testDrive, Author: testAsker,
	}
	return state, bus, send
}

func ask(state, question string, mac bool) Ask {
	return Ask{Drive: testDrive, State: state, Asker: testAsker, Question: question, Mac: mac}
}

func TestStartLaunchesSonnetLowAndReturnsBeforeTheAnswer(t *testing.T) {
	state, bus, send := harness(t)
	var out strings.Builder
	if err := ask(state, "which PR moved the dashboard URL?", false).Start(context.Background(), send, &out); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(state, "ask-lane", "brief.md")
	want := "launch ask-lane sonnet low brief=" + path
	if len(bus.posts) != 1 || !strings.HasSuffix(bus.posts[0].Text, want) || bus.posts[0].Lane != testAsker {
		t.Fatalf("posts = %+v, want one launch from %s ending %q", bus.posts, testAsker, want)
	}
	if !strings.Contains(out.String(), "answer record to "+testAsker) {
		t.Fatalf("out = %q", out.String())
	}
	text, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range []string{"Register slice: lane=ask-lane step=all", "Scale: ", "which PR moved the dashboard URL?", "--kind answer --to " + testAsker, "lr ask-lane --mac"} {
		if !strings.Contains(string(text), line) {
			t.Errorf("brief lacks %q:\n%s", line, text)
		}
	}
	if strings.Contains(string(text), "place=local") {
		t.Errorf("a Sprite brief is pinned to the Mac:\n%s", text)
	}
}

func TestStartPinsAMacQuestionToTheMac(t *testing.T) {
	state, _, send := harness(t)
	if err := ask(state, "what did the owner write in Slack about stems?", true).Start(context.Background(), send, io.Discard); err != nil {
		t.Fatal(err)
	}
	text, err := os.ReadFile(filepath.Join(state, "ask-lane", "brief.md"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(text), "ccx: role=reader incident=none place=local why=mac-state mcp=slack") {
		t.Fatalf("brief is not placed on the Mac:\n%s", text)
	}
}

func TestStartNumbersEachAskUnderTheStem(t *testing.T) {
	state, bus, send := harness(t)
	if err := os.Mkdir(filepath.Join(state, "ask-lane-3"), 0o750); err != nil {
		t.Fatal(err)
	}
	lanes := make([]string, 0, 3)
	for range 3 {
		var out strings.Builder
		if err := ask(state, "where is the dashboard URL?", false).Start(context.Background(), send, &out); err != nil {
			t.Fatal(err)
		}
		lane, _, _ := strings.Cut(out.String(), ":")
		lanes = append(lanes, lane)
	}
	if got, want := strings.Join(lanes, ","), "ask-lane,ask-lane-2,ask-lane-4"; got != want {
		t.Fatalf("lanes = %s, want %s", got, want)
	}
	if len(bus.posts) != 3 {
		t.Fatalf("posts = %d, want 3", len(bus.posts))
	}
}

func TestStartRefusesABlankQuestion(t *testing.T) {
	state, bus, send := harness(t)
	err := ask(state, "  ", false).Start(context.Background(), send, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "blank") {
		t.Fatalf("err = %v, want a blank-question refusal", err)
	}
	if len(bus.posts) != 0 {
		t.Fatalf("posted %d records for a blank question", len(bus.posts))
	}
}
