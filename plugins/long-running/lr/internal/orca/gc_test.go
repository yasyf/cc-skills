package orca

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var claudeDone = []string{
	"  The build 2210 JSON I pulled is at /tmp/b2210-evidence.json.",
	"✻ Baked for 8m 57s · done 1:17 PM",
	strings.Repeat("─", 80),
	"❯",
	strings.Repeat("─", 80),
	"  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
	"                                                       Now using usage credits",
	"                                                                 168428 tokens",
	"     current: 2.1.287 · latest: 2.1.288 ✔ Update installed · Restart to update",
	"                                        new task? /clear to save 168.4k tokens",
}

func wantTally(t *testing.T, got Tally, want Tally) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("tally %v, want %v", got, want)
	}
}

func wantCalls(t *testing.T, orca *fakeOrca, want ...[]string) {
	t.Helper()
	if len(orca.calls) == 0 && len(want) == 0 {
		return
	}
	if !reflect.DeepEqual(orca.calls, want) {
		t.Fatalf("calls %v, want %v", orca.calls, want)
	}
}

func wantSaid(t *testing.T, out string, want ...string) {
	t.Helper()
	for _, line := range want {
		if !strings.Contains(out, line) {
			t.Errorf("output %q lacks %q", out, line)
		}
	}
}

func action(t *testing.T, worker Worker, live map[string]bool, idle Idleness, ended map[string]string) *Action {
	t.Helper()
	orca := newOrca()
	orca.idle[worker.Terminal] = []Idleness{idle}
	found, err := terminalAction(context.Background(), worker, live, orca, ended)
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func TestTerminalActions(t *testing.T) {
	cases := []struct {
		name   string
		worker Worker
		live   map[string]bool
		idle   Idleness
		ended  map[string]string
		verb   string
		target string
		reason string
	}{
		{name: "a done lane's busy terminal is ended", worker: row("ctx_a", status("dispatched")), idle: IdleBusy, ended: map[string]string{"ctx_a": "none"}, verb: verbEnd, target: "term_ctx_a", reason: "dispatch ctx_a is done"},
		{name: "a terminal worker-stop closed needs nothing", worker: row("ctx_a", status(statusFailed), ownedTerminal()), idle: IdleQuiet, ended: map[string]string{"ctx_a": closedByStop}},
		{name: "a done lane's terminal Orca retained on abandon is ended", worker: row("ctx_a", status(statusFailed), state("active"), ownership("owned"), retained("user_requested")), idle: IdleQuiet, ended: map[string]string{"ctx_a": "none"}, verb: verbEnd, target: "term_ctx_a", reason: "dispatch ctx_a is done"},
		{name: "an owned terminal is released through Orca", worker: row("ctx_a", state("reclaimable"), ownedTerminal()), idle: IdleQuiet, verb: verbRelease, target: "ctx_a"},
		{name: "an external idle terminal is closed", worker: row("ctx_a"), idle: IdleQuiet, verb: verbClose, target: "term_ctx_a", reason: "dispatch ctx_a"},
		{name: "a busy external terminal is kept", worker: row("ctx_a"), idle: IdleBusy, verb: verbKeep, target: "term_ctx_a", reason: notIdle},
		{name: "a stale handle is already gone", worker: row("ctx_a"), idle: IdleUnknown},
		{name: "a terminal a live dispatch reuses is kept", worker: row("ctx_a"), live: map[string]bool{"term_ctx_a": true}, idle: IdleQuiet, verb: verbKeep, target: "term_ctx_a", reason: "serves an unsettled dispatch"},
		{name: "a user-retained terminal is kept", worker: row("ctx_a", ownership("owned"), retained("user_requested")), idle: IdleQuiet, verb: verbKeep, target: "term_ctx_a", reason: "retained: user_requested"},
		{name: "a released terminal needs nothing", worker: row("ctx_a", state(stateReleased), ownership(stateReleased), retained("")), idle: IdleQuiet},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			found := action(t, c.worker, c.live, c.idle, c.ended)
			if c.verb == "" {
				if found != nil {
					t.Fatalf("got %+v, want nothing", *found)
				}
				return
			}
			if found == nil || found.Verb != c.verb || found.Target != c.target || found.Reason != c.reason {
				t.Fatalf("got %+v, want %s %s %s", found, c.verb, c.target, c.reason)
			}
		})
	}
}

func TestIdleScreens(t *testing.T) {
	replaced := make([]string, len(claudeDone))
	for i, line := range claudeDone {
		replaced[i] = strings.ReplaceAll(line, "❯", "❯ rebase onto dev")
	}
	cases := []struct {
		name string
		tail []string
		want bool
	}{
		{"a finished Claude at an empty prompt is idle", claudeDone, true},
		{"codex at its placeholder prompt is idle", []string{"Worked for 16m 40s • 1:24 PM", "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh · ~/w"}, true},
		{"a running turn is busy", append([]string{"✶ Baking… (esc to interrupt)"}, claudeDone[2:]...), false},
		{"typed prompt text is not idle", replaced, false},
		{"an unanswered question is not idle", append([]string{"  Should I rebase onto dev?"}, claudeDone[1:]...), false},
		{"a screen with no prompt is not idle", []string{"$ tail -f build.log", "line 1"}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := idleScreen(c.tail); got != c.want {
				t.Fatalf("got %v", got)
			}
		})
	}
}

func TestWorktreeActions(t *testing.T) {
	landedB := Remote{Heads: map[string]bool{}, Landed: landedSet{headB: true}}
	empty := Remote{Heads: map[string]bool{}, Landed: landedSet{}}
	onTrunk := checkout(testWT, withHead(headC))
	onTrunk.OnTrunk = true
	titled := func(title string, quiet int64) Terminal {
		return Terminal{Handle: "term_sh", Title: title, LastOutputMS: fakeNow - quiet}
	}
	cases := []struct {
		name      string
		checkout  Checkout
		terminals []Terminal
		remote    Remote
		verb      string
		reason    string
	}{
		{name: "a clean pushed tree with an idle shell is removed", checkout: checkout(testWT), terminals: []Terminal{idleShellTerminal("term_sh")}, remote: defaultRemote(), verb: verbRemove},
		{name: "an agent terminal without a dispatch keeps the tree", checkout: checkout(testWT), terminals: []Terminal{agentTerminal("term_cc")}, remote: defaultRemote(), verb: verbKeep, reason: "1 agent terminal(s) no settled dispatch owns"},
		{name: "a shell with recent output keeps the tree", checkout: checkout(testWT), terminals: []Terminal{titled("~/x/v3-lane-base", 1000)}, remote: defaultRemote(), verb: verbKeep, reason: "shell term_sh is in use: ~/x/v3-lane-base"},
		{name: "Orca's untitled startup shell is idle", checkout: checkout(testWT), terminals: []Terminal{titled("", 3_600_000)}, remote: defaultRemote(), verb: verbRemove},
		{name: "an untitled shell with recent output keeps the tree", checkout: checkout(testWT), terminals: []Terminal{titled("", 1000)}, remote: defaultRemote(), verb: verbKeep, reason: "shell term_sh is in use: "},
		{name: "a shell running a command keeps the tree", checkout: checkout(testWT), terminals: []Terminal{titled("tail -f log", 3_600_000)}, remote: defaultRemote(), verb: verbKeep, reason: "shell term_sh is in use: tail -f log"},
		{name: "a dirty tree is kept", checkout: checkout(testWT, dirtyTree), remote: defaultRemote(), verb: verbKeep, reason: "uncommitted changes"},
		{name: "a clean pushed tree is removed while its PR is open", checkout: checkout(testWT), remote: defaultRemote(), verb: verbRemove},
		{name: "an unpushed head keeps the tree", checkout: checkout(testWT, withHead(headB)), remote: defaultRemote(), verb: verbKeep, reason: "unpushed commits on v3/lane"},
		{name: "a landed branch whose remote head is deleted is removed", checkout: checkout(testWT, withHead(headB)), remote: landedB, verb: verbRemove},
		{name: "commits after a landed head on a reused branch keep the tree", checkout: checkout(testWT, withHead(headD)), remote: landedB, verb: verbKeep, reason: "unpushed commits on v3/lane"},
		{name: "a lane that never committed is removed", checkout: onTrunk, remote: empty, verb: verbRemove},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := worktreeAction(context.Background(), c.checkout, TerminalList{Terminals: c.terminals}, map[string]Action{}, c.remote, fakeNow)
			if err != nil {
				t.Fatal(err)
			}
			if got.Verb != c.verb || got.Reason != c.reason {
				t.Fatalf("got %+v, want %s %q", got, c.verb, c.reason)
			}
		})
	}
}

