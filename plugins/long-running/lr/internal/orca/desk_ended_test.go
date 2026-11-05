package orca

import (
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func (w *deskWorld) launchLineFor(lane string) {
	w.t.Helper()
	w.register(store.Lane{Name: lane, Role: "lane", Launcher: coordinatorLane})
	brief := w.brief(lane, "Register slice: lane="+lane+" step=all\nScale: no N\nccx: place=local why=mac-build\n")
	w.lines()
	w.line(launchText(lane, brief))
	w.now = w.now.Add(time.Minute)
}

func TestALaunchLineIsNotLaunchedWhenItsLaneStoodDownAfterIt(t *testing.T) {
	w := newDeskWorld(t)
	w.launchLineFor(testLane)
	w.add(object{"kind": kindStandDown, "text": "Stand down: not an alert", "to": []string{testLane}})
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatalf("a stood-down lane was launched: %v", w.spawns)
	}
	wantContains(t, w.wantEscalation("WITHDRAWN "), "stood down on cci #")
}

func TestALaunchLineIsNotLaunchedWhenARootWithdrawFollowsItInTheSameRead(t *testing.T) {
	w := newDeskWorld(t)
	w.launchLineFor(testLane)
	w.add(object{"kind": verbWithdraw, "text": "no", "re": w.records[len(w.records)-1].Seq})
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatalf("a withdrawn launch line was launched: %v", w.spawns)
	}
}

func TestAnIncidentLineIsNotFiredWhenItsFixLaneStoodDownOrRootWithdrewIt(t *testing.T) {
	for _, tt := range []struct {
		name, want string
		after      func(w *deskWorld, line int64)
	}{
		{"stood down", "stood down on cci #", func(w *deskWorld, _ int64) {
			w.add(object{"kind": kindStandDown, "text": "Stand down: not an alert", "to": []string{"api-500-fix"}})
		}},
		{"withdrawn", "root withdrew it on cci #", func(w *deskWorld, line int64) {
			w.add(object{"kind": verbWithdraw, "text": "no", "re": line})
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			w := newDeskWorld(t)
			w.lines()
			line := w.line("orca-desk: incident api-500-fix https://dd/1 :: errors")
			w.now = w.now.Add(time.Minute)
			tt.after(w, line.Seq)
			w.lines()
			w.desk.background.Wait()
			if len(w.fired) != 0 {
				t.Fatalf("an ended incident line fired: %v", w.fired)
			}
			wantContains(t, w.wantEscalation("WITHDRAWN "), tt.want)
		})
	}
}
