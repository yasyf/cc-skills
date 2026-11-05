package orca

import (
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestARootStandDownOfSomeOfAnIncidentsLanesWithdrawsTheRest(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	for _, lane := range []string{"dd-1-fix", "dd-1-evidence", "dd-1-comms"} {
		w.register(store.Lane{Name: lane, Role: "lane", Launcher: coordinatorLane})
		w.line(launchText(lane, w.brief(lane, "Register slice: lane="+lane+" step=all\nScale: no N\nccx: place=local why=mac-build\n")))
	}
	w.now = w.now.Add(time.Minute)
	for _, lane := range []string{"dd-1-fix", "dd-1-evidence"} {
		w.add(object{"kind": kindStandDown, "text": "Stand down: not an alert", "to": []string{lane}})
	}
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatalf("a lane of a ruled-out incident was launched: %v", w.spawns)
	}
	wantContains(t, w.wantEscalation("WITHDRAWN "), "ruling incident dd-1 out")
}

func TestARootNoIncidentRulingStopsTheIncidentsRunningLanes(t *testing.T) {
	w := newDeskWorld(t)
	w.live("dd-1-comms", "ctx_c", "term_c")
	if _, err := w.desk.lane("dd-1-comms"); err != nil {
		t.Fatal(err)
	}
	w.lines()
	spawn := w.add(object{"lane": supervisorLane, "kind": "spawn", "text": "ALERT-LAUNCHED dd-1", "refs": object{"lanes": []string{"dd-1-fix"}}})
	w.add(object{"kind": "decide", "text": "No incident: dd-1's alert is another lane's post", "re": spawn.Seq})
	w.lines()
	stops := w.orcaCalls("orchestration worker-stop")
	if len(stops) != 1 || flag(stops[0], "--dispatch") != "ctx_c" {
		t.Fatalf("the ruled-out incident's comms lane was not stopped: %v", stops)
	}
}