func TestLocalRemovalsRunOneAtATime(t *testing.T) {
	var running, most atomic.Int64
	orca := newOrca()
	git := newGit()
	for _, n := range []string{"0", "1", "2"} {
		tree := testWT + "-" + n
		orca.rows = append(orca.rows, row("ctx_"+n, in(tree)))
		found := checkout(tree)
		git.reads[tree] = CheckoutRead{Found: &found}
	}
	orca.onRemove = func(string) {
		now := running.Add(1)
		for {
			seen := most.Load()
			if now <= seen || most.CompareAndSwap(seen, now) {
				break
			}
		}
		time.Sleep(50 * time.Millisecond)
		running.Add(-1)
	}
	tally, _ := runGC(t, orca, git, gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 3, verbRemoved: 3})
	if most.Load() != 1 {
		t.Fatalf("%d removals ran at once", most.Load())
	}
}

func TestAFinishedLanesRemovalStopsItsIdleTerminal(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.terminals[testWT] = []Terminal{agentTerminal("term_ctx_a"), idleShellTerminal("term_sh")}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbRemoved: 1, stopping: 1})
	wantCalls(t, orca, []string{"remove", testWT})
	wantSaid(t, out, "removed "+testWT+" stopping term_ctx_a")
}

func TestATabCloseTimeoutNoLongerStrandsARemovableTree(t *testing.T) {
	orca := newOrca(row("ctx_a", status(statusFailed)))
	orca.closeRefusal = "terminal_tab_close_timeout"
	orca.terminals[testWT] = []Terminal{agentTerminal("term_ctx_a")}
	tally, _ := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbRemoved: 1, stopping: 1})
	wantCalls(t, orca, []string{"remove", testWT})
}

func TestAKeptTreesTerminalIsStillClosed(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.terminals[testWT] = []Terminal{agentTerminal("term_ctx_a")}
	tally, out := runGC(t, orca, newGit(checkout(testWT, dirtyTree)), gcOptions{})
	wantTally(t, tally, Tally{verbKeep: 1, verbClosed: 1})
	wantCalls(t, orca, []string{"close", "term_ctx_a"})
	wantSaid(t, out, "keep "+testWT+" uncommitted changes")
}

func TestARefusedRemovalFallsBackToClosingTheTerminal(t *testing.T) {
	tree := t.TempDir()
	orca := newOrca(row("ctx_a", in(tree)))
	orca.terminals[tree] = []Terminal{agentTerminal("term_ctx_a")}
	orca.refusal = "worktree_busy"
	tally, _ := runGC(t, orca, newGit(checkout(tree)), gcOptions{})
	wantTally(t, tally, Tally{verbKeep: 1, verbClosed: 1})
	wantCalls(t, orca, []string{"remove", tree}, []string{"close", "term_ctx_a"})
}

func TestARemovalWhoseReplyOrcaLostCountsAsRemovedAndNamesTheError(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.terminals[testWT] = []Terminal{idleShellTerminal("term_sh")}
	orca.refusal = "runtime_unavailable: The Orca runtime closed the connection before responding"
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbRemoved: 1})
	wantSaid(t, out, "removed "+testWT+"\nerror "+testWT+" orca worktree rm answered runtime_unavailable: The Orca runtime closed the connection before responding, but its directory is gone\n")
}

func TestATerminalThatWakesBeforeTheRemovalKeepsTheTree(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.idle["term_ctx_a"] = []Idleness{IdleQuiet, IdleBusy}
	orca.terminals[testWT] = []Terminal{agentTerminal("term_ctx_a")}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbKeep: 1})
	wantCalls(t, orca)
	wantSaid(t, out, "keep "+testWT+" "+changed)
}

func TestABusySettledTerminalKeepsTheTree(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.idle["term_ctx_a"] = []Idleness{IdleBusy}
	orca.terminals[testWT] = []Terminal{{Handle: "term_ctx_a", Agent: "claude", Title: "✳ lane", LastOutputMS: fakeNow}}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbKeep: 2})
	wantCalls(t, orca)
	wantSaid(t, out, "keep "+testWT+" 1 agent terminal(s) no settled dispatch owns")
}

func TestADryRunChangesNothing(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.terminals[testWT] = []Terminal{agentTerminal("term_ctx_a")}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{dryRun: true})
	wantTally(t, tally, Tally{verbRemove: 1, stopping: 1})
	wantCalls(t, orca)
	wantSaid(t, out, "would remove "+testWT+" stopping term_ctx_a")
}

func TestAWorktreeWithAnUnsettledDispatchIsNeverTouched(t *testing.T) {
	orca := newOrca(row("ctx_a"), row("ctx_b", status("dispatched")))
	tally, _ := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1})
	wantCalls(t, orca, []string{"close", "term_ctx_a"})
}

func TestADispatchFilterLimitsTheSweep(t *testing.T) {
	other := "/w/monorepo/v3-other-base"
	orca := newOrca(row("ctx_a"), row("ctx_b", in(other)))
	runGC(t, orca, newGit(checkout(testWT), checkout(other)), gcOptions{dispatches: []string{"ctx_a"}})
	wantCalls(t, orca, []string{"remove", testWT}, []string{"close", "term_ctx_a"})
}

func TestADoneLanesBusyTerminalIsClosedBeforeItsTreeIsRemoved(t *testing.T) {
	orca := newOrca(row("ctx_a", status("dispatched")))
	orca.idle["term_ctx_a"] = []Idleness{IdleBusy}
	orca.terminals[testWT] = []Terminal{{Handle: "term_ctx_a", Agent: "claude", Title: "✳ lane", LastOutputMS: fakeNow}, idleShellTerminal("term_sh")}
	orca.stops["ctx_a"] = fenceReply{state: "stop_unknown", detail: "none"}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbStopped: 1, verbClosed: 1, verbRemoved: 1})
	wantCalls(t, orca, []string{"stop", "ctx_a"}, []string{"close", "term_ctx_a"}, []string{"remove", testWT})
	wantSaid(t, out, "closed term_ctx_a dispatch ctx_a is done")
}

func TestADoneLaneOrcaAlreadyFencedIsAbandonedClosedAndRemoved(t *testing.T) {
	orca := newOrca(row("ctx_a", status("dispatched"), state("active"), ownedTerminal()))
	orca.idle["term_ctx_a"] = []Idleness{IdleBusy}
	orca.terminals[testWT] = []Terminal{{Handle: "term_ctx_a", Agent: "codex", LastOutputMS: fakeNow}}
	orca.stops["ctx_a"] = fenceReply{detail: CodeFenced}
	orca.abandons["ctx_a"] = fenceReply{state: "abandoned", detail: "none", after: func(w *Worker) {
		w.Status, w.Ownership, w.RetainedReason = statusFailed, "owned", "user_requested"
	}}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbStopped: 1, verbClosed: 1, verbRemoved: 1})
	wantCalls(t, orca, []string{"stop", "ctx_a"}, []string{"abandon", "ctx_a"}, []string{"close", "term_ctx_a"}, []string{"remove", testWT})
	wantSaid(t, out, "stopped ctx_a abandoned")
}

func TestADoneLaneWithUnpushedWorkClosesItsBusyTerminal(t *testing.T) {
	orca := newOrca(row("ctx_a", status("dispatched")))
	orca.idle["term_ctx_a"] = []Idleness{IdleBusy}
	orca.terminals[testWT] = []Terminal{{Handle: "term_ctx_a", Agent: "claude", Title: "✳ lane", LastOutputMS: fakeNow}}
	orca.stops["ctx_a"] = fenceReply{state: "stop_unknown", detail: "none"}
	tally, out := runGC(t, orca, newGit(checkout(testWT, withHead(headD))), gcOptions{done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbStopped: 1, verbKeep: 1, verbClosed: 1})
	wantCalls(t, orca, []string{"stop", "ctx_a"}, []string{"close", "term_ctx_a"})
	wantSaid(t, out, "closed term_ctx_a dispatch ctx_a is done")
}

func TestADoneLaneWhoseTerminalWorkerStopClosedIsNotClosedAgain(t *testing.T) {
	orca := newOrca(row("ctx_a", status("dispatched"), ownedTerminal()))
	orca.stops["ctx_a"] = fenceReply{state: verbStopped, detail: closedByStop, after: func(w *Worker) { w.Status = statusFailed }}
	tally, _ := runGC(t, orca, newGit(checkout(testWT)), gcOptions{done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbStopped: 1, verbRemoved: 1})
	wantCalls(t, orca, []string{"stop", "ctx_a"}, []string{"remove", testWT})
}

func TestADoneLaneThatAlreadySettledIsReleased(t *testing.T) {
	orca := newOrca(row("ctx_a", ownedTerminal()))
	orca.stops["ctx_a"] = fenceReply{state: "succeeded", detail: "none"}
	tally, _ := runGC(t, orca, newGit(), gcOptions{done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbStopped: 1, "released": 1})
	wantCalls(t, orca, []string{"stop", "ctx_a"}, []string{"release", "ctx_a"})
}

func TestARefusedStopLeavesTheLiveLaneAlone(t *testing.T) {
	orca := newOrca(row("ctx_a", status("dispatched")))
	orca.stops["ctx_a"] = fenceReply{detail: "worker_identity_changed"}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbKeep: 1})
	wantCalls(t, orca, []string{"stop", "ctx_a"})
	wantSaid(t, out, "keep ctx_a worker-stop worker_identity_changed")
}

