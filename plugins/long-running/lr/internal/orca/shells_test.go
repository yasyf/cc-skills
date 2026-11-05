package orca

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

const (
	shellTree  = "/w/old-lane"
	shellPID   = "4100"
	shellTerm  = "term_shell"
	shellAgent = "term_agent"
)

func shellTerminal() Terminal {
	return Terminal{Handle: shellTerm, Worktree: shellTree, Connected: true}
}

func shellSessions() Sessions {
	return Sessions{shellTerm: {{PID: shellPID, Command: "-/opt/homebrew/bin/fish"}}}
}

func shellOrca(reads ...map[string]bool) *fakeOrca {
	orca := newOrca()
	orca.listed = []Terminal{shellTerminal()}
	orca.sessions = shellSessions()
	orca.emptyShells = reads
	return orca
}

func runShells(t *testing.T, orca *fakeOrca, dryRun bool, pause Pause) (Tally, string) {
	t.Helper()
	var out bytes.Buffer
	gc := &GC{Orca: orca, Pause: pause, Out: &out, DryRun: dryRun}
	tally, err := gc.Shells(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return tally, out.String()
}

func TestAnEmptyQuietShellClosesAndItsWorktreeStays(t *testing.T) {
	orca := shellOrca(map[string]bool{shellPID: true})
	tally, _ := runShells(t, orca, false, unpaused(t))
	wantTally(t, tally, Tally{verbClosed: 1})
	wantCalls(t, orca, []string{"close", shellTerm})
}

func TestADryRunNamesTheShellAndClosesNothing(t *testing.T) {
	orca := shellOrca(map[string]bool{shellPID: true})
	tally, out := runShells(t, orca, true, unpaused(t))
	wantTally(t, tally, Tally{verbEnd: 1})
	wantCalls(t, orca)
	wantSaid(t, out, "would end "+shellTerm+" empty shell, quiet three days, in "+shellTree)
}

func TestAShellStays(t *testing.T) {
	cases := []struct {
		name  string
		shape func(*fakeOrca)
	}{
		{"with a child or a recent byte on its tty", func(o *fakeOrca) { o.emptyShells = []map[string]bool{{}} }},
		{"beside an agent terminal in its worktree", func(o *fakeOrca) {
			o.listed = append(o.listed, Terminal{Handle: shellAgent, Agent: "claude", Worktree: shellTree, Connected: true})
		}},
		{"beside a terminal Orca names no agent for but whose processes run in its worktree", func(o *fakeOrca) {
			o.listed = append(o.listed, Terminal{Handle: shellAgent, Worktree: shellTree, Connected: true})
			o.sessions[shellAgent] = []Session{{PID: "4300", Command: "-fish"}, {PID: "4301", PPID: "4300", Command: "claude"}}
		}},
		{"when Orca records no worktree for it", func(o *fakeOrca) { o.listed[0].Worktree = "" }},
		{"when it is disconnected", func(o *fakeOrca) { o.listed[0].Connected = false }},
		{"when a detached process carries its handle", func(o *fakeOrca) {
			o.sessions[shellTerm] = append(o.sessions[shellTerm], Session{PID: "4200", Command: "sprite proxy"})
		}},
		{"when no process carries its handle", func(o *fakeOrca) { o.sessions = Sessions{} }},
		{"when it is an agent terminal", func(o *fakeOrca) { o.listed[0].Agent = "codex" }},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			orca := shellOrca(map[string]bool{shellPID: true})
			c.shape(orca)
			tally, _ := runShells(t, orca, false, unpaused(t))
			wantTally(t, tally, Tally{})
			wantCalls(t, orca)
		})
	}
}

func TestAShellThatWokeBeforeItsCloseStays(t *testing.T) {
	orca := shellOrca(map[string]bool{shellPID: true}, map[string]bool{})
	tally, out := runShells(t, orca, false, unpaused(t))
	wantTally(t, tally, Tally{verbKeep: 1})
	wantCalls(t, orca)
	wantSaid(t, out, "keep "+shellTerm+" "+changed)
}

