package orca

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestTheLaneStepsOpenNoStoreOfALaneWithoutOpenWork(t *testing.T) {
	for _, idle := range []int{0, scaletest.N() - 1} {
		w := newDeskWorld(t)
		w.live(testLane, testDispatch, testTerminal)
		w.acceptedRelay("R2")
		for i := range idle {
			w.write(filepath.Join(w.desk.Config.Store, fmt.Sprintf("%sidle-%d.sqlite3", lanePrefix, i)), "not a store")
		}
		for _, step := range []func() error{
			func() error { return w.desk.deliver(t.Context()) },
			func() error { return w.desk.reap(t.Context()) },
			func() error { return w.desk.stalledLaunches(t.Context()) },
			func() error { return w.desk.deadlines(t.Context()) },
		} {
			if err := step(); err != nil {
				t.Fatalf("with %d idle lane stores: %v", idle, err)
			}
		}
		if sends := w.orcaCalls("orchestration send"); len(sends) != 1 {
			t.Fatalf("with %d idle lane stores the relay was sent %d times", idle, len(sends))
		}
	}
}

func TestLinesPostedLongBeforeASlowPassReadsThemAreStillTaken(t *testing.T) {
	w := newDeskWorld(t)
	w.register(store.Lane{Name: testLane, Role: "lane", Launcher: coordinatorLane})
	brief := w.brief(testLane, testBrief+"ccx: place=local why=mac-build\n")
	w.lines()
	launch := w.line(launchText(testLane, brief))
	w.line("orca-desk: incident api-500-fix https://dd/1 :: errors")
	w.now = w.now.Add(24 * time.Minute)
	w.desk.passSince = w.now
	w.lines()
	w.desk.background.Wait()
	if action := w.action(testLane, "R"+strings.TrimPrefix(launch.Name(), "#")); action.Kind != kindLaunch {
		t.Fatal(action)
	}
	if len(w.fired) != 1 || w.fired[0].Slug != "api-500" {
		t.Fatal(w.fired)
	}
	w.noEscalation("LAUNCH-EXPIRED ")
}

func TestAMissingIndexIsRebuiltFromEveryLaneStoreOnce(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.acceptedRelay("R2")
	if err := w.desk.store.Edit(deskRunner, func(tx *incident.Tx) error { return tx.DropFact(openBooksFact) }); err != nil {
		t.Fatal(err)
	}
	w.step(w.desk.deliver)
	if sends := w.orcaCalls("orchestration send"); len(sends) != 1 {
		t.Fatalf("a rebuilt index sent the relay %d times", len(sends))
	}
}

func TestALaneLeavesTheIndexOnceItsWorkCloses(t *testing.T) {
	w := newDeskWorld(t)
	container := w.acceptedRelay("R2")
	if books, err := w.desk.workBooks(); err != nil || len(books) != 1 || books[0] != container {
		t.Fatal(books, err)
	}
	if err := w.desk.store.Edit(container, func(tx *incident.Tx) error {
		_, err := tx.Fail("R2", "withdrawn")
		return err
	}); err != nil {
		t.Fatal(err)
	}
	w.step(w.desk.deliver)
	if books, err := w.desk.workBooks(); err != nil || len(books) != 0 {
		t.Fatal(books, err)
	}
}
