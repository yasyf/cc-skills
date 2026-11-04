package orca

import (
	"context"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
)

func TestClockReadsAndPassesNeverOpenUnrelatedLaneDatabases(t *testing.T) {
	w := newDeskWorld(t)
	w.write(filepath.Join(w.desk.Config.Store, laneBook("historical")+".sqlite3"), "not a database")
	if err := w.desk.setClock("active", incidentClockState{Key: "R1"}); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.setClock("closed", incidentClockState{Key: "R2", Ended: "done"}); err != nil {
		t.Fatal(err)
	}
	live, err := w.desk.liveClocks()
	if err != nil || !reflect.DeepEqual(live, []string{"active"}) {
		t.Fatal(live, err)
	}
	if err := w.desk.clockPass(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestCompletedClocksLeaveTheWorkSetAndNewLaunchesReenterIt(t *testing.T) {
	w := clockedWorld(t)
	w.step(w.desk.clockPass)
	clock := w.clock()
	clock.Ended = "fix-live"
	if err := w.desk.setClock(clockLane, clock); err != nil {
		t.Fatal(err)
	}
	watching, err := w.desk.clockLanes()
	if err != nil || len(watching) != 0 {
		t.Fatal(watching, err)
	}
	if _, _, err := w.desk.acceptLaunch("R2", clockLane, launchTarget{Model: agentIncident, Brief: w.brief(clockLane, testBrief)}); err != nil {
		t.Fatal(err)
	}
	watching, err = w.desk.clockLanes()
	if err != nil || !reflect.DeepEqual(watching, []string{clockLane}) {
		t.Fatal(watching, err)
	}
	w.step(w.desk.incidentClock)
	watching, err = w.desk.clockLanes()
	if err != nil || !reflect.DeepEqual(watching, []string{clockLane}) {
		t.Fatal("pending launch lost its clock work", watching, err)
	}
}

func TestClockChangesAndWorkSetChangesRollBackTogether(t *testing.T) {
	w := newDeskWorld(t)
	if err := w.desk.store.Edit(deskRunner, func(tx *incident.Tx) error {
		if err := tx.DropFact(clockWork); err != nil {
			return err
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.setClock("lane", incidentClockState{Key: "R1"}); err == nil {
		t.Fatal("clock write succeeded without its work-set update")
	}
	_, found, err := w.desk.store.FactEntry(deskRunner, clockFacts, "lane")
	if err != nil || found {
		t.Fatal("partial clock write survived rollback", found, err)
	}
}