func TestARefusedShellCloseIsKept(t *testing.T) {
	orca := shellOrca(map[string]bool{shellPID: true})
	orca.closeRefusal = "terminal_busy"
	tally, _ := runShells(t, orca, false, unpaused(t))
	wantTally(t, tally, Tally{verbKeep: 1})
}

func TestAPauseClosesNoShell(t *testing.T) {
	orca := shellOrca(map[string]bool{shellPID: true})
	tally, _ := runShells(t, orca, false, Pause{Resumed: filepath.Join(t.TempDir(), "absent")})
	wantTally(t, tally, Tally{})
	wantCalls(t, orca)
}

func TestAPassClosesAtMostABatchOfShells(t *testing.T) {
	orca := newOrca()
	orca.sessions = Sessions{}
	empty := map[string]bool{}
	for index := range sweptBatch + 3 {
		handle := "term_" + strings.Repeat("x", index+1)
		orca.listed = append(orca.listed, Terminal{Handle: handle, Worktree: "/w/" + handle, Connected: true})
		orca.sessions[handle] = []Session{{PID: handle, Command: "-fish"}}
		empty[handle] = true
	}
	orca.emptyShells = []map[string]bool{empty}
	tally, _ := runShells(t, orca, false, unpaused(t))
	wantTally(t, tally, Tally{verbClosed: sweptBatch})
}

func TestEmptyShellsReadsChildlessLoginShellsByTheirTTY(t *testing.T) {
	dev := t.TempDir()
	stale := time.Now().Add(-shellQuiet - time.Hour)
	for name, modified := range map[string]time.Time{"ttys001": stale, "ttys002": time.Now(), "ttys003": stale} {
		path := filepath.Join(dev, name)
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, modified, modified); err != nil {
			t.Fatal(err)
		}
	}
	runner := holderRunner(map[string]string{
		"ps -axo pid=,ppid=,pgid=,tpgid=,comm=": "100 90 100 100 -/bin/fish\n200 90 200 200 -/bin/fish\n300 90 300 300 -/bin/fish\n301 300 301 300 sleep\n400 90 400 400 -/bin/fish\n",
		"ps -axo pid=,tty=":                     "  100 ttys001 \n  200 ttys002 \n  300 ttys003 \n  400 ttys004 \n",
	}, "")
	empty, err := Processes{Runner: runner, Dev: dev}.EmptyShells(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	got := make([]string, 0, len(empty))
	for pid := range empty {
		got = append(got, pid)
	}
	if !slices.Equal(got, []string{"100"}) {
		t.Fatalf("got %v", got)
	}
}

func runOrphans(t *testing.T, orca *fakeOrca, current string, keep, skip map[string]bool) Tally {
	t.Helper()
	var out bytes.Buffer
	gc := &GC{Orca: orca, Git: newGit(checkout(testWT)), Fleet: &fakeFleet{}, Pause: unpaused(t), Lock: filepath.Join(t.TempDir(), "remove.lock"), Out: &out, Keep: keep, SkipRuns: skip}
	tally, err := gc.OrphanedRun(context.Background(), current)
	if err != nil {
		t.Fatal(err)
	}
	return tally
}

func orphanOrca(run Run, rows ...Worker) *fakeOrca {
	orca := newOrca(rows...)
	orca.runs = []Run{run}
	for _, worker := range rows {
		orca.listed = append(orca.listed, Terminal{Handle: worker.Terminal, Worktree: testWT, Connected: true})
	}
	return orca
}

func TestARunWhoseCoordinatorIsGoneHasItsSettledOpenTerminalsSwept(t *testing.T) {
	orca := orphanOrca(Run{ID: "run_old", Coordinator: "term_gone"}, row("ctx_a"))
	tally := runOrphans(t, orca, "run_now", map[string]bool{}, nil)
	if tally[verbClosed]+tally[verbRemoved] == 0 {
		t.Fatalf("tally %v, calls %v", tally, orca.calls)
	}
}

