package orca

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func (w *deskWorld) orcaCount() int {
	count := 0
	for _, call := range w.calls {
		if call[0] == "orca" {
			count++
		}
	}
	return count
}

func TestRootRepliesToAHundredLanesCostTheLineReadNoMoreOrcaCallsThanOne(t *testing.T) {
	read := func(lanes int) int {
		w := newDeskWorld(t)
		w.runInbox = []orcaMessage{question("msg_q", "term_0")}
		w.lines()
		var first int64
		for i := range lanes {
			lane := fmt.Sprintf("lane-%d", i)
			w.live(lane, fmt.Sprintf("ctx_%d", i), fmt.Sprintf("term_%d", i))
			asked := w.add(object{"lane": lane, "kind": "ask", "text": "which way"})
			reply := w.add(object{"kind": "answer", "text": "left", "re": asked.Seq})
			if i == 0 {
				first = reply.Seq
			}
		}
		w.calls = nil
		w.desk.startPass()
		w.lines()
		held, err := relayOf(w.action("lane-0", fmt.Sprintf("cci#%d", first)))
		if err != nil || held.ReplyTo != "msg_q" {
			t.Fatal(held, err)
		}
		return w.orcaCount()
	}
	if one, hundred := read(1), read(100); one != hundred {
		t.Fatalf("a line read made %d Orca calls for 1 lane and %d for 100", one, hundred)
	}
}

func TestOnePassSharesOneWorkerListAcrossTheSweepAndTheDeadlines(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.dispatches[testDispatch].status = statusCompleted
	w.desk.startPass()
	w.step(w.desk.sweep)
	w.desk.settled = nil
	if _, err := w.desk.settledStatus(context.Background(), testDispatch); err != nil {
		t.Fatal(err)
	}
	if lists := w.orcaCalls("orchestration worker-list"); len(lists) != 1 {
		t.Fatalf("one pass listed the workers %d times", len(lists))
	}
}

func TestARootAnswerToAQuestionAskedMidPassReachesTheLane(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.lines()
	w.desk.startPass()
	early := w.add(object{"lane": testLane, "kind": "note", "text": "on it"})
	w.add(object{"kind": "answer", "text": "ok", "re": early.Seq})
	w.lines()
	if asks := w.orcaCalls("orchestration check"); len(asks) != 1 {
		t.Fatalf("the first read asked Orca for questions %d times", len(asks))
	}
	w.runInbox = []orcaMessage{question("msg_q", testTerminal)}
	asked := w.add(object{"lane": testLane, "kind": "ask", "text": "which way"})
	reply := w.add(object{"kind": "answer", "text": "left", "re": asked.Seq})
	w.lines()
	held, err := relayOf(w.action(testLane, fmt.Sprintf("cci#%d", reply.Seq)))
	if err != nil || held.ReplyTo != "msg_q" {
		t.Fatal(held, err)
	}
}

func TestAWorkerThatResumedMidPassKeepsItsRelayOpen(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.sentRelay("R2", "")
	w.now = w.now.Add(11 * time.Minute)
	w.dispatches[testDispatch].status = statusCompleted
	w.desk.startPass()
	w.step(w.desk.sweep)
	w.dispatches[testDispatch].status = "running"
	w.step(w.desk.deadlines)
	w.noEscalation("RELAY-CLOSED ")
	if status := w.relayStatus("R2"); status != incident.ActionAccepted {
		t.Fatal(status)
	}
}

func TestDeliverAndDeadlinesReadALanesDispatchAndMailboxOncePerPass(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.desk.startPass()
	w.sentRelay("R2", "")
	w.now = w.now.Add(11 * time.Minute)
	w.step(w.desk.deadlines)
	w.desk.unreadSeen = nil
	w.step(w.desk.deadlines)
	if shows := w.orcaCalls("orchestration worker-show"); len(shows) != 1 {
		t.Fatalf("one pass showed the lane's dispatch %d times", len(shows))
	}
	peeks := 0
	for _, call := range w.orcaCalls("orchestration check") {
		if slices.Contains(call, "--peek") {
			peeks++
		}
	}
	if peeks != 1 {
		t.Fatalf("one pass peeked the lane's mailbox %d times", peeks)
	}
}

func TestAWithdrawInAPassConfirmsTheStopFromAFreshRead(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.lines()
	w.desk.startPass()
	w.line("R9 (5:02 PM) orca-desk: withdraw lane-a")
	w.lines()
	w.wantEscalation("WITHDRAWN lane-a lane-a: by R9; dispatch ctx_a stopped")
	if shown, err := w.desk.show(context.Background(), testLane, ""); err != nil || shown.active() {
		t.Fatal(shown, err)
	}
}

func TestADeadlinesPassStopsAtItsBudgetInsideOneLane(t *testing.T) {
	w := newDeskWorld(t)
	for _, key := range []string{"R2", "R3"} {
		w.acceptedRelay(key)
	}
	clock := w.now.Add(11 * time.Minute)
	w.desk.Now = func() time.Time {
		clock = clock.Add(deadlineBudget)
		return clock
	}
	w.step(w.desk.deadlines)
	deadlined := 0
	for _, line := range w.escalations() {
		if strings.HasPrefix(line, "DEADLINE ") {
			deadlined++
		}
	}
	if deadlined != 1 || w.desk.deadlinedTo != "" {
		t.Fatalf("a deadlines pass over its budget raised %d deadlines and moved its cursor to %q", deadlined, w.desk.deadlinedTo)
	}
}

func TestAStalledLaunchLeavesARelayLineReadInTheSamePass(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.register(store.Lane{Name: "lane-b", Role: "lane", Launcher: coordinatorLane})
	brief := w.brief("lane-b", "Register slice: lane=lane-b step=all\nScale: no N\nccx: place=local why=mac-build\n")
	w.process = &fakeProcess{}
	if err := w.desk.Run(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	w.add(object{"text": launchText("lane-b", brief), "to": []string{deskLane}})
	w.line("R3 (5:02 PM) orca-desk: relay to lane-a: go")
	if err := w.desk.Run(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	if len(w.spawns) != 1 || w.desk.running() != 1 {
		t.Fatalf("the launch did not start and stay running: %v", w.spawns)
	}
	sends := w.orcaCalls("orchestration send")
	if len(sends) != 1 || flag(sends[0], "--to") != "dispatch:"+testDispatch {
		t.Fatalf("the relay was not sent while the launch ran: %v", w.calls)
	}
}