func TestARefusedAbandonLeavesTheStoppingLaneAlone(t *testing.T) {
	orca := newOrca(row("ctx_a", status("dispatched")))
	orca.stops["ctx_a"] = fenceReply{detail: CodeFenced}
	orca.abandons["ctx_a"] = fenceReply{detail: CodeFenced}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbKeep: 1})
	wantCalls(t, orca, []string{"stop", "ctx_a"}, []string{"abandon", "ctx_a"})
	wantSaid(t, out, "keep ctx_a worker-abandon "+CodeFenced)
}

func TestADoneDryRunChangesNothing(t *testing.T) {
	orca := newOrca(row("ctx_a", status("dispatched")))
	orca.idle["term_ctx_a"] = []Idleness{IdleBusy}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{dryRun: true, done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbStop: 1, verbEnd: 1, verbRemove: 1})
	wantCalls(t, orca)
	wantSaid(t, out, "would stop ctx_a", "would end term_ctx_a")
}

func TestDoneScopesTheSweepToItsLane(t *testing.T) {
	other := "/w/monorepo/v3-other-base"
	orca := newOrca(row("ctx_a", status("dispatched")), row("ctx_b", in(other)))
	orca.stops["ctx_a"] = fenceReply{state: "stop_unknown", detail: "none"}
	runGC(t, orca, newGit(checkout(testWT), checkout(other)), gcOptions{done: []string{"ctx_a"}})
	wantCalls(t, orca, []string{"stop", "ctx_a"}, []string{"close", "term_ctx_a"}, []string{"remove", testWT})
}

func TestATerminalThatWakesBeforeTheCloseIsKept(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.idle["term_ctx_a"] = []Idleness{IdleQuiet, IdleBusy}
	tally, out := runGC(t, orca, newGit(), gcOptions{})
	wantTally(t, tally, Tally{verbKeep: 1})
	wantCalls(t, orca)
	wantSaid(t, out, changed)
}

func TestAnOrcaRefusalKeepsTheTreeAndSaysWhy(t *testing.T) {
	tree := t.TempDir()
	orca := newOrca(row("ctx_a", in(tree)))
	orca.refusal = "worktree_dirty: uncommitted changes"
	tally, out := runGC(t, orca, newGit(checkout(tree)), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbKeep: 1})
	wantSaid(t, out, "keep "+tree+" worktree_dirty")
}

func TestATerminalThatOpensBeforeTheRemovalKeepsTheTree(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.listings = []TerminalList{
		{Terminals: []Terminal{idleShellTerminal("term_sh")}},
		{Terminals: []Terminal{idleShellTerminal("term_sh"), {Handle: "term_new", Agent: "claude", Title: "✳ new", LastOutputMS: fakeNow}}},
	}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbKeep: 1})
	wantSaid(t, out, "keep "+testWT+" "+changed)
	if orca.count("remove", testWT) != 0 {
		t.Fatal("removed a tree with a new terminal")
	}
}

func TestADisconnectedHandleIsAlreadyClosed(t *testing.T) {
	orca := newOrca(row("ctx_a", terminal("term_old")))
	orca.terminals[testWT] = []Terminal{idleShellTerminal("term_sh")}
	orca.disconnected["term_old"] = true
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbRemoved: 1})
	wantCalls(t, orca, []string{"remove", testWT})
	if strings.Contains(out, "term_old") {
		t.Fatalf("output %q names the old handle", out)
	}
}

func TestADoneLanesDisconnectedHandleGetsNoClose(t *testing.T) {
	orca := newOrca(row("ctx_a", status("dispatched"), terminal("term_old")))
	orca.stops["ctx_a"] = fenceReply{state: "stop_unknown", detail: "none"}
	orca.disconnected["term_old"] = true
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{done: []string{"ctx_a"}})
	wantTally(t, tally, Tally{verbStopped: 1, verbRemoved: 1})
	wantCalls(t, orca, []string{"stop", "ctx_a"}, []string{"remove", testWT})
	if strings.Contains(out, "term_old") {
		t.Fatalf("output %q names the old handle", out)
	}
}

func TestAWorktreeWithOneUnrecordedConnectedTerminalStaysKept(t *testing.T) {
	orca := newOrca(row("ctx_a", terminal("term_old")))
	orca.terminals[testWT] = []Terminal{{Handle: "term_x", Agent: "claude", Title: "✳ x", Tab: "tab_2", Leaf: "leaf_2"}}
	orca.stale["term_old"] = true
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbKeep: 2})
	wantCalls(t, orca)
	wantSaid(t, out, "keep term_old "+staleHandle, "keep "+testWT+" 1 agent terminal(s) no settled dispatch owns")
}

func TestATreeALiveProcessHoldsIsKept(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.holders[testWT] = "live process script /s/runner.zsh names it"
	_, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	if orca.count("remove", testWT) != 0 {
		t.Fatal("removed a held tree")
	}
	wantSaid(t, out, "keep "+testWT+" live process script /s/runner.zsh names it")
}

func TestARemovalALiveProcessRefusesLeavesItsAgentTerminalOpen(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.terminals[testWT] = []Terminal{agentTerminal("term_ctx_a")}
	orca.holders[testWT] = heldOpenInside
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbKeep: 1})
	wantCalls(t, orca)
	if strings.Contains(out, "closed term_ctx_a") {
		t.Fatalf("output %q closed the terminal", out)
	}
}

func TestAnApplyWhosePlanMovedIsRefusedWithTheDifference(t *testing.T) {
	planned := "would remove /w/a stopping term_a\nwould close term_b dispatch ctx_b\nkeep /w/c unpushed commits on c\nclose=1 remove=1 stopping=1\n"
	replanned := "would remove /w/a stopping term_a\nwould remove /w/d stopping term_d\nkeep /w/c unpushed commits on c\nremove=2 stopping=2\n"
	want := []string{"- would close term_b dispatch ctx_b", "+ would remove /w/d stopping term_d"}
	if got := Drift(planned, replanned); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v", got)
	}
	if got := Drift(planned, planned); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
}

func TestARemovalOrcaDropsTheConnectionOnKeepsATreeStillOnDiskAndNamesIt(t *testing.T) {
	tree := t.TempDir()
	orca := newOrca(row("ctx_a", in(tree)))
	orca.refusal = "runtime_unavailable: The Orca runtime closed the connection before responding"
	tally, out := runGC(t, orca, newGit(checkout(tree)), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbKeep: 1})
	wantSaid(t, out, "keep "+tree+" runtime_unavailable: The Orca runtime closed the connection before responding\n")
	if orca.count("remove", tree) != 1 {
		t.Fatalf("calls %v", orca.calls)
	}
}

func TestATreeOrcaNoLongerTracksIsRemovedWithGit(t *testing.T) {
	tree := t.TempDir()
	orca := newOrca(row("ctx_a", in(tree)))
	orca.refusal = "selector_not_found: selector_not_found"
	git := newGit(checkout(tree))
	tally, out := runGC(t, orca, git, gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbRemoved: 1})
	if !slices.Equal(git.removed, []string{tree}) {
		t.Fatalf("git removed %v", git.removed)
	}
	wantSaid(t, out, "removed "+tree+" Orca no longer tracks it, so git worktree remove removed it\n")
}

func TestATreeOrcaNoLongerTracksIsKeptWhenGitRefuses(t *testing.T) {
	tree := t.TempDir()
	orca := newOrca(row("ctx_a", in(tree)))
	orca.refusal = "selector_not_found: selector_not_found"
	git := newGit(checkout(tree))
	git.refusal = "fatal: cannot remove a locked working tree"
	tally, out := runGC(t, orca, git, gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbKeep: 1})
	wantSaid(t, out, "keep "+tree+" Orca no longer tracks it and git worktree remove refused: fatal: cannot remove a locked working tree\n")
}

func TestAnUntrackedTreeALiveProcessHoldsIsNeverHandedToGit(t *testing.T) {
	tree := t.TempDir()
	orca := newOrca(row("ctx_a", in(tree)))
	orca.refusal = "selector_not_found: selector_not_found"
	orca.holders[tree] = heldOpenInside
	git := newGit(checkout(tree))
	tally, _ := runGC(t, orca, git, gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbKeep: 1})
	if len(git.removed) != 0 || orca.count("remove", tree) != 0 {
		t.Fatalf("git %v, orca %v", git.removed, orca.calls)
	}
}

func TestADispatchThatFailedBeforeItsWorktreeResolvedIsKeptAndGCContinues(t *testing.T) {
	unplaced := Worker{Dispatch: "ctx_a", Status: statusFailed, Environment: "env_box"}
	orca := newOrca(unplaced, row("ctx_b"))
	orca.terminals[testWT] = []Terminal{agentTerminal("term_ctx_b"), idleShellTerminal("term_sh")}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbKeep: 1, verbRemoved: 1, stopping: 1})
	wantCalls(t, orca, []string{"remove", testWT})
	wantSaid(t, out, "keep ctx_a Orca records no workspace for it")
}

