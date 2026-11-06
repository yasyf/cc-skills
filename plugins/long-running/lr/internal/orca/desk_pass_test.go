package orca

import (
	"context"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
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

func TestRootRepliesToManyLanesCostTheLineReadNoMoreOrcaCallsThanOne(t *testing.T) {
	read := func(lanes int) int {
		w := newDeskWorld(t)
		w.lines()
		replies := make([]int64, lanes)
		for i := range lanes {
			lane := fmt.Sprintf("lane-%d", i)
			w.live(lane, fmt.Sprintf("ctx_%d", i), fmt.Sprintf("term_%d", i))
			decide := w.add(object{"lane": deskRunner, "kind": "decide", "text": "DECIDE msg_q " + lane + ": which way"})
			replies[i] = w.add(object{"kind": "answer", "text": "left", "re": decide.Seq}).Seq
		}
		w.calls = nil
		w.desk.startPass()
		w.lines()
		for i, reply := range replies {
			held, err := relayOf(w.action(fmt.Sprintf("lane-%d", i), fmt.Sprintf("cci#%d", reply)))
			if err != nil || held.ReplyTo != "msg_q" {
				t.Fatal(i, held, err)
			}
		}
		return w.orcaCount()
	}
	if one, many := read(1), read(scaletest.N()); one != many {
		t.Fatalf("a line read made %d Orca calls for 1 lane and %d for %d", one, many, scaletest.N())
	}
}

func TestOnePassSharesOneOpenDispatchReadAcrossTheSweepAndTheDeadlines(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.dispatches[testDispatch].status = statusCompleted
	w.desk.startPass()
	w.step(w.desk.sweep)
	if status, err := w.desk.settledStatus(context.Background(), testLane, testDispatch); err != nil || status != statusCompleted {
		t.Fatal(status, err)
	}
	if lists := w.orcaCalls("orchestration task-list"); len(lists) != 1 {
		t.Fatalf("one pass read the open dispatches %d times", len(lists))
	}
	if shows := w.orcaCalls("orchestration dispatch-show"); len(shows) != 1 {
		t.Fatalf("one pass confirmed the settled dispatch %d times", len(shows))
	}
}

func (w *deskWorld) retain(dispatches int) {
	w.t.Helper()
	if err := w.desk.store.Edit(deskRunner, func(tx *incident.Tx) error {
		for i := range dispatches {
			lane, dispatch := fmt.Sprintf("old-%d", i), fmt.Sprintf("ctx_old%d", i)
			w.write(filepath.Join(w.desk.Config.Receipts, lane+".json"), fmt.Sprintf(`{"result":{"taskId":%q,"dispatchId":%q}}`, taskOf(dispatch), dispatch))
			w.write(filepath.Join(w.desk.Config.Receipts, lane+".terminal"), fmt.Sprintf("term_old%d\n", i))
			w.dispatches[dispatch] = &fakeDispatch{status: statusCompleted, terminal: fmt.Sprintf("term_old%d", i), observation: "gone"}
			if _, _, err := tx.AcceptDue("reclaim:"+dispatch, kindReclaim, fmt.Sprintf("term_old%d", i), lane, w.now, zeroTime); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		w.t.Fatal(err)
	}
}

func TestASweepAndARelayingPassCostTheSameOrcaCallsAtAnyRunHistory(t *testing.T) {
	costs := func(retained int) (int, int) {
		w := newDeskWorld(t)
		w.retain(retained)
		w.live(testLane, testDispatch, testTerminal)
		w.live("lane-done", "ctx_done", "term_done")
		w.dispatches["ctx_done"].status = statusCompleted
		w.desk.startPass()
		w.calls = nil
		w.step(w.desk.sweep)
		swept := w.orcaCount()
		w.wantEscalation("RECLAIM reclaim:ctx_done runner")
		w.desk.startPass()
		w.calls = nil
		w.sentRelay("R2", "")
		return swept, w.orcaCount()
	}
	sweep, relay := costs(2000)
	if manySweep, manyRelay := costs(20000); sweep != manySweep || relay != manyRelay {
		t.Fatalf("a sweep made %d Orca calls at 2,000 retained dispatches and %d at 20,000; a relaying pass made %d and %d", sweep, manySweep, relay, manyRelay)
	}
}

func TestARelayLineAfterAQuestionAskedMidPassAnswersIt(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.lines()
	w.desk.startPass()
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: ok")
	w.lines()
	asked := slices.DeleteFunc(w.orcaCalls("orchestration check"), func(argv []string) bool { return !slices.Contains(argv, typeQuestion) })
	if len(asked) != 1 {
		t.Fatalf("the first read asked Orca for questions %d times", len(asked))
	}
	w.runInbox = []orcaMessage{question("msg_q", testTerminal)}
	w.line("R3 (5:02 PM) orca-desk: relay to lane-a: left")
	w.lines()
	held, err := relayOf(w.action(testLane, "R3"))
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
