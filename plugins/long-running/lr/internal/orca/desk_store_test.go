package orca

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
)

func TestRelayReadsReuseSQLiteSharedMemoryAndSeeExternalWrites(t *testing.T) {
	w := newDeskWorld(t)
	w.release = "first"
	checked := false
	w.desk.Sleep = func(time.Duration) {
		if checked {
			return
		}
		checked = true
		path, err := w.desk.store.Path(deskRunner)
		if err != nil {
			t.Fatal(err)
		}
		stamp := time.Unix(1, 0)
		if err := os.Chtimes(path+"-shm", stamp, stamp); err != nil {
			t.Fatal(err)
		}
		for range 64 {
			if _, err := w.desk.store.Header(deskRunner); err != nil {
				t.Fatal(err)
			}
		}
		info, err := os.Stat(path + "-shm")
		if err != nil {
			t.Fatal(err)
		}
		if !info.ModTime().Equal(stamp) {
			t.Fatal("unchanged reads rewrote SQLite shared memory")
		}
		writer := incident.Store{Root: w.desk.Config.Store}
		if err := writer.Edit(deskRunner, func(tx *incident.Tx) error {
			return tx.SetFact("io-probe", "updated")
		}); err != nil {
			t.Fatal(err)
		}
		value, found, err := w.desk.factOf(deskRunner, "io-probe")
		if err != nil || !found || value != "updated" {
			t.Fatalf("external update: %v, %v, %v", value, found, err)
		}
		w.release = "next"
	}
	if err := w.desk.Run(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if !checked {
		t.Fatal("relay never reached its idle boundary")
	}
}

func TestALaneStoodDownAsksTheInboxOnceAPassAndEveryTimeOutsideOne(t *testing.T) {
	w := newDeskWorld(t)
	ctx := context.Background()
	stoodDown := func(want string) {
		t.Helper()
		if down, err := w.desk.laneStoodDown(ctx, testLane); err != nil || down != want {
			t.Fatalf("stood down %q, want %q, err %v", down, want, err)
		}
	}
	stoodDown("")
	first := w.add(object{"lane": supervisorLane, "kind": kindStandDown, "text": "retired", "to": []string{testLane}})
	stoodDown(seqReceipt(first.Seq))

	w.desk.startPass()
	stoodDown(seqReceipt(first.Seq))
	tails := w.tails
	stoodDown(seqReceipt(first.Seq))
	if w.tails != tails {
		t.Fatalf("a second read in a pass asked the inbox %d more times", w.tails-tails)
	}
	w.records = nil
	stoodDown(seqReceipt(first.Seq))
	w.desk.startPass()
	stoodDown("")
}

func TestALanesRecordsAreAskedForOnceAPass(t *testing.T) {
	w := newDeskWorld(t)
	ctx := context.Background()
	asks := func(want int) {
		t.Helper()
		before := w.tails
		for range 3 {
			if _, err := w.desk.laneRecords(ctx, testLane, "head"); err != nil {
				t.Fatal(err)
			}
		}
		if got := w.tails - before; got != want {
			t.Fatalf("asked the inbox %d times, want %d", got, want)
		}
	}

	asks(3)
	w.desk.startPass()
	asks(1)
	asks(0)
	w.desk.startPass()
	asks(1)
}