func TestAMissingWorktreeIsSkipped(t *testing.T) {
	tally, _ := runGC(t, newOrca(row("ctx_a")), newGit(), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1})
}

func TestAMissingWorktreeOrcaStillRegistersIsDropped(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.known[testWT] = true
	tally, out := runGC(t, orca, newGit(), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbDropped: 1})
	wantCalls(t, orca, []string{"remove", testWT}, []string{"close", "term_ctx_a"})
	wantSaid(t, out, "dropped "+testWT+" its directory is gone")
}

func TestADropOrcaAlreadyForgotReadsAsDropped(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.refusal = "selector_not_found: selector_not_found"
	orca.known[testWT] = true
	tally, _ := runGC(t, orca, newGit(), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbDropped: 1})
}

func TestADryRunNamesTheDrop(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.known[testWT] = true
	tally, out := runGC(t, orca, newGit(), gcOptions{dryRun: true})
	wantTally(t, tally, Tally{verbClose: 1, verbDrop: 1})
	wantCalls(t, orca)
	wantSaid(t, out, "would drop "+testWT+" its directory is gone")
}

func TestATreeGitCannotReadIsKeptAndGCContinues(t *testing.T) {
	other := "/w/monorepo/v3-other-base"
	orca := newOrca(row("ctx_a"), row("ctx_b", in(other)))
	git := newGit(checkout(other))
	git.reads[testWT] = CheckoutRead{Refusal: "git cannot read it: fatal: gitfile does not point to a valid repository"}
	tally, out := runGC(t, orca, git, gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 2, verbKeep: 1, verbRemoved: 1})
	wantSaid(t, out, "keep "+testWT+" git cannot read it: fatal: gitfile does not point to a valid repository")
}

func TestAWorktreeWhoseTerminalsOrcaCannotListIsKept(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.listings = []TerminalList{{Refusal: "orca terminal list selector_not_found"}}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbKeep: 1})
	wantSaid(t, out, "keep "+testWT+" orca terminal list selector_not_found")
}

func TestATerminalListRefusalBeforeTheRemovalKeepsTheTree(t *testing.T) {
	orca := newOrca(row("ctx_a"))
	orca.listings = []TerminalList{{Terminals: []Terminal{idleShellTerminal("term_sh")}}, {Refusal: "orca terminal list selector_not_found"}}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
	wantTally(t, tally, Tally{verbClosed: 1, verbKeep: 1})
	wantSaid(t, out, "keep "+testWT+" orca terminal list selector_not_found")
}

func TestAPauseSetBeforeTheRunChangesNothing(t *testing.T) {
	pause := Pause{Resumed: filepath.Join(t.TempDir(), "absent")}
	orca := newOrca(row("ctx_a", status("dispatched")))
	orca.stops["ctx_a"] = fenceReply{state: "stop_unknown", detail: "none"}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{done: []string{"ctx_a"}, pause: &pause})
	wantTally(t, tally, Tally{verbSkip: 1})
	wantCalls(t, orca)
	if want := pause.Skip("run_x", "gc").Line() + "\n"; out != want {
		t.Fatalf("got %q, want %q", out, want)
	}
}

func TestADryRunReadsThroughAPause(t *testing.T) {
	pause := Pause{Resumed: filepath.Join(t.TempDir(), "absent")}
	orca := newOrca(row("ctx_a", state(stateReleased)))
	orca.terminals[testWT] = []Terminal{idleShellTerminal("term_sh")}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{dryRun: true, pause: &pause})
	wantTally(t, tally, Tally{verbRemove: 1})
	wantCalls(t, orca)
	wantSaid(t, out, "would remove "+testWT)
}

func TestAPauseSetBetweenCandidatesSkipsTheRest(t *testing.T) {
	pause := unpaused(t)
	removed := make(chan struct{})
	orca := newOrca(row("ctx_a", state(stateReleased)), row("ctx_b", state(stateReleased), in(testWT2)))
	orca.beforeListing = func(worktree string) {
		if worktree == testWT2 {
			select {
			case <-removed:
			case <-time.After(5 * time.Second):
			}
		}
	}
	orca.onRemove = func(string) {
		if err := os.Remove(pause.Resumed); err != nil {
			t.Error(err)
		}
		close(removed)
	}
	tally, out := runGC(t, orca, newGit(checkout(testWT), checkout(testWT2)), gcOptions{pause: &pause})
	wantTally(t, tally, Tally{verbRemoved: 1, verbSkip: 1})
	wantCalls(t, orca, []string{"remove", testWT})
	wantSaid(t, out, pause.Skip(testWT2, verbRemove).Line())
}

func TestAPauseSetAfterACandidateIsChosenSkipsItsRemoval(t *testing.T) {
	pause := unpaused(t)
	orca := newOrca(row("ctx_a", state(stateReleased)))
	orca.terminals[testWT] = []Terminal{idleShellTerminal("term_sh")}
	orca.onHolder = func() {
		if err := os.Remove(pause.Resumed); err != nil {
			t.Error(err)
		}
	}
	tally, out := runGC(t, orca, newGit(checkout(testWT)), gcOptions{pause: &pause})
	wantTally(t, tally, Tally{verbSkip: 1})
	wantCalls(t, orca)
	if panes, seen := orca.panes[testWT]; !seen || len(panes) != 0 {
		t.Fatalf("panes %v", orca.panes)
	}
	wantSaid(t, out, pause.Skip(testWT, verbRemove).Line())
}

func TestAPauseSetDuringAStopLetsItFinishAndSkipsTheRest(t *testing.T) {
	pause := unpaused(t)
	orca := newOrca(row("ctx_a", status("dispatched")), row("ctx_b", status("dispatched"), in(testWT2)))
	orca.stops["ctx_a"] = fenceReply{state: "stop_unknown", detail: "none"}
	orca.stops["ctx_b"] = fenceReply{state: "stop_unknown", detail: "none"}
	orca.onStop = func() {
		if err := os.Remove(pause.Resumed); err != nil {
			t.Error(err)
		}
	}
	tally, out := runGC(t, orca, newGit(checkout(testWT), checkout(testWT2)), gcOptions{done: []string{"ctx_a", "ctx_b"}, pause: &pause})
	wantTally(t, tally, Tally{verbStopped: 1, verbSkip: 3})
	wantCalls(t, orca, []string{"stop", "ctx_a"})
	wantSaid(t, out, pause.Skip("ctx_b", verbStop).Line(), pause.Skip("term_ctx_a", verbEnd).Line(), pause.Skip(testWT, verbRemove).Line())
}

const holderTree = "/w/monorepo/v3-idle-base"

func holderRunner(replies map[string]string, fallback string) *scripted {
	return &scripted{reply: func(argv []string) (string, string, int) {
		if said, ok := replies[joined(argv)]; ok {
			return said, "", 0
		}
		return fallback, "", 0
	}}
}

func holderOf(t *testing.T, ps, hooks, env string, panes map[string]bool) string {
	t.Helper()
	runner := holderRunner(map[string]string{
		"lsof -n -P -Fpn":                          "p84516\nfcwd\nn" + holderTree + "\np1\nftxt\nn/usr/lib/dyld\n",
		"ps -axo pid=,ppid=,pgid=,tpgid=,comm=":    ps,
		"ps -axo pid=,ppid=,command=":              hooks,
		"ps -axo pid=,ppid=,sess=":                 "84516 84510 7\n",
		"ps -E -ww -o pid=,tty=,command= -p 84516": env,
		"lsof -a -d cwd -Fn":                       "p84516\nfcwd\nn" + holderTree + "\n",
	}, "-/opt/homebrew/bin/fish\n")
	held, err := Processes{Runner: runner, Home: "/u"}.Holder(context.Background(), holderTree, panes)
	if err != nil {
		t.Fatal(err)
	}
	return held
}

