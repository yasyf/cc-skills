package lanes

import (
	"cmp"
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
	equal(t, got.Argv, []string{filepath.Join(claude, "long-running", "current", "bin", "lr"), "gc", "--run", "run_1", "--keep", "ctx_wanted", "--new", "--shells", "--other-runs"})
	equal(t, got.Dir, "/checkout")
	equal(t, got.Env, []string{"WORKER_CC_REMOTE=/pin/cc-remote", "CC_REMOTE_CONFIG=/pin/config.yaml"})
}

func TestTheCleanupPassKeepsARetiredLaneThatStillHoldsAStandingDutyUntilASuccessorTakesIt(t *testing.T) {
	_, s := driveState(t)
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: t.TempDir()}
	dutyBrief := filepath.Join(t.TempDir(), "duty.md")
	if err := os.WriteFile(dutyBrief, []byte("Standing: owner of the sand-next gate\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	lanes := []struct {
		name, role, standing, brief, retiredBy, predecessor string
		state                                               store.LaneState
	}{
		{name: "wanted-1", state: store.LaneDesired},
		{name: "parked-1", state: store.LaneHeld},
		{name: "duty-row-1", standing: "owner of item 40", retiredBy: "done"},
		{name: "duty-brief-1", brief: dutyBrief, retiredBy: "done"},
		{name: "desk-1", role: roleDesk, retiredBy: Root},
		{name: "plain-1", retiredBy: "done"},
		{name: "replaced-desk-1", role: roleDesk, retiredBy: "replaced-desk-2"},
		{name: "replaced-desk-2", role: roleDesk, state: store.LaneDesired},
		{name: "handed-1", standing: "owner of the m551 row", retiredBy: "done"},
		{name: "handed-2", standing: "owner of the m551 row", predecessor: "handed-1", state: store.LaneDesired},
	}
	for _, lane := range lanes {
		row := storeLane(lane.name)
		row.Role, row.Standing, row.Predecessor = cmp.Or(lane.role, row.Role), lane.standing, lane.predecessor
		row.Brief = cmp.Or(lane.brief, filepath.Join(t.TempDir(), "absent.md"))
		if _, err := s.WantLane(row, t0); err != nil {
			t.Fatal(err)
		}
		if err := s.SetLaneState(lane.name, cmp.Or(lane.state, store.LaneRetired), t0, lane.retiredBy, ""); err != nil {
			t.Fatal(err)
		}
		writeJSON(t, filepath.Join(env.receipts(), lane.name+".json"), map[string]any{"result": map[string]string{"dispatchId": "ctx_" + lane.name}})
	}
	keep, err := env.keepWanted(s)
	if err != nil {
		t.Fatal(err)
	}
	var kept []string
	for i := 1; i < len(keep); i += 2 {
		kept = append(kept, keep[i])
	}
	slices.Sort(kept)
	equal(t, kept, []string{"ctx_desk-1", "ctx_duty-brief-1", "ctx_duty-row-1", "ctx_handed-2", "ctx_parked-1", "ctx_replaced-desk-2", "ctx_wanted-1"})
}

func TestOnlyTheSupervisorsGCReadsTheRoster(t *testing.T) {
	for _, rostered := range []bool{false, true} {
		runner := &recordingRunner{reply: func(shell.Command) (shell.Result, error) { return shell.Result{}, nil }}
		env := Env{Runner: runner, Config: config.Config{OrcaRun: "run_1"}, State: t.TempDir(), Claude: t.TempDir(), Rostered: rostered}
		if _, err := env.gc(context.Background(), "--done", "ctx_a"); err != nil {
			t.Fatal(err)
		}
		want := []string{"gc", "--run", "run_1", "--done", "ctx_a"}
		if rostered {
			want = append(want, "--roster")
		}
		equal(t, runner.commands[0].Argv[1:], want)
	}
}

