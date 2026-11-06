package orca

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"slices"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const spriteTerminal = "term_d6e0c136-36b6-472a-bad2-542a51a841e3"

func dispatchStop() orcaMessage {
	stop := runMessage("msg_s", typeEscalation, dispatchHandle+testDispatch, 1)
	stop.Subject = finishedStop
	stop.Body = "Worker " + spriteTerminal + " ended its turn without sending worker_done for task task_a6d8c0ed60b8."
	return stop
}

func (w *deskWorld) calledWith(words ...string) bool {
	return slices.ContainsFunc(w.calls, func(argv []string) bool {
		return !slices.ContainsFunc(words, func(word string) bool { return !slices.Contains(argv, word) })
	})
}

func TestASpriteStopFromItsDispatchHandleIsReadOnTheTerminalItsBodyNames(t *testing.T) {
	w := newDeskWorld(t)
	w.launchedBy("meta-desk-1")
	w.write(filepath.Join(w.desk.Config.CCRemote, "workspaces", "pool-1.json"), "{}")
	task, err := json.Marshal(object{"schemaVersion": ccRemoteTaskSchema, "provider": providerSprites, "workspace": "pool-1", "worktreeId": "wt", "terminal": spriteTerminal, "forward": object{"host": "pool-1-host"}})
	if err != nil {
		t.Fatal(err)
	}
	w.write(filepath.Join(w.desk.Config.CCRemote, "orca", "task.json"), string(task))
	w.rest = `{"tasks":["- ` + "`lr wait --wake`" + ` armed as pid 41"],"read":"1 armed lr wait processes bear terminal ` + spriteTerminal + `"}`

	w.checked(dispatchStop())

	w.noEscalation("DECIDE msg_s ")
	w.wantEscalation("RESTING msg_s lane-a: ended its turn with - `lr wait --wake` armed as pid 41")
	read := slices.IndexFunc(w.calls, func(argv []string) bool { return slices.Contains(argv, "pool-1-host") })
	if read < 0 || w.calls[read][len(w.calls[read])-1] != spriteTerminal {
		t.Fatal(w.calls)
	}
}

func TestASpriteStopWhoseWaitWokeTheLaneReadsTheScreenOfTheTerminalItsBodyNames(t *testing.T) {
	w := newDeskWorld(t)
	w.launchedBy("meta-desk-1")
	w.rest = `{"tasks":null,"read":"0 armed lr wait processes bear or record terminal ` + spriteTerminal + `"}`
	w.screen = []string{"• Ran gh pr checks 51907 --watch", "• Working (31s • esc to interrupt)", "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh fast · ~/w"}

	w.checked(dispatchStop())

	w.noEscalation("DECIDE msg_s ")
	if !w.calledWith("read", "--terminal", spriteTerminal) {
		t.Fatal(w.calls)
	}
}

func TestARootReplyToAStopAlarmReachesTheLaneUnlessAddressedOnlyElsewhere(t *testing.T) {
	for name, c := range map[string]struct {
		to      []string
		relayed bool
	}{
		"to desk-runner": {[]string{deskRunner}, false},
		"to owner-relay": {[]string{lanes.OwnerRelay}, false},
		"to the lane":    {[]string{testLane}, true},
		"to no one":      {nil, true},
	} {
		t.Run(name, func(t *testing.T) {
			w := newDeskWorld(t)
			w.live(testLane, testDispatch, testTerminal)
			alarm := w.add(object{"lane": deskRunner, "kind": "decide", "text": "DECIDE msg_s lane-a: Worker stopped without reporting; nothing armed found, on this host: 0 armed lr wait processes bear terminal term_a: "})
			w.lines()
			reply := w.add(object{"kind": "answer", "to": c.to, "text": "It waits only on his pick for #51907.", "resolves": alarm.Seq})
			w.lines()

			_, err := w.desk.store.Action(laneBook(testLane), fmt.Sprintf("cci#%d", reply.Seq))
			if relayed := err == nil; relayed != c.relayed {
				t.Fatalf("relayed %v, want %v; escalations %q", relayed, c.relayed, w.escalations())
			}
			if !c.relayed {
				w.wantEscalation(fmt.Sprintf("KEPT cci#%d lane-a: root's reply to its stop alarm #%d is addressed to %s", reply.Seq, alarm.Seq, c.to[0]))
			}
		})
	}
}