func TestHolders(t *testing.T) {
	pane := map[string]bool{"tab_1:leaf_1": true}
	cases := []struct {
		name           string
		ps, hooks, env string
		panes          map[string]bool
		want           string
	}{
		{name: "an idle login shell at its prompt does not hold the tree", ps: "    1     0     1     0 launchd\n84516 84510 84516 84516 -/opt/homebrew/bin/fish\n"},
		{name: "a login shell running an agent holds the tree", ps: "84516 84510 84516 84516 -/opt/homebrew/bin/fish\n84861 84516 84861 84516 claude\n", want: heldOpenInside},
		{name: "a login shell running a foreground command holds the tree", ps: "84516 84510 84516 90000 -/bin/zsh\n", want: heldOpenInside},
		{name: "a shell that is not a login shell holds the tree", ps: "84516 84510 84516 84516 zsh\n", want: heldOpenInside},
		{name: "a capt-hookd worker parked in the tree does not hold it", ps: "84516   500 84516     0 /h/capt-hook/bin/python\n", hooks: "  500     1 /Applications/Captain Hook.app/Contents/Helpers/capt-hookd serve\n84516   500 /h/capt-hook/bin/python -P -m captain_hook.worker\n"},
		{name: "a python worker no capt-hookd spawned holds the tree", ps: "84516   777 84516     0 /h/capt-hook/bin/python\n", hooks: "84516   777 /h/capt-hook/bin/python -P -m captain_hook.worker\n", want: heldOpenInside},
		{name: "the agent of a terminal the removal stops does not hold the tree", ps: "84516 84510 84516 84516 claude\n", env: "84516 ttys064  claude ORCA_TERMINAL_HANDLE=term_new ORCA_PANE_KEY=tab_1:leaf_1 HOME=/u\n", panes: pane},
		{name: "a process detached from a stopped terminal holds the tree", ps: "84516     1 84516     0 node\n", env: "84516 ??       node server.js ORCA_PANE_KEY=tab_1:leaf_1\n", panes: pane, want: heldOpenInside},
		{name: "a process of a terminal the removal keeps holds the tree", ps: "84516 84510 84516 84516 claude\n", env: "84516 ttys070  claude ORCA_PANE_KEY=tab_2:leaf_2\n", panes: pane, want: heldOpenInside},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := holderOf(t, c.ps, c.hooks, c.env, c.panes); got != c.want {
				t.Fatalf("got %q, want %q", got, c.want)
			}
		})
	}
}

func TestAChildInTheSessionOfAStoppedAgentDoesNotHoldTheTreeAndOneThatLeftDoes(t *testing.T) {
	holder := func(session string) string {
		runner := holderRunner(map[string]string{
			"lsof -n -P -Fpn":                                "p84516\nfcwd\nn" + holderTree + "\np90001\nfcwd\nn" + holderTree + "/web\n",
			"ps -axo pid=,ppid=,pgid=,tpgid=,comm=":          "84516 84510 84516 84516 claude\n90001 84516 84516 84516 node\n",
			"ps -E -ww -o pid=,tty=,command= -p 84516,90001": "84516 ttys064  claude ORCA_PANE_KEY=tab_1:leaf_1\n90001 ??       node mcp.js\n",
			"ps -axo pid=,ppid=,sess=":                       "84516 84510 7\n90001 84516 " + session + "\n",
		}, "")
		held, err := Processes{Runner: runner, Home: "/u"}.Holder(context.Background(), holderTree, map[string]bool{"tab_1:leaf_1": true})
		if err != nil {
			t.Fatal(err)
		}
		return held
	}
	if got := holder("7"); got != "" {
		t.Fatalf("same session: got %q", got)
	}
	if got := holder("9"); got != heldOpenInside {
		t.Fatalf("left session: got %q", got)
	}
}

