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
