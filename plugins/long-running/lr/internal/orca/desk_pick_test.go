package orca

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
)

const (
	pickCard   = "card-rmw537imn"
	pickedText = `Owner on board d50e80e8, Raw SQL incident (rmw537imn): picked "Fix forward"`
)

func (w *deskWorld) parkedOnPick(lane string) int64 {
	w.t.Helper()
	blocker := w.add(object{"lane": lane, "kind": kindBlocker, "status": recordOpen, "to": []string{coordinatorLane}, "text": "#51907 at 8dbd227 lands only on his pick on " + pickCard})
	w.add(object{"lane": lane, "kind": "state", "to": []string{coordinatorLane}, "text": "Acknowledged: #51907 stays at 8dbd227 until his pick; no new PR, merge or production action"})
	return blocker.Seq
}

func TestAStopWhileHisPickIsOpenRestsWithoutADecide(t *testing.T) {
	for name, park := range map[string]func(w *deskWorld) (int64, string){
		"a wait naming his card": func(w *deskWorld) (int64, string) {
			return w.parkedOnPick(testLane), kindBlocker
		},
		"an owner record that only names his card": func(w *deskWorld) (int64, string) {
			seq := w.parkedOnPick(testLane)
			w.add(object{"lane": lanes.OwnerRelay, "kind": kindOwnerPick, "to": []string{coordinatorLane}, "text": "Owner asked to see the open picks. My reading: " + pickCard + " and two others are drawn as cards."})
			return seq, kindBlocker
		},
		"an ask the communicator carded": func(w *deskWorld) (int64, string) {
			ask := w.add(object{"lane": testLane, "kind": kindAsk, "status": recordOpen, "to": []string{lanes.OwnerRelay}, "text": "Fix forward or roll the cloud runner back on SoFi's tenant?"})
			w.add(object{"lane": lanes.OwnerRelay, "kind": "note", "re": ask.Seq, "to": []string{testLane}, "text": pickCard + " now carries your question"})
			w.add(object{"lane": testLane, "kind": "state", "to": []string{coordinatorLane}, "text": "waiting on his answer"})
			return ask.Seq, kindAsk
		},
	} {
		t.Run(name, func(t *testing.T) {
			w := newDeskWorld(t)
			w.launchedBy("meta-desk-1")
			seq, kind := park(w)

			w.checked(stopNotice("msg_s", 1))

			w.noEscalation("DECIDE msg_s ")
			w.wantEscalation(fmt.Sprintf("RESTING msg_s lane-a: ended its turn with its open %s #%d on his pick on %s", strings.ToUpper(kind), seq, pickCard))
		})
	}
}

func TestAStopAfterHisPickIsAnsweredOrWithNoOpenWaitStillDecides(t *testing.T) {
	for name, after := range map[string]func(w *deskWorld, blocker int64){
		"answered": func(w *deskWorld, _ int64) {
			w.add(object{"lane": "board-watch", "kind": kindOwnerPick, "to": []string{"all"}, "text": pickedText, "fields": object{"attrs": object{lanes.CardAttr: pickCard}}})
		},
		"closed": func(w *deskWorld, blocker int64) {
			w.records[blocker-1].Status = "closed"
		},
	} {
		t.Run(name, func(t *testing.T) {
			w := newDeskWorld(t)
			w.launchedBy("meta-desk-1")
			after(w, w.parkedOnPick(testLane))

			w.checked(stopNotice("msg_s", 1))

			w.wantEscalation("DECIDE msg_s lane-a: Worker stopped without reporting; nothing armed found, ")
		})
	}
}

func TestStopsOfLanesParkedOnHisPicksRaiseNoAlarmAtOneLaneAndAtMany(t *testing.T) {
	alarms := func(n int) [2]int {
		w := newDeskWorld(t)
		for i := range n {
			lane := fmt.Sprintf("parked%03d", i)
			w.parkedOnPick(lane)
			stop := orcaMessage{ID: fmt.Sprintf("msg_%d", i), Type: typeEscalation, FromHandle: fmt.Sprintf("term_%d", i), Subject: finishedStop, Body: "Worker ended its turn without sending worker_done"}
			if err := w.desk.relayStop(context.Background(), stop, lane); err != nil {
				t.Fatal(err)
			}
		}
		var decided, rested int
		for _, line := range w.escalations() {
			switch {
			case strings.HasPrefix(line, "DECIDE "):
				decided++
			case strings.HasPrefix(line, "RESTING "):
				rested++
			}
		}
		return [2]int{decided, rested / n}
	}

	one := alarms(1)
	if one != [2]int{0, 1} {
		t.Fatalf("one parked lane: %d decides and %d rests per lane, want 0 and 1", one[0], one[1])
	}
	if many := alarms(scaletest.N()); many != one {
		t.Fatalf("%d parked lanes: %d decides and %d rests per lane, want %v", scaletest.N(), many[0], many[1], one)
	}
}

func TestAStopWithAnOpenAskNamingACardToAnotherLaneStillDecides(t *testing.T) {
	w := newDeskWorld(t)
	w.launchedBy("meta-desk-1")
	w.add(object{"lane": testLane, "kind": kindAsk, "status": recordOpen, "to": []string{"board-refresher"}, "text": "For " + pickCard + ": replace the dead background link"})
	w.add(object{"lane": testLane, "kind": "state", "to": []string{coordinatorLane}, "text": "working on the next row"})

	w.checked(stopNotice("msg_s", 1))

	w.wantEscalation("DECIDE msg_s lane-a: Worker stopped without reporting; nothing armed found, ")
}
