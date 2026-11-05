package livetasks

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	handle = "term_a"
	agent  = "4100 claude --model opus HOME=/h ORCA_TERMINAL_HANDLE=term_a\n"
)

func terminal(t *testing.T, processes string, transcript ...string) Rest {
	t.Helper()
	claude := t.TempDir()
	if len(transcript) > 0 {
		path := filepath.Join(claude, "projects", "-h-repo", session+".jsonl")
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(strings.Join(transcript, "\n")+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	runner := &shelltest.Fake{}
	runner.Reply(processes, "ps")
	runner.Reply(`{"pid":4000,"session":"other"}`+"\n"+`{"pid":4100,"session":"`+session+`"}`+"\n", "cci", "who")
	rest, err := Terminal(context.Background(), runner, handle, claude, now)
	if err != nil {
		t.Fatal(err)
	}
	return rest
}

func TestTerminalListsTheLiveTasksOfItsClaudeAgentsTranscript(t *testing.T) {
	at := now.Add(-time.Minute)
	rest := terminal(t, agent,
		assistantCall(t, process, "m1", "Monitor", map[string]any{"command": "tail -f ci.log", "description": "ci log", "timeout_ms": 1800000}, at),
		startedResult(t, "m1", "aaa"),
	)
	if len(rest.Tasks) != 1 || !strings.HasPrefix(rest.Tasks[0], `- Monitor "ci log" (task aaa)`) {
		t.Fatal(rest)
	}
}

func TestTerminalCountsAnArmedWaitBearingItsHandleAndNoOtherTerminals(t *testing.T) {
	rest := terminal(t, agent+
		"4200 /h/.local/bin/lr wait 540 mailbox:x=/c --wake ORCA_TERMINAL_HANDLE=term_a LR_WAIT_ARMED=1\n"+
		"4300 /h/.local/bin/lr wait 540 mailbox:y=/c --wake ORCA_TERMINAL_HANDLE=term_ab LR_WAIT_ARMED=1\n")
	if len(rest.Tasks) != 1 || rest.Tasks[0] != "- `lr wait --wake` armed as pid 4200" {
		t.Fatal(rest)
	}
}

func TestTerminalWithNothingArmedNamesWhatItRead(t *testing.T) {
	cases := map[string]struct {
		processes  string
		transcript []string
		read       string
	}{
		"claude at rest": {agent, []string{assistantCall(t, process, "b1", "Bash", map[string]any{"command": "ls"}, now)}, "; claude pid 4100's transcript "},
		"no transcript":  {agent, nil, "; claude pid 4100, session " + session + ", has no transcript under "},
		"codex":          {"5100 codex exec ORCA_TERMINAL_HANDLE=term_a\n", nil, "; codex pid 5100 keeps no transcript lr reads, so only an armed lr wait counts"},
		"no agent":       {"5200 zsh ORCA_TERMINAL_HANDLE=term_b\n", nil, ", and no claude or codex process does"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			rest := terminal(t, c.processes, c.transcript...)
			if len(rest.Tasks) != 0 || !strings.HasPrefix(rest.Read, "0 armed lr wait processes bear terminal term_a"+c.read) {
				t.Fatal(rest)
			}
		})
	}
}
