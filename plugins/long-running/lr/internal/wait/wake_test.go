package wait

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
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

const laneTerminal = "term_lane"

func screenOf(t *testing.T, tail ...string) string {
	t.Helper()
	encoded, err := json.Marshal(map[string]any{"ok": true, "result": map[string]any{"terminal": map[string]any{"source": "screen", "tail": tail}}})
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded)
}

func lane(t *testing.T, tail ...string) *shelltest.Fake {
	t.Helper()
	fake := &shelltest.Fake{}
	fake.Reply(`{"ok":true,"result":{"messageId":"msg_1"}}`, "orca", "orchestration", "send")
	fake.Reply(screenOf(t, tail...), "orca", "terminal", "read")
	fake.Reply(`{"ok":true}`, "orca", "terminal", "send")
	return fake
}

func flagOf(argv []string, name string) string {
	at := slices.Index(argv, name)
	if at < 0 {
		return ""
	}
	return argv[at+1]
}

func runArmed(t *testing.T, fake *shelltest.Fake, output string, args ...string) error {
	t.Helper()
	t.Setenv(armedTag, "1")
	t.Setenv(terminalKey, laneTerminal)
	a := app.New(&bytes.Buffer{})
	a.Runner = fake
	cmd := Command(a)
	cmd.SetArgs(append([]string{"--wake", output}, args...))
	cmd.SilenceErrors, cmd.SilenceUsage = true, true
	return cmd.Execute()
}