func TestSettledGCStartsNoPassWhileARestartWaitsForOne(t *testing.T) {
	runner := &recordingRunner{reply: func(shell.Command) (shell.Result, error) {
		return shell.Result{Stdout: "closed=1\n"}, nil
	}}
	_, s := driveState(t)
	env := Env{Runner: runner, Config: config.Config{OrcaRun: "run_1", Checkout: "/checkout"}, State: t.TempDir(), Claude: t.TempDir(), Local: t.TempDir()}
	check := &SettledGC{Env: env, Store: s, Log: slog.Default()}
	YieldGC(true)
	t.Cleanup(func() { YieldGC(false) })
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	equal(t, len(runner.commands), 0)
	YieldGC(false)
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	if !slices.ContainsFunc(runner.commands, func(c shell.Command) bool { return slices.Contains(c.Argv, "gc") }) {
		t.Fatalf("no pass after the restart cleared: %v", runner.commands)
	}
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

type retiredLane struct {
	name, brief, role, last, retiredBy, run string
	silent                                  time.Duration
	state                                   store.LaneState
	foreign, closed, noRun                  bool
}

type orcaFleet struct {
	open  []string
	reads int
}

func retiredLanes(t *testing.T, s *store.Store, env Env, lanes ...retiredLane) *orcaFleet {
	t.Helper()
	fleet := &orcaFleet{open: []string{"term_ctx_not-ours"}}
	for _, lane := range lanes {
		row := storeLane(lane.name)
		row.Brief, row.Role = lane.brief, cmp.Or(lane.role, row.Role)
		if _, err := s.WantLane(row, t0); err != nil {
			t.Fatal(err)
		}
		if err := s.SetLaneState(lane.name, cmp.Or(lane.state, store.LaneRetired), t0, cmp.Or(lane.retiredBy, retiredSilent), ""); err != nil {
			t.Fatal(err)
		}
		if err := s.LaneHeard(lane.name, t0.Add(-lane.silent), lane.last, 1); err != nil {
			t.Fatal(err)
		}
		if lane.foreign {
			continue
		}
		run := cmp.Or(lane.run, env.Config.OrcaRun)
		if lane.noRun {
			run = ""
		}
		writeJSON(t, filepath.Join(env.receipts(), lane.name+".json"), map[string]any{"result": map[string]any{
			"dispatchId": "ctx_" + lane.name,
			"runId":      run,
			"effects":    []map[string]string{{"kind": "worktree", "id": "repo::/w/" + lane.name}, {"kind": "terminal", "role": "agent", "id": "term_ctx_" + lane.name}},
		}})
		if !lane.closed {
			fleet.open = append(fleet.open, "term_ctx_"+lane.name)
		}
	}
	return fleet
}

func (f *orcaFleet) reply(c shell.Command) string {
	f.reads++
	if !slices.Contains(c.Argv, "terminal") {
		return `{"ok":true,"result":{"totalCount":0}}`
	}
	terminals := make([]map[string]string, 0, len(f.open))
	for _, handle := range f.open {
		terminals = append(terminals, map[string]string{"handle": handle})
	}
	raw, _ := json.Marshal(map[string]any{"result": map[string]any{"totalCount": len(f.open), "terminals": terminals}})
	return string(raw)
}

func TestStaleRetiredNamesALaneThatEndedItselfAfterAQuarterHourAndAnyOtherAfterTwoHours(t *testing.T) {
	_, s := driveState(t)
	dir := t.TempDir()
	brief := func(name, text string) string {
		path := filepath.Join(dir, name+".md")
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	var fleet *orcaFleet
	runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
		return shell.Result{Stdout: fleet.reply(c)}, nil
	}}
	bus := &fakeBus{open: []client.Record{{Lane: "asking-1", Kind: "decide"}}}
	env := Env{Bus: bus, Runner: runner, Config: config.Config{Drive: testDrive, OrcaRun: "run_1"}, State: t.TempDir(), Claude: t.TempDir(), Local: t.TempDir()}
	ordinary := brief("ordinary", "Step: 1\n")
	fleet = retiredLanes(t, s, env,
		retiredLane{name: "silent-1", brief: ordinary, last: "report", silent: 3 * time.Hour},
		retiredLane{name: "quiet-1", brief: ordinary, last: "report", silent: time.Hour},
		retiredLane{name: "landed-1", brief: ordinary, last: kindLanded, silent: 20 * time.Minute},
		retiredLane{name: "stopped-1", brief: ordinary, last: "stopped", silent: 20 * time.Minute},
		retiredLane{name: "just-landed-1", brief: ordinary, last: kindLanded, silent: 5 * time.Minute},
		retiredLane{name: "asking-1", brief: ordinary, last: "stopped", retiredBy: retiredStopped, silent: 20 * time.Minute},
		retiredLane{name: "stood-down-1", brief: ordinary, last: "report", retiredBy: retiredStopped, silent: 20 * time.Minute},
		retiredLane{name: "done-1", brief: ordinary, last: "report", retiredBy: "done", silent: 20 * time.Minute},
		retiredLane{name: "coordinator-1", brief: ordinary, last: "report", retiredBy: Root, silent: 20 * time.Minute},
		retiredLane{name: "succeeded-1", brief: ordinary, last: "report", retiredBy: "wanted-1", silent: 20 * time.Minute},
		retiredLane{name: "closed-1", brief: ordinary, last: kindLanded, silent: 20 * time.Minute, closed: true},
		retiredLane{name: "other-run-1", brief: ordinary, last: kindLanded, silent: 20 * time.Minute, run: "run_2"},
		retiredLane{name: "no-run-1", brief: ordinary, last: kindLanded, silent: 20 * time.Minute, noRun: true},
		retiredLane{name: "wanted-1", brief: ordinary, last: kindLanded, silent: 3 * time.Hour, state: store.LaneDesired},
		retiredLane{name: "standing-1", brief: brief("standing", "Standing: landing desk\n"), last: kindLanded, silent: 3 * time.Hour},
		retiredLane{name: "desk-1", brief: ordinary, role: roleDesk, last: kindLanded, silent: 3 * time.Hour},
		retiredLane{name: "foreign-1", brief: ordinary, last: kindLanded, silent: 3 * time.Hour, foreign: true},
		retiredLane{name: "no-brief-1", brief: filepath.Join(dir, "absent.md"), last: kindLanded, silent: 3 * time.Hour},
		retiredLane{name: Root, brief: ordinary, last: kindLanded, silent: 3 * time.Hour},
		retiredLane{name: OwnerRelay, brief: ordinary, last: kindLanded, silent: 3 * time.Hour},
	)
	got, misread, err := env.staleRetired(context.Background(), s, t0)
	if err != nil {
		t.Fatal(err)
	}
	named := make([]string, 0, len(got))
	for _, lane := range got {
		named = append(named, fmt.Sprintf("%s %s ended=%v", lane.name, lane.dispatch, lane.ended))
	}
	equal(t, named, []string{
		"coordinator-1 ctx_coordinator-1 ended=true",
		"done-1 ctx_done-1 ended=true",
		"landed-1 ctx_landed-1 ended=true",
		"silent-1 ctx_silent-1 ended=false",
		"stood-down-1 ctx_stood-down-1 ended=true",
		"stopped-1 ctx_stopped-1 ended=true",
		"succeeded-1 ctx_succeeded-1 ended=true",
	})
	slices.Sort(misread)
	equal(t, misread, []string{
		`no-run-1: its launch receipt names Orca Run "", not the drive's run_1, so its session stays open`,
		`other-run-1: its launch receipt names Orca Run "run_2", not the drive's run_1, so its session stays open`,
	})
}