func TestALiveRunnerScriptNamingTheTreeHoldsIt(t *testing.T) {
	root := t.TempDir()
	tree, desk := filepath.Join(root, "stack-enqueue-preflight-speed"), filepath.Join(root, "desk")
	for _, dir := range []string{tree, desk} {
		if err := os.Mkdir(dir, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(desk, "runner.zsh"), []byte("cd stack-enqueue-preflight-speed && stack-enqueue\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	runner := holderRunner(map[string]string{"lsof -n -P -Fpn": "", "lsof -a -d cwd -Fn": "p1\nfcwd\nn" + desk + "\n"}, "zsh ./runner.zsh\n")
	got, err := Processes{Runner: runner, Home: "/u"}.Holder(context.Background(), tree, nil)
	if err != nil {
		t.Fatal(err)
	}
	if want := "live process script " + filepath.Join(desk, "runner.zsh") + " names it"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestACwdUnderLibraryOrThePerUserTempTreeIsNeverOpened(t *testing.T) {
	listing := "p1\nfcwd\nn/u/Library/Containers/com.apple.mail/Data\np2\nfcwd\nn/private/var/folders/ab/cd/T/session\np3\nfcwd\nn/d/desk\n"
	got := Processes{Home: "/u"}.scannedCwds(listing, holderTree)
	if !slices.Equal(got, []string{"/d/desk"}) {
		t.Fatalf("got %v", got)
	}
}

func TestOnlyAFileUnderTheTreesResolvedPathHoldsIt(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	tree := filepath.Join(root, "v3-idle-base")
	if err := os.Mkdir(tree, 0o750); err != nil {
		t.Fatal(err)
	}
	listing := "p7\nf3\nn" + tree + "/go.mod\np8\nf4\nn" + root + "/v3-idle-base-2/go.mod\n"
	runner := holderRunner(map[string]string{"lsof -n -P -Fpn": listing}, "")
	got, err := Processes{Runner: runner}.openInside(context.Background(), tree)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, map[string]bool{"7": true}) {
		t.Fatalf("got %v", got)
	}
}

func TestPairedServers(t *testing.T) {
	boxAgent := Terminal{Handle: "term_box", Agent: "claude", Title: "✳ lane"}
	paired := func(found Checkout) CheckoutRead { return CheckoutRead{Found: &found} }
	t.Run("a settled box lane's clean pushed tree is removed through its server", func(t *testing.T) {
		orca := newOrca(boxRow("ctx_a"))
		orca.terminals[boxWT] = []Terminal{boxAgent}
		orca.paired[boxWT] = paired(checkout(boxWT, onEnv(boxEnv)))
		orca.holders[boxWT] = "a Mac process scan must not apply"
		tally, out := runGC(t, orca, newGit(), gcOptions{})
		wantTally(t, tally, Tally{verbRemoved: 1, stopping: 1})
		wantCalls(t, orca, []string{"remove", boxWT, boxEnv})
		wantSaid(t, out, "removed "+boxWT+" stopping term_box")
	})
	t.Run("a box tree with unpushed commits is kept and its idle terminal closed", func(t *testing.T) {
		orca := newOrca(boxRow("ctx_a"))
		orca.terminals[boxWT] = []Terminal{boxAgent}
		orca.paired[boxWT] = paired(checkout(boxWT, withHead(headD), onEnv(boxEnv)))
		tally, out := runGC(t, orca, newGit(), gcOptions{})
		wantTally(t, tally, Tally{verbKeep: 1, verbClosed: 1})
		wantCalls(t, orca, []string{"close", "term_box", boxEnv})
		wantSaid(t, out, "keep "+boxWT+" unpushed commits on v3/lane")
	})
	t.Run("a box terminal a live dispatch reuses keeps both", func(t *testing.T) {
		orca := newOrca(boxRow("ctx_a"), boxRow("ctx_b", status("dispatched")))
		orca.paired[boxWT] = paired(checkout(boxWT, onEnv(boxEnv)))
		tally, out := runGC(t, orca, newGit(), gcOptions{})
		wantTally(t, tally, Tally{verbKeep: 1})
		wantCalls(t, orca)
		wantSaid(t, out, "keep term_box serves an unsettled dispatch")
	})
	t.Run("a box path never matches a live Mac tree of the same path", func(t *testing.T) {
		orca := newOrca(boxRow("ctx_a", in(testWT)), row("ctx_b", status("dispatched"), terminal("term_mac")))
		orca.terminals[testWT] = []Terminal{boxAgent}
		orca.paired[testWT] = paired(checkout(testWT, onEnv(boxEnv)))
		tally, _ := runGC(t, orca, newGit(checkout(testWT)), gcOptions{})
		wantTally(t, tally, Tally{verbRemoved: 1, stopping: 1})
		wantCalls(t, orca, []string{"remove", testWT, boxEnv})
	})
}

func hostedFleet() *fakeFleet {
	return &fakeFleet{hosted: map[SpriteKey]Sprite{{spriteWT, "term_sprite"}: testSprite}, reads: map[string]CheckoutRead{}}
}

func TestSpriteVerdicts(t *testing.T) {
	refused := func(reason string) CheckoutRead { return CheckoutRead{Refusal: reason} }
	dirty := checkout(testSprite.Root, dirtyTree)
	unpushed := checkout(testSprite.Root, withHead(headB))
	cases := []struct {
		name string
		rows []Worker
		idle []Idleness
		read *CheckoutRead
		want string
	}{
		{name: "uncommitted work keeps the Sprite", read: &CheckoutRead{Found: &dirty}, want: "keep pool-abc uncommitted changes"},
		{name: "an unpushed head keeps the Sprite", read: &CheckoutRead{Found: &unpushed}, want: "keep pool-abc unpushed commits on v3/lane"},
		{name: "an unreadable checkout keeps the Sprite", read: new(refused("ssh to pool-abc could not read /home/sprite/monorepo: timeout")), want: "keep pool-abc ssh to pool-abc could not read"},
		{name: "a busy terminal keeps the Sprite", idle: []Idleness{IdleBusy}, want: "keep pool-abc terminal term_sprite is " + notIdle},
		{name: "an unreadable terminal keeps the Sprite", idle: []Idleness{IdleUnknown}, want: "keep pool-abc terminal term_sprite is unreadable or disconnected, so its idleness is unknown"},
		{name: "a terminal that wakes before the destroy keeps the Sprite", idle: []Idleness{IdleQuiet, IdleBusy}, want: "keep pool-abc " + changed},
		{name: "an unsettled dispatch on the same Sprite keeps it", rows: []Worker{spriteRow("ctx_t", status("dispatched"))}, want: "keep pool-abc serves an unsettled dispatch"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			orca := newOrca(append([]Worker{spriteRow("ctx_s")}, c.rows...)...)
			if c.idle != nil {
				orca.idle["term_sprite"] = c.idle
			}
			fleet := hostedFleet()
			if c.read != nil {
				fleet.reads["pool-abc"] = *c.read
			}
			_, out := runGC(t, orca, newGit(), gcOptions{fleet: fleet})
			if len(fleet.destroyed) != 0 {
				t.Fatalf("destroyed %v", fleet.destroyed)
			}
			wantSaid(t, out, c.want)
		})
	}
}

func TestASettledLanesCleanLandedSpriteIsDestroyedWithItsEnvironment(t *testing.T) {
	orca, fleet := newOrca(spriteRow("ctx_s")), hostedFleet()
	tally, out := runGC(t, orca, newGit(), gcOptions{fleet: fleet})
	wantTally(t, tally, Tally{verbGone: 1})
	wantCalls(t, orca, []string{"forget", "env_abc"})
	if !slices.Equal(fleet.destroyed, []string{"pool-abc"}) {
		t.Fatalf("destroyed %v", fleet.destroyed)
	}
	wantSaid(t, out, "destroyed pool-abc dispatch ctx_s")
}

func TestASpriteNoCCRemoteTaskTiesToTheDispatchIsNeverTouched(t *testing.T) {
	fleet := &fakeFleet{hosted: map[SpriteKey]Sprite{{spriteWT, "term_other"}: testSprite}}
	tally, _ := runGC(t, newOrca(spriteRow("ctx_s")), newGit(), gcOptions{fleet: fleet})
	if len(fleet.destroyed) != 0 || tally[verbGone] != 0 {
		t.Fatalf("destroyed %v, tally %v", fleet.destroyed, tally)
	}
}

func TestAnEarlierDispatchOnALiveSpriteNeverReachesWorktreeRmOnItsCheckout(t *testing.T) {
	remote := spriteRow("ctx_s", onHost(testSprite.Environment))
	orca, fleet := newOrca(remote), &fakeFleet{hosted: map[SpriteKey]Sprite{{spriteWT, "term_other"}: testSprite}}
	found := checkout(testSprite.Root, onEnv(testSprite.Environment))
	orca.paired[testSprite.Root] = CheckoutRead{Found: &found}
	_, out := runGC(t, orca, newGit(), gcOptions{fleet: fleet})
	wantCalls(t, orca, []string{"close", "term_sprite", testSprite.Environment})
	if len(fleet.destroyed) != 0 || strings.Contains(out, testSprite.Root) {
		t.Fatalf("destroyed %v, output %q", fleet.destroyed, out)
	}
}

func TestADoneDestroysTheSpriteWithoutReadingItsTerminal(t *testing.T) {
	orca := newOrca(spriteRow("ctx_s", status("dispatched")))
	orca.idle["term_sprite"] = []Idleness{IdleUnknown}
	orca.stops["ctx_s"] = fenceReply{state: verbStopped, detail: "none", after: func(w *Worker) { w.Status = statusFailed }}
	fleet := hostedFleet()
	tally, _ := runGC(t, orca, newGit(), gcOptions{fleet: fleet, done: []string{"ctx_s"}})
	if !slices.Equal(fleet.destroyed, []string{"pool-abc"}) || tally[verbGone] != 1 {
		t.Fatalf("destroyed %v, tally %v", fleet.destroyed, tally)
	}
}

func TestARefusedDestroyKeepsTheEnvironment(t *testing.T) {
	orca, fleet := newOrca(spriteRow("ctx_s")), hostedFleet()
	fleet.refusal = "machine lacks the workspace label"
	_, out := runGC(t, orca, newGit(), gcOptions{fleet: fleet})
	wantCalls(t, orca)
	wantSaid(t, out, "keep pool-abc machine lacks the workspace label")
}

func TestASpriteDryRunDestroysNothing(t *testing.T) {
	orca, fleet := newOrca(spriteRow("ctx_s")), hostedFleet()
	_, out := runGC(t, orca, newGit(), gcOptions{dryRun: true, fleet: fleet})
	wantCalls(t, orca)
	if len(fleet.destroyed) != 0 {
		t.Fatalf("destroyed %v", fleet.destroyed)
	}
	wantSaid(t, out, "would destroy pool-abc dispatch ctx_s")
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestTaskRecordsTieARecordedSpriteWorkspaceToItsOrcaWorktreeAndTerminal(t *testing.T) {
	state := t.TempDir()
	for _, dir := range []string{"orca", "workspaces"} {
		if err := os.Mkdir(filepath.Join(state, dir), 0o750); err != nil {
			t.Fatal(err)
		}
	}
	task := func(name string, overrides map[string]any) {
		record := map[string]any{
			"schemaVersion": 1, "workspace": name, "provider": "sprites", "projectRoot": "/home/sprite/monorepo",
			"environmentId": "env_" + name, "worktreeId": "repo_" + name + "::/home/sprite/monorepo", "terminal": "term_" + name,
			"forward": map[string]string{"host": name, "config": "/s/ssh/" + name + ".ssh"},
		}
		for key, value := range overrides {
			if value == nil {
				delete(record, key)
			} else {
				record[key] = value
			}
		}
		writeJSON(t, filepath.Join(state, "orca", name+".json"), record)
	}
	task("live", nil)
	writeJSON(t, filepath.Join(state, "workspaces", "live.json"), map[string]any{})
	task("destroyed", nil)
	task("namespace", map[string]any{"provider": "namespace"})
	writeJSON(t, filepath.Join(state, "workspaces", "namespace.json"), map[string]any{})
	task("unattached", map[string]any{"worktreeId": nil, "terminal": nil})
	writeJSON(t, filepath.Join(state, "workspaces", "unattached.json"), map[string]any{})
	if err := os.WriteFile(filepath.Join(state, "orca", "live.json.lock"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := CCRemote{State: state}.Sprites()
	if err != nil {
		t.Fatal(err)
	}
	want := map[SpriteKey]Sprite{{"repo_live::/home/sprite/monorepo", "term_live"}: {"live", "live", "/s/ssh/live.ssh", "/home/sprite/monorepo", "env_live"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v", got)
	}
}

func realGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", slices.Concat([]string{"-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"}, args)...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v: %s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func TestAMissingDirectoryIsAlreadyRemoved(t *testing.T) {
	read, err := (&Git{Runner: shell.Exec{}}).Checkout(context.Background(), filepath.Join(t.TempDir(), "gone"))
	if err != nil || read.Found != nil || read.Refusal != "" {
		t.Fatalf("read %+v, err %v", read, err)
	}
}

func TestADirectoryWhoseRepositoryIsGoneNamesWhyGitCannotReadIt(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, ".git"), []byte("gitdir: "+root+"/reaped/.git/worktrees/lane\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	read, err := (&Git{Runner: shell.Exec{}}).Checkout(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	if !regexp.MustCompile(`^git cannot read it: fatal: `).MatchString(read.Refusal) {
		t.Fatalf("refusal %q", read.Refusal)
	}
}

func TestGCReclaimsTheRestOfTheRunPastACheckoutGitCannotRead(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, ".git"), []byte("gitdir: "+root+"/reaped/.git/worktrees/lane\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	orca := newOrca(row("ctx_gone", in(root)), row("ctx_a"))
	orca.terminals[testWT] = []Terminal{idleShellTerminal("term_s")}
	git := newGit(checkout(testWT))
	git.delegate = func(ctx context.Context, path string) (CheckoutRead, bool, error) {
		if path != root {
			return CheckoutRead{}, false, nil
		}
		read, err := (&Git{Runner: shell.Exec{}}).Checkout(ctx, path)
		return read, true, err
	}
	tally, out := runGC(t, orca, git, gcOptions{})
	if tally[verbRemoved] != 1 {
		t.Fatalf("tally %v", tally)
	}
	wantSaid(t, out, "keep "+root+" git cannot read it: fatal: ")
}

func TestGitRemovesACleanWorktreeAndNamesWhyItKeepsADirtyOne(t *testing.T) {
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	realGit(t, base, "init", "--quiet", "-b", "main", "clone")
	clone := filepath.Join(base, "clone")
	realGit(t, clone, "commit", "--allow-empty", "-m", "base")
	realGit(t, clone, "worktree", "add", "--quiet", "--detach", filepath.Join(base, "clean"))
	realGit(t, clone, "worktree", "add", "--quiet", "--detach", filepath.Join(base, "dirty"))
	if err := os.WriteFile(filepath.Join(base, "dirty", "scratch.txt"), []byte("unsaved"), 0o600); err != nil {
		t.Fatal(err)
	}
	git := &Git{Runner: shell.Exec{}}
	removed, err := git.Remove(context.Background(), filepath.Join(base, "clean"))
	if err != nil || removed != "" {
		t.Fatalf("removed %q, err %v", removed, err)
	}
	if isDir(filepath.Join(base, "clean")) {
		t.Fatal("the clean worktree is still there")
	}
	refused, err := git.Remove(context.Background(), filepath.Join(base, "dirty"))
	if err != nil || !strings.HasPrefix(refused, "fatal: ") {
		t.Fatalf("refused %q, err %v", refused, err)
	}
	if _, err := os.Stat(filepath.Join(base, "dirty", "scratch.txt")); err != nil {
		t.Fatal(err)
	}
}

func TestAWorktreeOfAnotherRepositoryIsCheckedAgainstThatRepositorysOrigin(t *testing.T) {
	other, otherRepository, pushed := "/w/ccx/fs-churn", "/w/ccx/.git", headD
	orca := newOrca(row("ctx_a"), row("ctx_b", in(other)))
	orca.terminals[testWT] = []Terminal{idleShellTerminal("term_s")}
	orca.terminals[other] = []Terminal{{Handle: "term_o", Title: "~/x/fs-churn", LastOutputMS: fakeNow - 3_600_000}}
	git := newGit(checkout(testWT), checkout(other, withHead(pushed), func(c *Checkout) { c.Repository = otherRepository }))
	git.remotes[otherRepository] = Remote{Heads: map[string]bool{pushed: true}, Landed: landedSet{}}
	tally, out := runGC(t, orca, git, gcOptions{})
	if tally[verbRemoved] != 2 {
		t.Fatalf("tally %v, output %q", tally, out)
	}
}

func TestGitReadsTheTrunkAndHeadsOfTheCheckoutsOwnOrigin(t *testing.T) {
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	realGit(t, base, "init", "--quiet", "--bare", "-b", "main", "origin.git")
	realGit(t, base, "clone", "--quiet", filepath.Join(base, "origin.git"), "clone")
	clone := filepath.Join(base, "clone")
	realGit(t, clone, "commit", "--allow-empty", "-m", "base")
	realGit(t, clone, "push", "--quiet", "origin", "main")
	realGit(t, clone, "worktree", "add", "--quiet", "-b", "lane", filepath.Join(base, "lane"))
	realGit(t, filepath.Join(base, "lane"), "commit", "--allow-empty", "-m", "lane")
	realGit(t, filepath.Join(base, "lane"), "push", "--quiet", "origin", "lane")
	realGit(t, clone, "worktree", "add", "--quiet", "--detach", filepath.Join(base, "trunk"), "main")
	git := &Git{Runner: shell.Exec{}}
	ctx := context.Background()
	lane, err := git.Checkout(ctx, filepath.Join(base, "lane"))
	if err != nil {
		t.Fatal(err)
	}
	trunk, err := git.Checkout(ctx, filepath.Join(base, "trunk"))
	if err != nil {
		t.Fatal(err)
	}
	remote, err := git.Remote(ctx, lane.Found.Repository)
	if err != nil {
		t.Fatal(err)
	}
	if lane.Found.Repository != filepath.Join(clone, ".git") || lane.Found.Branch != "lane" || lane.Found.OnTrunk {
		t.Fatalf("lane %+v", *lane.Found)
	}
	if !trunk.Found.OnTrunk {
		t.Fatalf("trunk %+v", *trunk.Found)
	}
	if !remote.Heads[lane.Found.Head] {
		t.Fatalf("heads %v lack %s", remote.Heads, lane.Found.Head)
	}
	if got := remote.Landed.(PullRequests).Origin; got != filepath.Join(base, "origin.git") {
		t.Fatalf("origin %q", got)
	}
}

func clientFor(reply func(argv []string) (string, string, int)) (Client, *scripted) {
	runner := &scripted{reply: reply}
	return Client{Runner: runner}, runner
}

func constant(body string) func([]string) (string, string, int) {
	return func([]string) (string, string, int) { return body, "", 0 }
}

func TestCloseTakesTheWholeTabUnlessAnotherPaneSharesIt(t *testing.T) {
	cases := []struct {
		name  string
		list  string
		wants []string
	}{
		{"alone in its tab", `{"ok":true,"result":{"terminals":[{"handle":"term_a","tabId":"tab_1","connected":true},{"handle":"term_b","tabId":"tab_2","connected":true}]}}`, []string{"orca", "terminal", "close", "--terminal", "term_a", "--tab", "--json"}},
		{"a shared tab", `{"ok":true,"result":{"terminals":[{"handle":"term_a","tabId":"tab_1","connected":true},{"handle":"term_b","tabId":"tab_1","connected":true}]}}`, []string{"orca", "terminal", "close", "--terminal", "term_a", "--json"}},
		{"a worktree Orca no longer tracks", `{"ok":false,"error":{"code":"selector_not_found","message":"selector_not_found"}}`, []string{"orca", "terminal", "close", "--terminal", "term_a", "--json"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			client, runner := clientFor(func(argv []string) (string, string, int) {
				switch argv[2] {
				case "show":
					return `{"ok":true,"result":{"terminal":{"tabId":"tab_1","worktreePath":"/w"}}}`, "", 0
				case "list":
					return c.list, "", 0
				}
				return `{"ok":true}`, "", 0
			})
			refusal, err := client.Close(context.Background(), "term_a", "")
			if err != nil || refusal != "" {
				t.Fatalf("refusal %q, err %v", refusal, err)
			}
			if got := runner.calls[len(runner.calls)-1]; !slices.Equal(got, c.wants) {
				t.Fatalf("got %v", got)
			}
		})
	}
}

func TestACloseRefusedAsStaleReadsAsClosedOnlyWhenThePaneIsGone(t *testing.T) {
	for _, c := range []struct {
		connected string
		want      string
	}{{"false", ""}, {"true", CodeStaleHandle}} {
		client, _ := clientFor(func(argv []string) (string, string, int) {
			switch argv[2] {
			case "show":
				return `{"ok":true,"result":{"terminal":{"tabId":"tab_1","worktreePath":"/w","connected":` + c.connected + `}}}`, "", 0
			case "list":
				return `{"ok":true,"result":{"terminals":[]}}`, "", 0
			}
			return `{"ok":false,"error":{"code":"terminal_handle_stale"}}`, "", 1
		})
		got, err := client.Close(context.Background(), "term_a", "")
		if err != nil || got != c.want {
			t.Fatalf("connected %s: got %q, err %v", c.connected, got, err)
		}
	}
}

func TestPresenceReadsTheHandle(t *testing.T) {
	client, runner := clientFor(constant(`{"ok":false,"error":{"code":"terminal_handle_stale"}}`))
	got, err := client.Presence(context.Background(), "term_a", "")
	if err != nil || got != CodeStaleHandle {
		t.Fatalf("got %q, err %v", got, err)
	}
	if want := []string{"orca", "terminal", "show", "--terminal", "term_a", "--json"}; !slices.Equal(runner.calls[0], want) {
		t.Fatalf("argv %v", runner.calls[0])
	}
	client, _ = clientFor(constant(`{"ok":true,"result":{"terminal":{"connected":false}}}`))
	if got, err := client.Presence(context.Background(), "term_a", ""); err != nil || got != disconnected {
		t.Fatalf("got %q, err %v", got, err)
	}
}

func TestStopFencesTheDispatchThroughOrca(t *testing.T) {
	client, runner := clientFor(constant(`{"ok":true,"_meta":{},"result":{"state":"stopped","processAction":"closed_agent_terminal"}}`))
	state, detail, err := client.Stop(context.Background(), "ctx_a")
	if err != nil || state != verbStopped || detail != closedByStop {
		t.Fatalf("state %q, detail %q, err %v", state, detail, err)
	}
	if want := []string{"orca", orchestration, "worker-stop", "--dispatch", "ctx_a", "--full", "--json"}; !slices.Equal(runner.calls[0], want) {
		t.Fatalf("argv %v", runner.calls[0])
	}
	client, _ = clientFor(constant(`{"ok":false,"error":{"code":"dispatch_not_found"}}`))
	state, detail, err = client.Stop(context.Background(), "ctx_a")
	if err != nil || state != "" || detail != "dispatch_not_found" {
		t.Fatalf("state %q, detail %q, err %v", state, detail, err)
	}
}

func TestIdleReads(t *testing.T) {
	now := func() int64 { return fakeNow }
	t.Run("a terminal whose process exited is already gone", func(t *testing.T) {
		client, runner := clientFor(constant(`{"ok":true,"result":{"terminal":{"connected":false,"lastOutputAt":0}}}`))
		got, err := client.Idle(context.Background(), "term_a", "", now)
		if err != nil || got != IdleUnknown || len(runner.calls) != 1 {
			t.Fatalf("got %v, err %v, calls %v", got, err, runner.calls)
		}
	})
	t.Run("output during the idle wait is not idle", func(t *testing.T) {
		shows := 0
		client, _ := clientFor(func(argv []string) (string, string, int) {
			if argv[2] == "wait" {
				return `{"ok":true,"result":{"wait":{"satisfied":true}}}`, "", 0
			}
			shows++
			last := "0"
			if shows > 1 {
				last = "10000000000"
			}
			return `{"ok":true,"result":{"terminal":{"connected":true,"lastOutputAt":` + last + `}}}`, "", 0
		})
		if got, err := client.Idle(context.Background(), "term_a", "", now); err != nil || got != IdleBusy {
			t.Fatalf("got %v, err %v", got, err)
		}
	})
	t.Run("a Sprite terminal reads through its environment", func(t *testing.T) {
		client, runner := clientFor(constant(`{"ok":false,"error":{"code":"terminal_handle_stale"}}`))
		if got, err := client.Idle(context.Background(), "term_a", "env_abc", now); err != nil || got != IdleUnknown {
			t.Fatalf("got %v, err %v", got, err)
		}
		if want := []string{"orca", "terminal", "show", "--terminal", "term_a", "--environment", "env_abc", "--json"}; !slices.Equal(runner.calls[0], want) {
			t.Fatalf("argv %v", runner.calls[0])
		}
	})
}

func TestADisconnectedTerminalNoLongerHoldsItsWorktree(t *testing.T) {
	client, _ := clientFor(constant(`{"ok":true,"result":{"terminals":[{"handle":"term_dead","agentIdentity":"codex","connected":false},{"handle":"term_sh","connected":true}]}}`))
	listed, err := client.TerminalsIn(context.Background(), testWT, "")
	if err != nil || len(listed.Terminals) != 1 || listed.Terminals[0].Handle != "term_sh" {
		t.Fatalf("listed %+v, err %v", listed, err)
	}
}

func TestAWorktreeOrcaNoLongerTracksHasNoTerminals(t *testing.T) {
	client, runner := clientFor(constant(`{"ok":false,"error":{"code":"selector_not_found","message":"selector_not_found"}}`))
	listed, err := client.TerminalsIn(context.Background(), testWT, "")
	if err != nil || !listed.Untracked {
		t.Fatalf("listed %+v, err %v", listed, err)
	}
	if want := []string{"orca", "terminal", "list", "--worktree", "path:" + testWT, "--limit", "1000", "--json"}; !slices.Equal(runner.calls[0], want) {
		t.Fatalf("argv %v", runner.calls[0])
	}
}

func TestForgetAndRemoveNameTheirRefusals(t *testing.T) {
	client, runner := clientFor(constant(`{"ok":true}`))
	if refusal, err := client.Forget(context.Background(), "env_abc"); err != nil || refusal != "" {
		t.Fatalf("refusal %q, err %v", refusal, err)
	}
	if want := []string{"orca", "environment", "rm", "--environment", "env_abc", "--json"}; !slices.Equal(runner.calls[0], want) {
		t.Fatalf("argv %v", runner.calls[0])
	}
	client, runner = clientFor(constant(`{"ok":false,"error":{"code":"worktree_dirty","message":"uncommitted changes"}}`))
	if refusal, err := client.Remove(context.Background(), testWT, ""); err != nil || refusal != "worktree_dirty: uncommitted changes" {
		t.Fatalf("refusal %q, err %v", refusal, err)
	}
	if want := []string{"orca", "worktree", "rm", "--worktree", "path:" + testWT, "--json"}; !slices.Equal(runner.calls[0], want) {
		t.Fatalf("argv %v", runner.calls[0])
	}
}

func TestAnOrchestrationCallAsksForTheFullRecordAndRefusesTheConciseView(t *testing.T) {
	replies := []string{`{"ok":true,"result":{"workers":[]},"_meta":{"runtimeId":"rt_1"}}`, `{"ok":true,"result":{"workers":[]}}`}
	client, runner := clientFor(func([]string) (string, string, int) {
		reply := replies[0]
		replies = replies[1:]
		return reply, "", 0
	})
	if _, err := client.Call(context.Background(), orchestration, "worker-list", "--run", "run_1"); err != nil {
		t.Fatal(err)
	}
	_, err := client.Call(context.Background(), orchestration, "worker-list", "--run", "run_1")
	if err == nil || !strings.Contains(err.Error(), "orca orchestration worker-list printed its concise view") {
		t.Fatalf("got %v", err)
	}
	if want := []string{"orca", orchestration, "worker-list", "--run", "run_1", "--full", "--json"}; !slices.Equal(runner.calls[0], want) {
		t.Fatalf("argv %v", runner.calls[0])
	}
}

func TestWorkersPageThroughTheRun(t *testing.T) {
	pages := []string{
		`{"ok":true,"_meta":{},"result":{"workers":[{"dispatchId":"ctx_a","dispatchStatus":"failed","terminalState":null,"agentTerminalHandle":null,"resource":null,"projection":{"host":{"kind":"remote","id":"env_box"},"workspace":null}}],"page":{"nextCursor":"c2"}}}`,
		`{"ok":true,"_meta":{},"result":{"workers":[{"dispatchId":"ctx_b","dispatchStatus":"completed","terminalState":"retained","agentTerminalHandle":"term_b","resource":{"ownershipState":"external","retainedReason":"external_terminal","worktreeId":"repo::/w/b"},"projection":{"workspace":{"id":"repo::/w/other"}}}],"page":{}}}`,
	}
	client, runner := clientFor(func([]string) (string, string, int) {
		reply := pages[0]
		pages = pages[1:]
		return reply, "", 0
	})
	got, err := client.Workers(context.Background(), "run_1")
	if err != nil {
		t.Fatal(err)
	}
	want := []Worker{
		{Dispatch: "ctx_a", Status: statusFailed, Environment: "env_box"},
		{Dispatch: "ctx_b", Status: statusCompleted, TerminalState: "retained", Ownership: "external", RetainedReason: external, Terminal: "term_b", Workspace: "repo::/w/b", Worktree: "/w/b"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v", got)
	}
	if !slices.Contains(runner.calls[1], "--cursor") {
		t.Fatalf("argv %v", runner.calls[1])
	}
}

func TestOrcaReadsABoxTreesHeadAndBranchFromItsServer(t *testing.T) {
	client, runner := clientFor(constant(`{"ok":true,"result":{"worktree":{"head":"` + headB + `","branch":"refs/heads/yasyf/box-lane"}}}`))
	read, err := client.PairedCheckout(context.Background(), boxWT, boxEnv, newGit())
	if err != nil {
		t.Fatal(err)
	}
	want := checkout(boxWT, withHead(headB), onEnv(boxEnv))
	want.Branch = "yasyf/box-lane"
	if !reflect.DeepEqual(*read.Found, want) {
		t.Fatalf("got %+v", *read.Found)
	}
	if wantArgv := []string{"orca", "worktree", "show", "--worktree", "path:" + boxWT, "--environment", boxEnv, "--json"}; !slices.Equal(runner.calls[0], wantArgv) {
		t.Fatalf("argv %v", runner.calls[0])
	}
}

func TestARemoteCheckoutReadsPorcelainBranchHeadersOverSSH(t *testing.T) {
	runner := &scripted{reply: constant("# branch.oid " + headA + "\n# branch.head (detached)\n? scratch.txt\n")}
	read, err := CCRemote{Runner: runner, State: "/nonexistent"}.Checkout(context.Background(), testSprite, newGit())
	if err != nil {
		t.Fatal(err)
	}
	want := Checkout{Path: testSprite.Root, Dirty: true, Head: headA, Repository: repository}
	if !reflect.DeepEqual(*read.Found, want) {
		t.Fatalf("got %+v", *read.Found)
	}
	if got := runner.calls[0][:6]; !slices.Equal(got, []string{"ssh", "-F", "/s/ssh/pool-abc.ssh", "-o", "BatchMode=yes", "pool-abc"}) {
		t.Fatalf("argv %v", got)
	}
}

func TestLandedSearchesClosedPRsForTheOneHeadItIsAskedAbout(t *testing.T) {
	runner := &scripted{reply: func(argv []string) (string, string, int) {
		switch argv[8] {
		case headB:
			return `[{"number":7,"headRefOid":"` + headB + `"}]`, "", 0
		case headC:
			return `[{"number":8,"headRefOid":"` + headC + `"}]`, "", 0
		}
		return "[]", "", 0
	}}
	landed := PullRequests{Runner: runner, Origin: "https://github.com/Forge-AI/monorepo", Squashed: map[int]bool{7: true}}
	for head, want := range map[string]bool{headB: true, headC: false, headD: false} {
		if got, err := landed.Landed(context.Background(), head); err != nil || got != want {
			t.Fatalf("%s: got %v, err %v", head, got, err)
		}
	}
	found := slices.ContainsFunc(runner.calls, func(argv []string) bool {
		return slices.Equal(argv, []string{"gh", "pr", "list", "--repo", "https://github.com/Forge-AI/monorepo", "--state", "closed", "--search", headB, "--json", "number,headRefOid"})
	})
	if !found {
		t.Fatalf("calls %v", runner.calls)
	}
}
