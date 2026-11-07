package coordinator

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	testDrive = "d"
	short     = 20 * time.Millisecond
	forever   = time.Hour
)

func always(record) bool { return true }

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
	batch := collect(context.Background(), arriving(at(1, "lane-a", "answer"), at(2, "lane-b", "done"), at(3, "lane-a", "blocker")), 50*time.Millisecond, forever, short, always)
	if len(batch) != 3 {
		t.Fatalf("batch holds %d records, want 3", len(batch))
	}
	if time.Since(started) < 50*time.Millisecond {
		t.Fatal("a routine record closed the window early")
	}
}

func TestARecordThatCannotWaitClosesTheWindowAndBringsTheRestAlong(t *testing.T) {
	for _, urgent := range []record{at(2, "owner-relay", "ask"), at(2, "root", "decide"), at(2, "lane-a", "incident"), at(2, "lane-a", "mechanism"), at(2, "lane-a", "fix-live"), at(2, "lane-a", "urgent")} {
		batch := collect(context.Background(), arriving(at(1, "lane-b", "answer"), urgent), forever, forever, short, always)
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
	go func() { got <- collect(context.Background(), records, short, forever, short, always) }()
	time.Sleep(3 * short)
	records <- at(1, "lane-a", "answer")
	if batch := <-got; len(batch) != 1 {
		t.Fatalf("batch %+v, want the one late record", batch)
	}
}

func TestAQuietDriveGivesUpInsideOneBashCall(t *testing.T) {
	if batch := collect(context.Background(), make(chan record), short, 2*short, short, always); len(batch) != 0 {
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

func withBody(t *testing.T, text, body string) record {
	t.Helper()
	path := filepath.Join(t.TempDir(), "body.txt")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	r := at(9, "lane-a", "ask")
	r.Text, r.Refs.Path = text, path
	return r
}

func TestABatchPrintsAClippedRecordWholeSoNoReadFollows(t *testing.T) {
	whole := "9:05 PM the whole question, past the point the bus cut it"
	if got := withBody(t, "9:05 PM the whole question…", whole+"\n").whole(); got != whole {
		t.Fatalf("got %q, want the blob in place of the clipped text", got)
	}
}

func TestABatchPrintsAShortDocumentBesideItsSummary(t *testing.T) {
	if got := withBody(t, "plan attached", "step one").whole(); got != "plan attached\nstep one" {
		t.Fatalf("got %q", got)
	}
}

func TestALongBodyStaysAPointerSoItCostsNoContext(t *testing.T) {
	r := withBody(t, "plan attached", strings.Repeat("x", bodyShown+1))
	got := r.whole()
	if strings.Contains(got, "xxx") || !strings.Contains(got, r.Refs.Path) || !strings.Contains(got, "4001 bytes") {
		t.Fatalf("got %q", got)
	}
}

func post(t *testing.T, d client.Draft) client.Record {
	t.Helper()
	d.Drive = testDrive
	stored, err := client.Post(context.Background(), d)
	if err != nil {
		t.Fatalf("post %s: %v", d.Kind, err)
	}
	return stored
}

func toRoot(t *testing.T, d client.Draft) client.Record {
	t.Helper()
	d.To = []string{lanes.Root}
	return post(t, d)
}

func cursorAfterNow(t *testing.T) string {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	seen := post(t, client.Draft{Lane: "lane-a", Kind: "note", Text: "before the coordinator's cursor"})
	cursor := filepath.Join(t.TempDir(), "next.cursor")
	if err := saveCursor(cursor, []record{{Seq: seen.Seq, Num: seen.Num, At: seen.At}}); err != nil {
		t.Fatal(err)
	}
	return cursor
}

func runNext(t *testing.T, cursor string) string {
	t.Helper()
	return runNextWith(t, "--drive", testDrive, "--cursor", cursor, "--every", (5 * short).String())
}

func runNextWith(t *testing.T, args ...string) string {
	t.Helper()
	var out bytes.Buffer
	cmd := nextCommand(app.New(io.Discard))
	cmd.SetOut(&out)
	cmd.SetArgs(args)
	if err := cmd.ExecuteContext(context.Background()); err != nil {
		t.Fatal(err)
	}
	return out.String()
}

func printed(out string, r client.Record) bool {
	return strings.Contains(out, fmt.Sprintf("#%d ", r.Seq))
}

func TestNextPrintsOneBatchAndResumesAfterIt(t *testing.T) {
	cursor := cursorAfterNow(t)
	answer := toRoot(t, client.Draft{Lane: "lane-a", Kind: "answer", Text: "dev", Topic: "base"})
	ask := toRoot(t, client.Draft{Lane: "lane-b", Kind: "ask", Text: "which base?"})

	if out := runNext(t, cursor); !printed(out, answer) || !printed(out, ask) {
		t.Fatalf("batch %q misses a record", out)
	}
	saved, err := os.ReadFile(cursor)
	if want := ask.Name() + " " + ask.At.Truncate(time.Millisecond).Format(time.RFC3339Nano); err != nil || strings.TrimSpace(string(saved)) != want {
		t.Fatalf("cursor %q, %v, want %q", saved, err, want)
	}

	blocker := toRoot(t, client.Draft{Lane: "lane-a", Kind: "blocker", Text: "red"})
	if out := runNext(t, cursor); printed(out, ask) || !printed(out, blocker) {
		t.Fatalf("second batch %q, want only #%d", out, blocker.Seq)
	}
}

func TestACursorNamingARecordThisStoreHasNotTakenRefusesInsteadOfWaiting(t *testing.T) {
	cursor := cursorAfterNow(t)
	toRoot(t, client.Draft{Lane: "lane-a", Kind: "ask", Text: "which base?"})
	if err := os.WriteFile(cursor, []byte("#575540 "+time.Now().Add(-time.Minute).Format(time.RFC3339Nano)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cmd := nextCommand(app.New(io.Discard))
	cmd.SetOut(io.Discard)
	cmd.SetArgs([]string{"--drive", testDrive, "--cursor", cursor, "--every", forever.String()})
	done := make(chan error, 1)
	go func() { done <- cmd.ExecuteContext(context.Background()) }()
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "has not taken #575540 yet") {
			t.Fatalf("next = %v, want a refusal naming the record the store lacks", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("next waited on a cursor this store cannot reach instead of refusing")
	}
}

func TestACursorHoldingABareSeqRefuses(t *testing.T) {
	cursor := cursorAfterNow(t)
	if err := os.WriteFile(cursor, []byte("575540\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := readCursor(context.Background(), testDrive, cursor); err == nil || !strings.Contains(err.Error(), "delete it") {
		t.Fatalf("readCursor = %v, want a refusal naming the fix", err)
	}
}

func TestEveryKindAddressedToRootPrints(t *testing.T) {
	for _, kind := range append(client.Kinds(), "waste.rank") {
		t.Run(kind, func(t *testing.T) {
			cursor := cursorAfterNow(t)
			r := toRoot(t, client.Draft{Lane: "lane-a", Kind: kind, Text: "#7 " + kind, Topic: "t", PRs: []string{"7"}, LaneRefs: []string{"lane-b"}, Attrs: map[string]string{"brief": "/brief.md", "model": "opus"}})
			if out := runNext(t, cursor); !printed(out, r) {
				t.Fatalf("a %s addressed to root did not print: %q", kind, out)
			}
		})
	}
}

func TestAHoldPrintsAboveTheRecordsThatArrivedBeforeIt(t *testing.T) {
	var out bytes.Buffer
	if err := (view{}).print(&out, []record{at(1, "lane-a", "answer"), at(2, "owner-relay", "hold"), at(3, "lane-b", "ask")}, nil, "", time.Now()); err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 3 || !strings.HasPrefix(lines[0], "#2 ") || !strings.HasPrefix(lines[1], "#1 ") || !strings.HasPrefix(lines[2], "#3 ") {
		t.Fatalf("batch printed %q, want the hold first", out.String())
	}
}

func TestAWatchThatFailsSurfacesInsteadOfWaiting(t *testing.T) {
	dir := t.TempDir()
	notADirectory := filepath.Join(dir, "home")
	if err := os.WriteFile(notADirectory, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CCI_HOME", notADirectory)
	cmd := nextCommand(app.New(io.Discard))
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs([]string{"--drive", testDrive, "--cursor", filepath.Join(dir, "c"), "--every", forever.String()})
	if err := cmd.ExecuteContext(context.Background()); err == nil {
		t.Fatal("a dead watch returned no error")
	}
}