func TestTheHarvestReadsOrcaAsOftenForAHundredFinishedLanesAsForOne(t *testing.T) {
	pass := func(lanes int) (int, []string) {
		_, s := driveState(t)
		brief := filepath.Join(t.TempDir(), "brief.md")
		if err := os.WriteFile(brief, []byte("Step: 1\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		var fleet *orcaFleet
		runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
			switch c.Argv[0] {
			case "ps":
				return shell.Result{Stdout: "claude\n"}, nil
			case "orca":
				return shell.Result{Stdout: fleet.reply(c)}, nil
			}
			return shell.Result{Stdout: "nothing to reclaim\n"}, nil
		}}
		env := Env{Bus: &fakeBus{}, Runner: runner, Config: config.Config{Drive: testDrive, OrcaRun: "run_1", Checkout: "/checkout"}, State: t.TempDir(), Claude: t.TempDir(), Local: t.TempDir()}
		finished := make([]retiredLane, lanes)
		for n := range finished {
			finished[n] = retiredLane{name: fmt.Sprintf("done-%d", n), brief: brief, last: "done", retiredBy: "done", silent: 20 * time.Minute}
		}
		fleet = retiredLanes(t, s, env, finished...)
		if err := (&SettledGC{Env: env, Store: s, Log: slog.Default()}).Check(context.Background(), t0); err != nil {
			t.Fatal(err)
		}
		var passes []string
		for _, c := range runner.commands {
			if slices.Contains(c.Argv, "gc") {
				passes = append(passes, strings.Join(c.Argv[4:], " "))
			}
		}
		return fleet.reads, passes
	}
	oneReads, onePasses := pass(1)
	hundredReads, hundredPasses := pass(100)
	if oneReads != hundredReads {
		t.Fatalf("the supervisor read Orca %d times for one finished lane and %d for a hundred", oneReads, hundredReads)
	}
	if len(onePasses) != 1 || len(hundredPasses) != 1 || strings.Count(hundredPasses[0], "--finished") != 100 {
		t.Fatalf("one lane ran %q, a hundred ran %q", onePasses, hundredPasses)
	}
}

