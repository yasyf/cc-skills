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
	runner.Reply("shape-box\nother-box\n", boxesRead...)
	runner.Reply("user.name=lane\nlong-running.signedinproof=lane-a=release-50287\nlong-running.signedinproof=lane-b=release-50300\n", configRead...)
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
		{"ci mac-box ssh shape-box -- open -a Iris", true},
		{`AWS_PROFILE="$DEVBOX_AWS_PROFILE" tools/ci mac-box ssh other-box -- 'open -a "Iris"'`, true},
		{"ci mac-box ssh shape-box -- open /Applications/Iris.app", true},
		{"ci mac-box ssh shape-box -- '/Applications/Iris.app/Contents/MacOS/Iris &'", true},
		{"ci mac-box ssh shape-box -- ~/rel/sand iris exec --prompt hi", true},
		{"ci mac-box ssh shape-box <<'EOF'\nopen -a Iris\nEOF", true},
		{"ssh -i ~/.config/mac-box/shape-box/id_ed25519 ec2-user@10.0.0.4 'open -a Iris'", true},
		{"ci mac-box ssh shape-box -- IRIS_TEAM=personal IRIS_RELEASE_PROOF=release-50287 open -a Iris", false},
		{"ci mac-box ssh shape-box -- IRIS_TEAM=personal IRIS_RELEASE_PROOF=release-50287 ~/rel/sand iris exec --prompt hi", false},
		{"ci mac-box ssh shape-box -- IRIS_TEAM=acme IRIS_RELEASE_PROOF=release-50287 open -a Iris", true},
		{"ci mac-box ssh shape-box -- IRIS_RELEASE_PROOF=release-50287 open -a Iris", true},
		{"ci mac-box ssh shape-box -- IRIS_TEAM=personal open -a Iris", true},
		{"ci mac-box ssh shape-box -- IRIS_TEAM=personal IRIS_RELEASE_PROOF=release-50300 open -a Iris", true},
		{"ci mac-box ssh shape-box -- 'open -a Iris; IRIS_TEAM=personal IRIS_RELEASE_PROOF=release-50287 open -a Iris'", true},
		{"ci mac-box ssh shape-box -- IRIS_PLANE=staging sand iris exec --prompt hi", false},
		{"ci mac-box ssh fresh-box -- open -a Iris", false},
		{"ci mac-box ssh shape-box-2 -- open -a Iris", false},
		{"ci mac-box ssh shape-box -- ls ~/rel ~/.iris", false},
		{"ci mac-box ssh shape-box -- pkill -x Iris", false},
		{"ci mac-box ssh shape-box -- ls /Applications/Iris.app/Contents/MacOS/", false},
		{"ci mac-box ssh shape-box -- 'echo open -a Iris'", false},
		{"ci mac-box list", false},
		{"open -a Iris", false},
	}
	for _, c := range cases {
		t.Run(c.command, func(t *testing.T) {
			got := Guard{Runner: fake(), Getenv: lane}.Refusal(context.Background(), payload(t, c.command)) != ""
			if got != c.refused {
				t.Fatalf("Refusal(%q) refused = %v, want %v", c.command, got, c.refused)
			}
		})
	}
}

func TestAProofGrantBelongsToItsLane(t *testing.T) {
	otherLane := func(key string) string {
		if key == "CLAUDE_LONG_RUNNING_LANE" {
			return "lane-c"
		}
		return ""
	}
	command := "ci mac-box ssh shape-box -- IRIS_TEAM=personal IRIS_RELEASE_PROOF=release-50287 open -a Iris"
	if (Guard{Runner: fake(), Getenv: otherLane}).Refusal(context.Background(), payload(t, command)) == "" {
		t.Fatal("allowed a lane root did not grant")
	}
	ungranted := &shelltest.Fake{}
	ungranted.Reply("shape-box\n", boxesRead...)
	ungranted.Reply("user.name=lane\n", configRead...)
	if (Guard{Runner: ungranted, Getenv: lane}).Refusal(context.Background(), payload(t, command)) == "" {
		t.Fatal("allowed a proof nobody granted")
	}
}

