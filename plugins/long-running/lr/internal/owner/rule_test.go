package owner

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const ruledReply = "No call names lr.json any more: lr reads the drive from the session."

func ruling(t *testing.T, fake *shelltest.Fake) (string, string, error) {
	t.Helper()
	config, state := t.TempDir(), t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	for path, body := range map[string]string{
		filepath.Join(config, "long-running", "drives", "brook.json"): `{"drive":"brook","cci_drive":"brook-bus","checkout":"/repo","state_dir":"` + state + `","sessions":["s1"]}`,
		filepath.Join(state, "owner-relay", "owner-ledger.md"):        header + "| m1 | Nov 3 10:09 PM | stop passing LR_CONFIG on every call |  | open |\n",
		filepath.Join(state, "replies", "r1.json"):                    `{"reply":{"md":"` + ruledReply + `"}}`,
	} {
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	var out bytes.Buffer
	cmd := Command(&app.App{Runner: fake}, noLane)
	cmd.SetOut(&out)
	cmd.SetArgs([]string{"rule", "m1", "--reply", "r1", "--drive", "brook"})
	err := cmd.Execute()
	return state, out.String(), err
}

func answers(t *testing.T, found ...answer) string {
	t.Helper()
	data, err := json.Marshal(found)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func TestARulingLabelsTheHooksAnswerKeepsTheReplyAndMarksTheRow(t *testing.T) {
	fake := &shelltest.Fake{}
	title := "What did the owner say at Nov 3 10:09 PM?"
	fake.Reply(answers(t,
		answer{ID: "other-drive", Title: title, Body: "merge the billing stack", Tags: []string{ownerAnswer}},
		answer{ID: "hooks", Title: title, Body: "stop passing  LR_CONFIG\non every call\n\nOwner, 2026-11-03 10:09 PM PT", Tags: []string{ownerAnswer, "scope:durable"}},
	), "ccn", "-R", "/repo", "answer", "list")
	fake.Reply("", "ccn", "-R", "/repo", "answer", "edit")
	state, out, err := ruling(t, fake)
	if err != nil {
		t.Fatal(err)
	}
	if out != "m1 ruled: answer hooks, "+filepath.Join(state, "owner-relay", "answers", "m1.md")+", responded\n" {
		t.Fatalf("out = %q", out)
	}
	edits := slices.DeleteFunc(fake.Calls(), func(c shell.Command) bool { return c.Argv[4] != "edit" })
	if len(edits) != 1 || !slices.Equal(edits[0].Argv[4:], []string{"edit", "hooks", "--add-label", "drive:brook-bus", "--add-label", "owner:m1"}) {
		t.Fatalf("edits = %v", edits)
	}
	if got := read(t, filepath.Join(state, "owner-relay", "answers", "m1.md")); got != ruledReply {
		t.Fatalf("answer file = %q", got)
	}
	if got := read(t, filepath.Join(state, "owner-relay", "owner-ledger.md")); !strings.Contains(got, "| m1 | Nov 3 10:09 PM | stop passing LR_CONFIG on every call |  | responded |\n") {
		t.Fatalf("ledger:\n%s", got)
	}
}

func TestARulingWithNoAnswerAddsOneWithTheRowsWords(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("[]", "ccn", "-R", "/repo", "answer", "list")
	fake.Reply(`{"id":"made"}`, "ccn", "-R", "/repo", "answer", "add")
	_, out, err := ruling(t, fake)
	if err != nil || !strings.HasPrefix(out, "m1 ruled: answer made, ") {
		t.Fatalf("out = %q, %v", out, err)
	}
	add := fake.Calls()[1].Argv[4:]
	want := []string{"add", "--json", "--label", ownerAnswer, "--label", "drive:brook-bus", "--label", "owner:m1", "--label", "scope:durable", "--body", "stop passing LR_CONFIG on every call", "--", "What did the owner say at Nov 3 10:09 PM?"}
	if !slices.Equal(add, want) {
		t.Fatalf("add = %q", add)
	}
}
