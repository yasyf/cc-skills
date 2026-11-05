package grading

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func stubBinrun(t *testing.T, script string) (plugin, log string) {
	t.Helper()
	plugin = t.TempDir()
	log = filepath.Join(t.TempDir(), "binrun.log")
	path := filepath.Join(plugin, "scripts", "binrun.sh")
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	body := "#!/bin/sh\necho \"$*\" >> " + log + "\n" + script
	if err := os.WriteFile(path, []byte(body), 0o700); err != nil { //nolint:gosec // the stub has to execute
		t.Fatal(err)
	}
	return plugin, log
}

func codexSession(t *testing.T, home, name string, modified time.Time) string {
	t.Helper()
	path := filepath.Join(home, ".codex", "sessions", "2026", "11", name)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, modified, modified); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestPruneLocalDiskCollectsOldBinariesAndDropsSessionsOlderThanADay(t *testing.T) {
	plugin, log := stubBinrun(t, "")
	home := t.TempDir()
	now := time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC)
	old := codexSession(t, home, "old.jsonl", now.Add(-25*time.Hour))
	recent := codexSession(t, home, "recent.jsonl", now.Add(-23*time.Hour))

	if err := pruneLocalDisk(context.Background(), home, plugin, now); err != nil {
		t.Fatal(err)
	}

	calls, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(calls)) != "-- gc" {
		t.Fatalf("binrun called with %q, want the gc verb", calls)
	}
	if _, err := os.Stat(old); !os.IsNotExist(err) {
		t.Fatalf("a session past its life stayed: %v", err)
	}
	if _, err := os.Stat(recent); err != nil {
		t.Fatalf("a session within its life went: %v", err)
	}
}

func TestPruneLocalDiskOnAMachineWithNoCodexSessionsSucceeds(t *testing.T) {
	plugin, _ := stubBinrun(t, "")

	if err := pruneLocalDisk(context.Background(), t.TempDir(), plugin, time.Now()); err != nil {
		t.Fatal(err)
	}
}

func TestPruneLocalDiskNamesAFailedCollectionAndLeavesSessionsAlone(t *testing.T) {
	plugin, _ := stubBinrun(t, "echo the store is locked >&2\nexit 3\n")
	home := t.TempDir()
	now := time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC)
	old := codexSession(t, home, "old.jsonl", now.Add(-48*time.Hour))

	err := pruneLocalDisk(context.Background(), home, plugin, now)

	if err == nil || !strings.Contains(err.Error(), "the store is locked") {
		t.Fatalf("got %v, want the gc failure with its output", err)
	}
	if _, err := os.Stat(old); err != nil {
		t.Fatalf("a failed collection still removed a session: %v", err)
	}
}
