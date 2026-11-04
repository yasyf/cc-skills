package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
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
	passes := slices.DeleteFunc(slices.Clone(runner.commands), func(c shell.Command) bool { return !slices.Contains(c.Argv, "gc") })
	if len(passes) != 2 {
		t.Fatalf("ran %d passes", len(passes))
	}
	got := passes[0]
	equal(t, got.Argv, []string{filepath.Join(claude, "long-running", "current", "bin", "lr"), "gc", "--run", "run_1", "--keep", "ctx_wanted", "--new"})
	equal(t, got.Dir, "/checkout")
	equal(t, got.Env, []string{"WORKER_CC_REMOTE=/pin/cc-remote", "CC_REMOTE_CONFIG=/pin/config.yaml"})
}

func reapPass(t *testing.T, gcSaid string, terminals ...int) ([]client.Draft, []shell.Command) {
	t.Helper()
	listed := 0
	runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
		switch {
		case c.Argv[0] == "ps":
			return shell.Result{Stdout: "claude\ncodex\n"}, nil
		case c.Argv[0] == "orca" && c.Argv[1] == "worktree":
			return shell.Result{Stdout: `{"result":{"totalCount":10}}`}, nil
		case c.Argv[0] == "orca" && c.Argv[1] == "terminal":
			total := terminals[listed]
			listed++
			return shell.Result{Stdout: fmt.Sprintf(`{"result":{"totalCount":%d}}`, total)}, nil
		}
		return shell.Result{Stdout: gcSaid}, nil
	}}
	_, s := driveState(t)
	bus := &fakeBus{}
	env := Env{Bus: bus, Runner: runner, Config: config.Config{Drive: testDrive, OrcaRun: "run_1", Checkout: "/checkout"}, State: t.TempDir(), Claude: t.TempDir(), Local: t.TempDir()}
	check := &SettledGC{Env: env, Store: s, Log: slog.Default()}
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	return bus.posted, runner.commands
}

func TestAPassThatReclaimedPostsItsBeforeAndAfterCounts(t *testing.T) {
	posted, _ := reapPass(t, "removed /w/a\nremoved=1 stopping=1 keep=2\n", 8, 6)
	if len(posted) != 1 || posted[0].Topic != ReapTopic || posted[0].Text != "sessions 2 to 2, worktrees 10 to 10, terminals 8 to 6; this pass: removed=1 stopping=1 keep=2" {
		t.Fatalf("posted %+v", posted)
	}
}

func TestAPassThatOnlyKeptPostsNothing(t *testing.T) {
	if posted, _ := reapPass(t, "keep /w/a dirty\nkeep=1\n", 8, 8); len(posted) != 0 {
		t.Fatalf("posted %+v", posted)
	}
}

func TestStaleRetiredNamesOnlyASilentRetiredUnsettledLaneWithAnOrdinaryBrief(t *testing.T) {
	_, s := driveState(t)
	dir := t.TempDir()
	brief := func(name, text string) string {
		path := filepath.Join(dir, name+".md")
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	statuses := map[string]string{}
	runner := &recordingRunner{reply: func(shell.Command) (shell.Result, error) {
		rows := make([]map[string]string, 0, len(statuses))
		for dispatch, status := range statuses {
			rows = append(rows, map[string]string{"dispatchId": dispatch, "dispatchStatus": status})
		}
		raw, err := json.Marshal(map[string]any{"ok": true, "result": map[string]any{"workers": rows}})
		return shell.Result{Stdout: string(raw)}, err
	}}
	env := Env{Runner: runner, Config: config.Config{Drive: testDrive, OrcaRun: "run_1"}, State: t.TempDir(), Claude: t.TempDir(), Local: t.TempDir()}
	lane := func(name, briefPath, status string, silent time.Duration, state store.LaneState) {
		t.Helper()
		row := storeLane(name)
		row.Brief = briefPath
		if _, err := s.WantLane(row, t0); err != nil {
			t.Fatal(err)
		}
		if err := s.SetLaneState(name, state, t0, "done", ""); err != nil {
			t.Fatal(err)
		}
		if err := s.LaneHeard(name, t0.Add(-silent), "report", 1); err != nil {
			t.Fatal(err)
		}
		writeJSON(t, filepath.Join(env.receipts(), name+".json"), map[string]any{"result": map[string]string{"dispatchId": "ctx_" + name}})
		statuses["ctx_"+name] = status
	}
	ordinary := brief("ordinary", "Step: 1\n")
	lane("stale-1", ordinary, "dispatched", 3*time.Hour, store.LaneRetired)
	lane("recent-1", ordinary, "dispatched", time.Hour, store.LaneRetired)
	lane("settled-1", ordinary, "completed", 3*time.Hour, store.LaneRetired)
	lane("wanted-1", ordinary, "dispatched", 3*time.Hour, store.LaneDesired)
	lane("standing-1", brief("standing", "Standing: landing desk\n"), "dispatched", 3*time.Hour, store.LaneRetired)
	lane("no-brief-1", filepath.Join(dir, "absent.md"), "dispatched", 3*time.Hour, store.LaneRetired)
	lane(Root, ordinary, "dispatched", 3*time.Hour, store.LaneRetired)
	lane(OwnerRelay, ordinary, "dispatched", 3*time.Hour, store.LaneRetired)
	got, err := env.staleRetired(context.Background(), s, t0)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, got, []string{"--stale", "ctx_stale-1"})
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

func TestACleanupPassHoldsARestartOnlyWhileItRuns(t *testing.T) {
	var during bool
	runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
		if slices.Contains(c.Argv, "gc") {
			during = GCPassRunning()
		}
		return shell.Result{}, nil
	}}
	_, s := driveState(t)
	check := &SettledGC{Env: Env{Runner: runner, Config: config.Config{OrcaRun: "run_1"}, State: t.TempDir(), Claude: t.TempDir()}, Store: s, Log: slog.Default()}
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	if !during || GCPassRunning() {
		t.Fatalf("running during the pass %v, after it %v", during, GCPassRunning())
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