func TestARunIsLeftAlone(t *testing.T) {
	cases := []struct {
		name string
		run  Run
		row  Worker
		keep map[string]bool
		skip map[string]bool
	}{
		{name: "when local config names it", run: Run{ID: "run_old", Coordinator: "term_gone"}, row: row("ctx_a"), skip: map[string]bool{"run_old": true}},
		{name: "when its coordinator's terminal is open", run: Run{ID: "run_old", Coordinator: "term_ctx_a"}, row: row("ctx_a")},
		{name: "when it is the drive's own Run", run: Run{ID: "run_now", Coordinator: "term_gone"}, row: row("ctx_a")},
		{name: "when it holds mail from no Run", run: Run{ID: unboundRun, Coordinator: "term_gone"}, row: row("ctx_a")},
		{name: "when it is legacy state", run: Run{ID: "run_old", Coordinator: "term_gone", Legacy: 1}, row: row("ctx_a")},
		{name: "when its dispatch is unsettled", run: Run{ID: "run_old", Coordinator: "term_gone"}, row: row("ctx_a", status("dispatched"))},
		{name: "when the registry keeps its dispatch", run: Run{ID: "run_old", Coordinator: "term_gone"}, row: row("ctx_a"), keep: map[string]bool{"ctx_a": true}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			orca := orphanOrca(c.run, c.row)
			tally := runOrphans(t, orca, "run_now", c.keep, c.skip)
			wantTally(t, tally, Tally{})
			wantCalls(t, orca)
		})
	}
}

func TestAShellPassReadsNoProcessesWhileEveryShellHasRecentOutput(t *testing.T) {
	orca := newOrca()
	terminal := shellTerminal()
	terminal.LastOutputMS = fakeNow - 1_000
	orca.listed = []Terminal{terminal}
	tally, _ := runShells(t, orca, false, unpaused(t))
	wantTally(t, tally, Tally{})
}

func TestRunsFollowsTheCursorRunListPrints(t *testing.T) {
	pages := map[string]string{
		"":     `{"ok":true,"_meta":{},"result":{"runs":[{"id":"run_a","coordinator_handle":"term_a","legacy":0}],"nextCursor":"next"}}`,
		"next": `{"ok":true,"_meta":{},"result":{"runs":[{"id":"run_b","coordinator_handle":"term_b","legacy":1}],"nextCursor":null}}`,
	}
	client, _ := clientFor(func(argv []string) (string, string, int) {
		cursor := ""
		if at := slices.Index(argv, "--cursor"); at >= 0 {
			cursor = argv[at+1]
		}
		return pages[cursor], "", 0
	})
	runs, err := client.Runs(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	want := []Run{{ID: "run_a", Coordinator: "term_a"}, {ID: "run_b", Coordinator: "term_b", Legacy: 1}}
	if !slices.Equal(runs, want) {
		t.Fatalf("got %v", runs)
	}
}

func TestATruncatedTerminalListProvesNothing(t *testing.T) {
	client, _ := clientFor(constant(`{"ok":true,"result":{"terminals":[{"handle":"term_a"}],"truncated":true}}`))
	if _, err := client.Terminals(context.Background()); err == nil {
		t.Fatal("a truncated list read as complete")
	}
}

func TestSkippedRunsReadsLocalConfig(t *testing.T) {
	configured := &scripted{reply: func([]string) (string, string, int) { return "run_a\nrun_b\n", "", 0 }}
	skipped, err := skippedRuns(context.Background(), configured)
	if err != nil || !skipped["run_a"] || !skipped["run_b"] || len(skipped) != 2 {
		t.Fatalf("got %v, err %v", skipped, err)
	}
	unset := &scripted{reply: func([]string) (string, string, int) { return "", "", gitConfigUnset }}
	if skipped, err = skippedRuns(context.Background(), unset); err != nil || len(skipped) != 0 {
		t.Fatalf("got %v, err %v", skipped, err)
	}
	broken := &scripted{reply: func([]string) (string, string, int) { return "", "fatal: bad config", 128 }}
	if _, err = skippedRuns(context.Background(), broken); err == nil {
		t.Fatal("an unreadable config read as no skips")
	}
}
