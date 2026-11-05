package codexguard

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	tabs    = `{"result":{"tabs":[{"browserPageId":"p1","url":"https://app.example.test/"},{"browserPageId":"p2","url":"http://localhost:3000/"}]}}`
	current = `{"result":{"tab":{"browserPageId":"p1","url":"https://app.example.test/"}}}`
)

func lane(key string) string {
	if key == "CLAUDE_LONG_RUNNING_LANE" {
		return "lane-a"
	}
	return ""
}

func payload(t *testing.T, command string) []byte {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"cwd": "/w", "tool_input": map[string]string{"command": command}})
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func fake() *shelltest.Fake {
	runner := &shelltest.Fake{}
	runner.Reply("app.example.test\n", "git", "config")
	runner.Reply(tabs, "orca", "tab", "list")
	runner.Reply(current, "orca", "tab", "current")
	return runner
}

func TestRefusals(t *testing.T) {
	cases := []struct {
		command string
		refused bool
	}{
		{"orca tab create --url https://app.example.test/ --json", true},
		{"orca goto --url https://app.example.test/settings", true},
		{`orca eval --page p1 --expression '(async()=>{const r=await fetch("/api/graphql/Viewer",{method:"POST"});return r.json()})()' --json`, true},
		{"orca eval --page=p1 --expression 'document.title'", true},
		{"orca exec --page p1 --command 'click e8' --json", true},
		{"orca snapshot --page p1 --interactive --json", true},
		{"orca eval --expression 'document.title'", true},
		{`orca eval --page p2 --expression 'fetch("https://app.example.test/api/graphql/Viewer")'`, true},
		{"orca exec --page p2 --command 'open https://app.example.test/'", true},
		{"cd /w && /opt/bin/orca eval --page p1 --expression 'document.title' | jq .", true},
		{"env FOO=bar orca tab create --url https://app.example.test/", true},
		{`zsh -lc "orca tab create --url https://app.example.test/"`, true},
		{"x=$(orca eval --page p1 --expression 'document.title')", true},
		{"orca tab create --url http://localhost:3000/ --json", false},
		{"orca tab create --url https://b-1a2b.app.example.test/ --json", false},
		{"orca goto --url https://app.example.test.preview.dev/", false},
		{"orca eval --page p2 --expression 'document.title'", false},
		{"orca tab list --json", false},
		{"orca tab close --page p1 --json", false},
		{"orca terminal list --json", false},
		{"agent-browser open https://app.example.test/", false},
		{"ci mac-box ssh box-1 -- iris status", false},
		{"echo 'orca tab create --url https://app.example.test/'", false},
		{"if (((", false},
	}
	for _, c := range cases {
		t.Run(c.command, func(t *testing.T) {
			got := Guard{Runner: fake(), Getenv: lane}.Refuses(context.Background(), payload(t, c.command))
			if got != c.refused {
				t.Fatalf("Refuses(%q) = %v, want %v", c.command, got, c.refused)
			}
		})
	}
}

func TestPassesOutsideALane(t *testing.T) {
	none := func(string) string { return "" }
	if (Guard{Runner: fake(), Getenv: none}).Refuses(context.Background(), payload(t, "orca tab create --url https://app.example.test/")) {
		t.Fatal("refused a session that is no lane")
	}
}

func TestFailedReadsPass(t *testing.T) {
	create := "orca tab create --url https://app.example.test/"
	eval := "orca eval --page p1 --expression 'document.title'"
	noHosts := &shelltest.Fake{}
	noHosts.Fail(1, "unset", "git", "config")
	if (Guard{Runner: noHosts, Getenv: lane}).Refuses(context.Background(), payload(t, create)) {
		t.Fatal("refused with no hosts configured")
	}
	noTabs := &shelltest.Fake{}
	noTabs.Reply("app.example.test\n", "git", "config")
	noTabs.Fail(1, "orca is down", "orca", "tab")
	if (Guard{Runner: noTabs, Getenv: lane}).Refuses(context.Background(), payload(t, eval)) {
		t.Fatal("refused when the tab read failed")
	}
	if (Guard{Runner: fake(), Getenv: lane}).Refuses(context.Background(), []byte("not json")) {
		t.Fatal("refused an unreadable call")
	}
}

func TestReadsOnlyForAPageVerb(t *testing.T) {
	runner := fake()
	Guard{Runner: runner, Getenv: lane}.Refuses(context.Background(), payload(t, "git status && orca tab list --json"))
	if calls := runner.Calls(); len(calls) != 0 {
		t.Fatalf("read on a call that drives no page: %v", calls)
	}
}