func TestAGrantNamesTheTopLevelLaneNeverItsSubagents(t *testing.T) {
	proof := "ci mac-box ssh shape-box -- IRIS_TEAM=personal IRIS_RELEASE_PROOF=release-50287 open -a Iris"
	nameless := &shelltest.Fake{}
	nameless.Reply("shape-box\n", boxesRead...)
	nameless.Reply("long-running.signedinproof==release-50287\n", configRead...)
	cases := []struct {
		name    string
		agentID string
		command string
		runner  *shelltest.Fake
		refused bool
	}{
		{"the granted lane's proof", "", proof, fake(), false},
		{"a subagent of the granted lane", "a1", proof, fake(), true},
		{"a subagent against a grant naming no lane", "a1", proof, nameless, true},
		{"the granted lane's credential write", "", keyWrite, granting("lane-a=ssm:/ci/desk-host/cci-service-key\n"), false},
		{"a subagent's credential write", "a1", keyWrite, granting("lane-a=ssm:/ci/desk-host/cci-service-key\n"), true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			raw, err := json.Marshal(map[string]any{"cwd": "/w", "agent_id": c.agentID, "tool_input": map[string]string{"command": c.command}})
			if err != nil {
				t.Fatal(err)
			}
			if got := (Guard{Runner: c.runner, Getenv: lane}).Refusal(context.Background(), raw) != ""; got != c.refused {
				t.Fatalf("refused = %v, want %v", got, c.refused)
			}
		})
	}
}

func TestAGrantStillRefusesHisBrowser(t *testing.T) {
	said := Guard{Runner: fake(), Getenv: lane}.Refusal(context.Background(), payload(t, "orca tab create --url https://app.example.test/ --json"))
	if said != refusal {
		t.Fatalf("Refusal = %q, want the browser refusal", said)
	}
}

func TestPassesOutsideALane(t *testing.T) {
	none := func(string) string { return "" }
	if (Guard{Runner: fake(), Getenv: none}).Refusal(context.Background(), payload(t, "orca tab create --url https://app.example.test/")) != "" {
		t.Fatal("refused a session that is no lane")
	}
}

func TestFailedReadsPass(t *testing.T) {
	create := "orca tab create --url https://app.example.test/"
	eval := "orca eval --page p1 --expression 'document.title'"
	noHosts := &shelltest.Fake{}
	noHosts.Fail(1, "unset", "git", "config")
	if (Guard{Runner: noHosts, Getenv: lane}).Refusal(context.Background(), payload(t, create)) != "" {
		t.Fatal("refused with no hosts configured")
	}
	noTabs := &shelltest.Fake{}
	noTabs.Reply("app.example.test\n", "git", "config")
	noTabs.Fail(1, "orca is down", "orca", "tab")
	if (Guard{Runner: noTabs, Getenv: lane}).Refusal(context.Background(), payload(t, eval)) != "" {
		t.Fatal("refused when the tab read failed")
	}
	if (Guard{Runner: fake(), Getenv: lane}).Refusal(context.Background(), []byte("not json")) != "" {
		t.Fatal("refused an unreadable call")
	}
}

func TestReadsOnlyForAPageVerb(t *testing.T) {
	runner := fake()
	Guard{Runner: runner, Getenv: lane}.Refusal(context.Background(), payload(t, "git status && orca tab list --json"))
	if calls := runner.Calls(); len(calls) != 0 {
		t.Fatalf("read on a call that drives no page: %v", calls)
	}
}

func TestATestMacRefusalNamesTheFreshBox(t *testing.T) {
	said := Guard{Runner: fake(), Getenv: lane}.Refusal(context.Background(), payload(t, "ci mac-box ssh shape-box -- open -a Iris"))
	if said != irisRefusal {
		t.Fatalf("Refusal = %q, want the test Mac refusal", said)
	}
}

func TestAFailedBoxReadPasses(t *testing.T) {
	noBoxes := &shelltest.Fake{}
	noBoxes.Fail(1, "unset", "git", "config")
	if (Guard{Runner: noBoxes, Getenv: lane}).Refusal(context.Background(), payload(t, "ci mac-box ssh shape-box -- open -a Iris")) != "" {
		t.Fatal("refused with no boxes configured")
	}
}

func TestReadsNoBoxesForACallThatLaunchesNoIris(t *testing.T) {
	runner := fake()
	Guard{Runner: runner, Getenv: lane}.Refusal(context.Background(), payload(t, "ci mac-box ssh shape-box -- ls ~/rel"))
	if calls := runner.Calls(); len(calls) != 0 {
		t.Fatalf("read on a call that launches no Iris: %v", calls)
	}
}
