package waste

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

func sessionID(n int) string {
	return fmt.Sprintf("00000000-0000-0000-0000-%012d", n)
}

func (w *world) rollout(t *testing.T, lane string, n int, began, modified time.Time, lines ...string) {
	t.Helper()
	w.bus.peers = append(w.bus.peers, client.Peer{Session: sessionID(n), Lane: lane})
	dir := filepath.Join(w.cfg.CodexSessions, began.In(time.Local).Format(codexDayLayout))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "rollout-"+began.Format("2006-01-02T15-04-05")+"-"+sessionID(n)+".jsonl")
	body := strings.Join(append([]string{codexLine(began, "session_meta", `{}`)}, lines...), "\n") + "\n"
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, modified, modified); err != nil {
		t.Fatal(err)
	}
}

func codexLine(at time.Time, kind, payload string) string {
	return fmt.Sprintf(`{"timestamp":%q,"ordinal":1,"type":%q,"payload":%s}`, at.Format(time.RFC3339Nano), kind, payload)
}

func compactedLines(at time.Time, count int) []string {
	lines := make([]string, count)
	for i := range lines {
		lines[i] = codexLine(at, "compacted", `{"message":""}`)
	}
	return lines
}

func TestCompactionsRankDriveWideAndNameTheLaneThatLostMostTurns(t *testing.T) {
	w := newWorld(t)
	w.rollout(t, "lane-a", 1, t0.Add(-time.Hour), t0, compactedLines(inside, 9)...)
	w.rollout(t, "lane-b", 2, t0.Add(-30*time.Minute), t0, compactedLines(inside, 3)...)

	report := w.read(t, Window)

	equal(t, signal(report, signalCompactions), []string{"lane-a 8"})
}

func TestARateAtTheMeasuredPostFixLevelIsNotAFinding(t *testing.T) {
	w := newWorld(t)
	w.rollout(t, "lane-a", 1, t0.Add(-time.Hour), t0, compactedLines(inside, 2)...)

	equal(t, signal(w.read(t, Window), signalCompactions), []string{})
}

func TestCompactionsBeforeTheWindowAndTheHoursBeforeItDoNotCount(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Compactions = 1
	w.rollout(t, "lane-a", 1, t0.Add(-2*time.Hour), t0, append(compactedLines(t0.Add(-90*time.Minute), 3), compactedLines(inside, 1)...)...)

	equal(t, signal(w.read(t, Window), signalCompactions), []string{"lane-a 1"})
}

func TestOnlyALineThatOpensWithTheMarkCountsAndAHugeLineDoesNotStopTheScan(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Compactions = 1
	hugeCompacted := codexLine(inside, "compacted", `{"message":"`+strings.Repeat("x", 1<<20)+`"}`)
	quoted := codexLine(inside, "event_msg", `{"pad":"`+strings.Repeat("y", 400)+`","type":"compacted"}`)
	w.rollout(t, "lane-a", 1, t0.Add(-time.Hour), t0, hugeCompacted, quoted, codexLine(inside, "compacted", `{}`))

	equal(t, signal(w.read(t, Window), signalCompactions), []string{"lane-a 2"})
}

func TestARolloutNoLaneOwnsCountsNothing(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Compactions = 1
	w.rollout(t, "lane-a", 1, t0.Add(-time.Hour), t0, compactedLines(inside, 9)...)
	w.bus.peers = nil

	equal(t, signal(w.read(t, Window), signalCompactions), []string{})
}

func TestCodexSessionsFollowTheConfigThenCodexHomeThenTheHomeDirectory(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("CODEX_HOME", "")

	got, err := codexSessions("")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, got, filepath.Join(home, ".codex", "sessions"))

	t.Setenv("CODEX_HOME", "/codex")
	got, _ = codexSessions("")
	equal(t, got, "/codex/sessions")

	got, _ = codexSessions("/configured")
	equal(t, got, "/configured")
}
