package coordinator

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const (
	testDrive = "d"
	short     = 20 * time.Millisecond
	forever   = time.Hour
)

func at(seq int64, lane, kind string) record {
	return record{Seq: seq, Lane: lane, Kind: kind, Text: "t", At: time.Date(2026, 11, 3, 5, 5, 0, 0, time.UTC)}
}

func arriving(rs ...record) <-chan record {
	records := make(chan record, len(rs))
	for _, r := range rs {
		records <- r
	}
	return records
}

func TestAWindowHoldsEveryRoutineRecordUntilItCloses(t *testing.T) {
	started := time.Now()
	batch := collect(context.Background(), arriving(at(1, "lane-a", "answer"), at(2, "lane-b", "done"), at(3, "lane-a", "blocker")), 50*time.Millisecond, forever, short)
	if len(batch) != 3 {
		t.Fatalf("batch holds %d records, want 3", len(batch))
	}
	if time.Since(started) < 50*time.Millisecond {
		t.Fatal("a routine record closed the window early")
	}
}

func TestARecordThatCannotWaitClosesTheWindowAndBringsTheRestAlong(t *testing.T) {
	for _, urgent := range []record{at(2, "owner-relay", "ask"), at(2, "root", "decide"), at(2, "lane-a", "incident"), at(2, "lane-a", "mechanism"), at(2, "lane-a", "fix-live"), at(2, "lane-a", "urgent")} {
		batch := collect(context.Background(), arriving(at(1, "lane-b", "answer"), urgent), forever, forever, short)
		if len(batch) != 2 || batch[1].Seq != 2 {
			t.Fatalf("%s %s: batch %+v, want both records", urgent.Lane, urgent.Kind, batch)
		}
	}
}

func TestAStallWatchUrgentWaitsForTheWindow(t *testing.T) {
	if closesAtOnce(at(1, stallWatch, "urgent")) {
		t.Fatal("a stall-watch urgent woke the coordinator on its own")
	}
}

func TestAnEmptyWindowEndsOnTheFirstLaterRecord(t *testing.T) {
	records := make(chan record)
	got := make(chan []record)
	go func() { got <- collect(context.Background(), records, short, forever, short) }()
	time.Sleep(3 * short)
	records <- at(1, "lane-a", "answer")
	if batch := <-got; len(batch) != 1 {
		t.Fatalf("batch %+v, want the one late record", batch)
	}
}

func TestAQuietDriveGivesUpInsideOneBashCall(t *testing.T) {
	if batch := collect(context.Background(), make(chan record), short, 2*short, short); len(batch) != 0 {
		t.Fatalf("batch %+v, want none", batch)
	}
}

func TestRepeatsOfOneStallWatchAlarmPrintOnceAtTheirNewest(t *testing.T) {
	alarm := func(seq int64, topic string) record {
		r := at(seq, stallWatch, "urgent")
		r.Topic, r.Text = topic, "stale "+topic
		return r
	}
	lines := fold([]record{alarm(1, "stale:1"), alarm(2, "stale:2"), alarm(3, "stale:1"), at(4, "lane-a", "blocker"), at(5, "lane-a", "blocker")})
	if len(lines) != 4 {
		t.Fatalf("%d lines, want 4: %+v", len(lines), lines)
	}
	if lines[0].Seq != 3 || lines[0].seen != 2 || lines[1].Seq != 2 || lines[1].seen != 1 {
		t.Fatalf("folded %+v", lines)
	}
	if !strings.Contains(lines[0].render(), "(seen 2 times)") {
		t.Fatalf("line %q does not say it repeated", lines[0].render())
	}
}

func TestALineNamesWhatTheCoordinatorActsOn(t *testing.T) {
	r := at(9, "lane-a", "ask")
	r.Re, r.To = 7, []string{"root@d", "lane-b"}
	r.Text, r.Refs.Path = "which branch?", "/blobs/b.txt"
	want := "#9 9:05 PM ASK lane-a re #7 -> lane-b: which branch? (body /blobs/b.txt)"
	if got := (line{r, 1}).render(); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func stubWatch(t *testing.T, rs ...record) string {
	t.Helper()
	dir := t.TempDir()
	var lines bytes.Buffer
	for _, r := range rs {
		raw, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		lines.Write(raw)
		lines.WriteByte('\n')
	}
	records := filepath.Join(dir, "records")
	if err := os.WriteFile(records, lines.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	argv := filepath.Join(dir, "argv")
	script := "#!/bin/sh\necho \"$@\" >> " + argv + "\ncat " + records + "\nexec sleep 60\n"
	installCCI(t, dir, script)
	return argv
}

func installCCI(t *testing.T, dir, script string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, "cci"), []byte(script), 0o700); err != nil { //nolint:gosec // the stub has to execute
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
}

func runNext(t *testing.T, cursor string) string {
	t.Helper()
	var out bytes.Buffer
	cmd := nextCommand()
	cmd.SetOut(&out)
	cmd.SetArgs([]string{"--drive", testDrive, "--cursor", cursor, "--every", (5 * short).String()})
	if err := cmd.ExecuteContext(context.Background()); err != nil {
		t.Fatal(err)
	}
	return out.String()
}

func TestNextPrintsOneBatchKillsItsWatchAndResumesAfterIt(t *testing.T) {
	argv := stubWatch(t, at(11, "lane-a", "answer"), at(12, "lane-b", "ask"))
	cursor := filepath.Join(t.TempDir(), "next.cursor")

	started := time.Now()
	out := runNext(t, cursor)
	if time.Since(started) > 30*time.Second {
		t.Fatal("next waited on its killed watch")
	}
	if !strings.Contains(out, "#11 ") || !strings.Contains(out, "#12 ") {
		t.Fatalf("batch %q misses a record", out)
	}
	saved, err := os.ReadFile(cursor)
	if err != nil || strings.TrimSpace(string(saved)) != "12" {
		t.Fatalf("cursor %q, %v, want 12", saved, err)
	}

	runNext(t, cursor)
	calls, err := os.ReadFile(argv)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(calls)), "\n")
	if len(lines) != 2 {
		t.Fatalf("watch ran %d times, want 2: %q", len(lines), calls)
	}
	if strings.Contains(lines[0], "--since") {
		t.Fatalf("first run named a cursor it does not have: %q", lines[0])
	}
	if !strings.Contains(lines[1], "--since 12") || !strings.Contains(lines[1], "--reader root") || !strings.Contains(lines[1], "--drive d") {
		t.Fatalf("second run did not resume as reader root after 12: %q", lines[1])
	}
}

func TestAWatchThatFailsSurfacesInsteadOfWaiting(t *testing.T) {
	dir := t.TempDir()
	installCCI(t, dir, "#!/bin/sh\necho no such drive >&2\nexit 3\n")
	cmd := nextCommand()
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs([]string{"--drive", testDrive, "--cursor", filepath.Join(dir, "c"), "--every", forever.String()})
	if err := cmd.ExecuteContext(context.Background()); err == nil {
		t.Fatal("a dead watch returned no error")
	}
}