func TestEveryCloseOfAFinishedLanePostsARecordNamingItWithTheDaysCount(t *testing.T) {
	_, s := driveState(t)
	brief := filepath.Join(t.TempDir(), "brief.md")
	if err := os.WriteFile(brief, []byte("Step: 1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var fleet *orcaFleet
	runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
		switch c.Argv[0] {
		case "ps":
			return shell.Result{Stdout: "claude\n"}, nil
		case "orca":
			return shell.Result{Stdout: fleet.reply(c)}, nil
		}
		return shell.Result{Stdout: "keep ctx_held-1 stale but its screen does not read as an empty prompt\nstopped ctx_landed-1 stopped\nclosed term-1 dispatch ctx_landed-1 is done\nkeep /w/settled uncommitted changes\nclosed term-2 dispatch ctx_settled-1\nclosed=2 keep=2 stopped=1\n"}, nil
	}}
	bus := &fakeBus{tail: []client.Record{{Topic: closedTopic}, {Topic: ReapTopic}}}
	env := Env{Bus: bus, Runner: runner, Config: config.Config{Drive: testDrive, OrcaRun: "run_1", Checkout: "/checkout"}, State: t.TempDir(), Claude: t.TempDir(), Local: t.TempDir()}
	fleet = retiredLanes(t, s, env,
		retiredLane{name: "landed-1", brief: brief, last: kindLanded, retiredBy: Root, silent: 20 * time.Minute},
		retiredLane{name: "held-1", brief: brief, last: "stopped", retiredBy: Root, silent: 20 * time.Minute},
		retiredLane{name: "settled-1", brief: brief, last: "done", retiredBy: Root, silent: 20 * time.Minute},
	)
	check := &SettledGC{Env: env, Store: s, Log: slog.Default()}
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	gc := slices.IndexFunc(runner.commands, func(c shell.Command) bool { return slices.Contains(c.Argv, "gc") })
	equal(t, runner.commands[gc].Argv[4:], []string{"--finished", "ctx_held-1", "--stale", "ctx_held-1", "--finished", "ctx_landed-1", "--stale", "ctx_landed-1", "--finished", "ctx_settled-1", "--stale", "ctx_settled-1", "--new", "--shells", "--other-runs"})
	equal(t, bus.query.Since, time.Date(2026, 10, 31, 0, 0, 0, 0, pacificZone))
	closed := slices.DeleteFunc(slices.Clone(bus.posted), func(d client.Draft) bool { return d.Topic != closedTopic })
	said := make([]string, 0, len(closed))
	for _, d := range closed {
		said = append(said, d.ID+" "+d.Text)
	}
	equal(t, said, []string{
		closedTopic + ":ctx_landed-1 landed-1's terminal closed: its last record was landed and the registry retired it (root); lr gc found it at an empty prompt under the agent process it launched. Dispatch ctx_landed-1. Lane terminals closed today: 2.",
		closedTopic + ":ctx_settled-1 settled-1's terminal closed: its last record was done and the registry retired it (root); lr gc found it at an empty prompt under the agent process it launched. Dispatch ctx_settled-1. Lane terminals closed today: 3.",
	})
}

func TestAPassCutOffByItsDeadlineStillNamesTheTerminalsItClosed(t *testing.T) {
	_, s := driveState(t)
	brief := filepath.Join(t.TempDir(), "brief.md")
	if err := os.WriteFile(brief, []byte("Step: 1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var fleet *orcaFleet
	runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
		switch c.Argv[0] {
		case "ps":
			return shell.Result{Stdout: "claude\n"}, nil
		case "orca":
			return shell.Result{Stdout: fleet.reply(c)}, nil
		}
		return shell.Result{Stdout: "closed term-1 dispatch ctx_settled-1\n", Code: shell.CodeTimedOut}, context.DeadlineExceeded
	}}
	bus := &fakeBus{}
	env := Env{Bus: bus, Runner: runner, Config: config.Config{Drive: testDrive, OrcaRun: "run_1", Checkout: "/checkout"}, State: t.TempDir(), Claude: t.TempDir(), Local: t.TempDir()}
	fleet = retiredLanes(t, s, env, retiredLane{name: "settled-1", brief: brief, last: "done", silent: 20 * time.Minute})
	check := &SettledGC{Env: env, Store: s, Log: slog.Default()}
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	if len(bus.posted) != 1 || bus.posted[0].ID != closedTopic+":ctx_settled-1" {
		t.Fatalf("posted %+v", bus.posted)
	}
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
	var since time.Time
	_, s := driveState(t)
	runner := &recordingRunner{reply: func(c shell.Command) (shell.Result, error) {
		if slices.Contains(c.Argv, "gc") {
			during = GCPassRunning()
			read, err := GCPassSince(s)
			if err != nil {
				t.Fatal(err)
			}
			since = read
		}
		return shell.Result{}, nil
	}}
	check := &SettledGC{Env: Env{Runner: runner, Config: config.Config{OrcaRun: "run_1"}, State: t.TempDir(), Claude: t.TempDir()}, Store: s, Log: slog.Default()}
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	if !during || GCPassRunning() {
		t.Fatalf("running during the pass %v, after it %v", during, GCPassRunning())
	}
	if !since.Equal(t0) {
		t.Fatalf("status read the pass as running since %v, want %v", since, t0)
	}
	if after, err := GCPassSince(s); err != nil || !after.IsZero() {
		t.Fatalf("status reads a finished pass as running since %v: %v", after, err)
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