func TestACompletedWaitReachesTheLaneThatArmedItAndNoDesk(t *testing.T) {
	output := filepath.Join(t.TempDir(), "wait.log")
	if err := os.WriteFile(output, []byte("released replay graded\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fake := lane(t, "  Worked for 5m 38s • 11:33 AM", "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh · ~/w")
	err := runArmed(t, fake, output, "5", "--", "sh", "-c", "exit 3")
	var exit *ExitError
	if !errors.As(err, &exit) || exit.Code != 3 {
		t.Fatalf("got %v, want the guarded command's exit 3", err)
	}
	var mail, typed []string
	for _, call := range fake.Calls() {
		if call.Argv[0] != "orca" {
			t.Fatalf("a completed wait ran %q; only the arming terminal hears of it", call.Argv)
		}
		switch strings.Join(call.Argv[:3], " ") {
		case "orca orchestration send":
			mail = call.Argv
		case "orca terminal send":
			typed = call.Argv
		}
	}
	if flagOf(mail, "--to") != laneTerminal || flagOf(mail, "--from") != laneTerminal || flagOf(mail, "--subject") != "lr wait exited 3" {
		t.Fatalf("mail %q", mail)
	}
	body := flagOf(mail, "--body")
	if !strings.Contains(body, output) || !strings.Contains(body, "released replay graded") {
		t.Fatalf("mail body %q names neither the output file nor what it holds", body)
	}
	if flagOf(typed, "--terminal") != laneTerminal || !strings.Contains(flagOf(typed, "--text"), "msg_1") {
		t.Fatalf("typed %q", typed)
	}
}

func TestAWakeKeepsOffALaneThatIsNotAtRest(t *testing.T) {
	cases := map[string][]string{
		"working":        {"• Working (12s • esc to interrupt)", "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh · ~/w"},
		"a dialog":       {"  Would you like to run the following command?", "› 1. Yes, proceed (y)", "  2. No (esc)", "  Press enter to confirm or esc to cancel"},
		"another screen": {"plain shell output"},
	}
	for name, tail := range cases {
		t.Run(name, func(t *testing.T) {
			output := filepath.Join(t.TempDir(), "wait.log")
			fake := lane(t, tail...)
			if err := Wake(t.Context(), fake, laneTerminal, output, 0); err != nil {
				t.Fatal(err)
			}
			sent, typed := false, false
			for _, call := range fake.Calls() {
				sent = sent || strings.Join(call.Argv[:3], " ") == "orca orchestration send"
				typed = typed || strings.Join(call.Argv[:3], " ") == "orca terminal send"
			}
			if !sent || typed {
				t.Fatalf("mailed %v and typed %v, want mail with nothing typed", sent, typed)
			}
		})
	}
}

func TestAWaitAtItsDeadlineStillWakesTheLane(t *testing.T) {
	output := filepath.Join(t.TempDir(), "wait.log")
	fake := lane(t, "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh · ~/w")
	if err := runArmed(t, fake, output, "0", "--", "sleep", "30"); err != nil {
		t.Fatal(err)
	}
	if !slices.ContainsFunc(fake.Calls(), func(call shell.Command) bool { return strings.Join(call.Argv[:3], " ") == "orca terminal send" }) {
		t.Fatalf("no wake in %v", fake.Calls())
	}
}

func TestWakeNeedsTheTerminalThatArmedIt(t *testing.T) {
	t.Setenv(terminalKey, "")
	a := app.New(&bytes.Buffer{})
	a.Runner = &shelltest.Fake{}
	cmd := Command(a)
	cmd.SetArgs([]string{"--wake", filepath.Join(t.TempDir(), "wait.log"), "5", "--", "true"})
	cmd.SilenceErrors, cmd.SilenceUsage = true, true
	if err := cmd.Execute(); err == nil || !strings.Contains(err.Error(), terminalKey) {
		t.Fatalf("got %v", err)
	}
}

func TestArmReturnsAtOnceAndLeavesTheWaitRunningInItsOwnSession(t *testing.T) {
	dir := t.TempDir()
	release, report, output := filepath.Join(dir, "release"), filepath.Join(dir, "report"), filepath.Join(dir, "wait.log")
	script := filepath.Join(dir, "lr")
	body := fmt.Sprintf("#!/bin/sh\nwhile [ ! -e %q ]; do sleep 0.05; done\necho said\nprintf '%%s %%s %%s\\n' \"$$\" \"$(ps -o pgid= -p $$ | tr -d ' ')\" \"$%s\" > %q.tmp\necho \"$*\" >> %q.tmp\nmv %q.tmp %q\n", release, armedTag, report, report, report, report)
	if err := os.WriteFile(script, []byte(body), 0o700); err != nil { //nolint:gosec // the stub has to execute
		t.Fatal(err)
	}
	var stdout bytes.Buffer
	args := []string{"wait", "--wake", output, "5", "--", "sh", "-c", "exit 3"}
	if err := Arm(Options{Seconds: 5, Command: []string{"sh", "-c", "exit 3"}, Stdout: &stdout}, script, args, output); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(stdout.String(), "ARMED ") || !strings.HasSuffix(strings.TrimSpace(stdout.String()), output) {
		t.Fatalf("printed %q", stdout.String())
	}
	if _, err := os.Stat(report); err == nil {
		t.Fatal("Arm waited for the wait to run")
	}
	if err := os.WriteFile(release, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	var lines []string
	for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if content, err := os.ReadFile(report); err == nil {
			lines = strings.Split(strings.TrimSpace(string(content)), "\n")
			break
		}
	}
	if len(lines) != 2 {
		t.Fatalf("the detached wait never ran: %q", lines)
	}
	fields := strings.Fields(lines[0])
	if len(fields) != 3 || fields[0] != fields[1] || fields[2] != "1" {
		t.Fatalf("pid, group, tag %q: want a session leader carrying the armed tag", lines[0])
	}
	if lines[1] != strings.Join(args, " ") {
		t.Fatalf("args %q", lines[1])
	}
	said, err := os.ReadFile(output)
	if err != nil || !strings.Contains(string(said), "said") {
		t.Fatalf("output %q: %v", said, err)
	}
}

func TestAnArmWithABadSourceRefusesBeforeDetaching(t *testing.T) {
	output := filepath.Join(t.TempDir(), "wait.log")
	err := Arm(Options{Seconds: 5, Sources: []string{"landing-desk"}, Stdout: &bytes.Buffer{}}, "/nonexistent/lr", nil, output)
	if err == nil || !strings.Contains(err.Error(), "usage: lr wait") {
		t.Fatalf("got %v", err)
	}
	if _, statErr := os.Stat(output); statErr == nil {
		t.Fatal("a refused arm created its output file")
	}
}

func TestAWaitWithNeitherSourceNorCommandIsAUsageError(t *testing.T) {
	r := start(t, Options{Seconds: 1}).wait(t)
	if r.code != CodeUsage || !strings.Contains(r.errs.String(), "usage: lr wait") {
		t.Fatalf("got %d %q", r.code, r.errs.String())
	}
}

func TestAnArmedWaitWithOnlyADeadlineNamesWhatItNeeds(t *testing.T) {
	err := runArmed(t, &shelltest.Fake{}, filepath.Join(t.TempDir(), "wait.log"), "2800")
	if err == nil || err.Error() != Usage {
		t.Fatalf("got %v, want the usage naming a source or a command", err)
	}
}
