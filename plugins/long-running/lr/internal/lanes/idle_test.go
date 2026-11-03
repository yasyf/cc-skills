package lanes

import (
	"context"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type recordingRunner struct {
	commands []shell.Command
	reply    func(shell.Command) (shell.Result, error)
}

func (r *recordingRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	r.commands = append(r.commands, c)
	return r.reply(c)
}

func TestSettledGCRunsANewPassEveryFiveMinutesFromTheCheckoutWithTheDrivesCCRemote(t *testing.T) {
	state := t.TempDir()
	writeJSON(t, filepath.Join(state, "runner.json"), map[string]any{"orca": map[string]any{"sprite": map[string]any{"env": map[string]string{"WORKER_CC_REMOTE": "/pin/cc-remote", "CC_REMOTE_CONFIG": "/pin/config.yaml"}}}})
	runner := &recordingRunner{reply: func(shell.Command) (shell.Result, error) {
		return shell.Result{Stdout: "closed=1\n"}, nil
	}}
	claude := t.TempDir()
	_, s := driveState(t)
	env := Env{Runner: runner, Config: config.Config{OrcaRun: "run_1", Checkout: "/checkout"}, State: state, Claude: claude, Local: t.TempDir()}
	if _, err := s.WantLane(storeLane("wanted-1"), t0); err != nil {
		t.Fatal(err)
	}
	writeJSON(t, filepath.Join(env.receipts(), "wanted-1.json"), map[string]any{"result": map[string]string{"dispatchId": "ctx_wanted"}})
	writeJSON(t, filepath.Join(env.receipts(), "ended-1.json"), map[string]any{"result": map[string]string{"dispatchId": "ctx_ended"}})
	check := &SettledGC{Env: env, Store: s, Log: slog.Default()}
	for _, at := range []time.Duration{0, time.Minute, 5 * time.Minute} {
		if err := check.Check(context.Background(), t0.Add(at)); err != nil {
			t.Fatal(err)
		}
	}
	if len(runner.commands) != 2 {
		t.Fatalf("ran %d passes", len(runner.commands))
	}
	got := runner.commands[0]
	equal(t, got.Argv, []string{filepath.Join(claude, "long-running", "current", "bin", "lr"), "gc", "--run", "run_1", "--keep", "ctx_wanted", "--new"})
	equal(t, got.Dir, "/checkout")
	equal(t, got.Env, []string{"WORKER_CC_REMOTE=/pin/cc-remote", "CC_REMOTE_CONFIG=/pin/config.yaml"})
}

func TestAFailedSettledPassIsLoggedAndTheSupervisorContinues(t *testing.T) {
	runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
		return shell.Result{Code: 1, Stderr: "orca refused"}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "orca refused"}
	}}
	_, s := driveState(t)
	check := &SettledGC{Env: Env{Runner: runner, Config: config.Config{OrcaRun: "run_1"}, State: t.TempDir(), Claude: t.TempDir()}, Store: s, Log: slog.Default()}
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
}

func TestTheIdleCensusCountsProcessesAndGroupsWhatGCWouldDo(t *testing.T) {
	a, _ := driveState(t)
	raw, err := os.ReadFile(a.ConfigPath)
	if err != nil {
		t.Fatal(err)
	}
	withRun := strings.Replace(string(raw), "{", `{"orca_run":"run_1",`, 1)
	if err := os.WriteFile(a.ConfigPath, []byte(withRun), 0o600); err != nil {
		t.Fatal(err)
	}
	plan := strings.Join([]string{
		"would remove /w/a stopping term_a pid 1 ppid 2 pgid 1 started Mon Nov 2 23:49:38 2026 claude",
		"would remove /w/b",
		"would close term_c dispatch ctx_c pid 3 ppid 4 pgid 3 started Mon Nov 2 23:49:38 2026 codex",
		"keep /w/d git cannot read it: fatal: not a git repository",
		"keep /w/e git cannot read it: Stopping at filesystem boundary",
		"keep /w/f unpushed commits on yasyf/f",
		"keep pool-a terminal term_g is unreadable or disconnected, so its idleness is unknown",
		"keep pool-b terminal term_h is unreadable or disconnected, so its idleness is unknown",
		"keep pool-c terminal term_i is unreadable or disconnected, so its idleness is unknown",
		"close=1 keep=6 remove=2",
	}, "\n") + "\n"
	runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
		switch {
		case c.Argv[0] == "ps":
			return shell.Result{Stdout: "claude\n/usr/bin/login\n/x/codex\nclaude\n"}, nil
		case c.Argv[0] == "orca" && c.Argv[1] == "worktree":
			return shell.Result{Stdout: `{"ok":true,"result":{"worktrees":[{}],"totalCount":101,"truncated":true}}`}, nil
		case c.Argv[0] == "orca" && c.Argv[1] == "terminal":
			return shell.Result{Stdout: `{"ok":true,"result":{"terminals":[{}],"totalCount":178,"truncated":true}}`}, nil
		}
		return shell.Result{Stdout: plan}, nil
	}}
	a.Runner = runner
	got, err := execute(t, Command(a), "--idle")
	if err != nil {
		t.Fatal(err)
	}
	want := strings.Join([]string{
		"processes claude=2 codex=1",
		"orca worktrees=101 terminals=178",
		"gc would close=1 remove=2",
		"keep 3 terminal <terminal> is unreadable or disconnected, so its idleness is unknown",
		"keep 2 git cannot read it",
		"keep 1 unpushed commits",
	}, "\n") + "\n"
	equal(t, got, want)
	last := runner.commands[len(runner.commands)-1]
	if !slices.Contains(last.Argv, "--dry-run") || slices.Contains(last.Argv, "--new") {
		t.Fatalf("argv %v", last.Argv)
	}
}
