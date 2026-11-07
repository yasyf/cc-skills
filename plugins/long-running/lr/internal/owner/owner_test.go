package owner

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var addedAt = time.Date(2026, 11, 3, 5, 10, 0, 0, time.UTC)

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func staged(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "owner-ledger.md")
	if err := os.WriteFile(path, []byte(read(t, filepath.Join("testdata", "ledger.md"))), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestMarkClosesOnlyTheNamedRow(t *testing.T) {
	path := staged(t)
	if err := Mark(path, "m2", responded, 346705); err != nil {
		t.Fatal(err)
	}
	if err := Mark(path, "m1", addressed, 0); err != nil {
		t.Fatal(err)
	}
	if got, want := read(t, path), read(t, filepath.Join("testdata", "marked.md")); got != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestRowsHidesAddressedRowsUnlessAskedForAll(t *testing.T) {
	path := staged(t)
	if err := Mark(path, "m1", addressed, 346705); err != nil {
		t.Fatal(err)
	}
	waiting, err := Rows(path, false)
	if err != nil {
		t.Fatal(err)
	}
	every, err := Rows(path, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(every) != len(waiting)+1 || !strings.HasPrefix(every[0], "| m1 |") || slices.ContainsFunc(waiting, func(row string) bool { return strings.HasPrefix(row, "| m1 |") }) {
		t.Fatalf("waiting %q, every %q", waiting, every)
	}
}

func TestRowsOfALedgerNotYetWrittenAreEmpty(t *testing.T) {
	rows, err := Rows(filepath.Join(t.TempDir(), "owner-ledger.md"), true)
	if err != nil || rows != nil {
		t.Fatalf("rows %q, err %v", rows, err)
	}
}

func TestARemarkKeepsTheRoutedRecord(t *testing.T) {
	path := staged(t)
	if err := Mark(path, "m1", addressed, 346705); err != nil {
		t.Fatal(err)
	}
	if err := Mark(path, "m1", responded, 0); err != nil {
		t.Fatal(err)
	}
	if want := "| m1 | Nov 2 8:05 PM | keep the nightly report | 346705 | responded |\n"; !strings.Contains(read(t, path), want) {
		t.Fatalf("no %q in:\n%s", want, read(t, path))
	}
}

func TestARowTakesItsTimeFromItsStampAndAMarkKeepsItWhateverClockHisWordsCarry(t *testing.T) {
	path := staged(t)
	row, err := Add(path, addedAt, "2:13 AM: keep the nightly report", opened, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := Mark(path, row, addressed, 346705); err != nil {
		t.Fatal(err)
	}
	if want := "| m13 | Nov 2 9:10 PM | 2:13 AM: keep the nightly report | 346705 | addressed |\n"; !strings.Contains(read(t, path), want) {
		t.Fatalf("no %q in:\n%s", want, read(t, path))
	}
}

func TestAnUnknownRowIsRefusedAndTheLedgerStaysWhole(t *testing.T) {
	path := staged(t)
	before := read(t, path)
	for _, row := range []string{"m3", "m", "7:32 PM"} {
		if err := Mark(path, row, responded, 0); err == nil || !strings.Contains(err.Error(), row) {
			t.Fatalf("row %q: got %v, want a refusal naming it", row, err)
		}
	}
	if got := read(t, path); got != before {
		t.Fatalf("the ledger changed:\n%s", got)
	}
}

func run(t *testing.T, args ...string) (string, error) {
	t.Helper()
	config := t.TempDir()
	drives := filepath.Join(config, "long-running", "drives")
	if err := os.MkdirAll(drives, 0o750); err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(t.TempDir(), "brook")
	entry, err := json.Marshal(map[string]any{"drive": "d1", "cci_drive": "brook", "state_dir": state, "sessions": []string{"s1"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(drives, "d1.json"), entry, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	ledger := filepath.Join(state, "owner-relay", "owner-ledger.md")
	if err := os.MkdirAll(filepath.Dir(ledger), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(ledger, []byte(read(t, filepath.Join("testdata", "ledger.md"))), 0o600); err != nil {
		t.Fatal(err)
	}
	cmd := Command(&app.App{Now: func() time.Time { return addedAt }}, noLane)
	cmd.SetIn(strings.NewReader("a pick | on the board\nand a line\n"))
	cmd.SetOut(io.Discard)
	cmd.SetErr(io.Discard)
	cmd.SetArgs(append(args, "--drive", "brook"))
	err = cmd.Execute()
	return read(t, ledger), err
}

func TestTheVerbMarksTheNamedDrivesLedger(t *testing.T) {
	got, err := run(t, "mark", "m12", "responded", "--routed", "346705")
	if err != nil {
		t.Fatal(err)
	}
	if want := "| m12 | Nov 2 8:07 PM | release everything | 346705 | responded |\n"; !strings.Contains(got, want) {
		t.Fatalf("no %q in:\n%s", want, got)
	}
}

func TestTheVerbMarksTheRowTheHooksLedgerPathHolds(t *testing.T) {
	config := t.TempDir()
	drives := filepath.Join(config, "long-running", "drives")
	if err := os.MkdirAll(drives, 0o750); err != nil {
		t.Fatal(err)
	}
	scratch := t.TempDir()
	for _, entry := range []map[string]any{
		{"drive": "aaa", "cci_drive": "brook", "state_dir": filepath.Join(scratch, "other"), "sessions": []string{"s1"}},
		{"drive": "brook", "state_dir": filepath.Join(scratch, "brook"), "sessions": []string{"s2"}},
	} {
		data, err := json.Marshal(entry)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(drives, entry["drive"].(string)+".json"), data, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	ledger := filepath.Join(scratch, "brook", "owner-relay", "owner-ledger.md")
	if err := os.MkdirAll(filepath.Dir(ledger), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(ledger, []byte(header+"| m1 | Nov 2 8:05 PM | keep the nightly report |  | open |\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cmd := Command(&app.App{Now: func() time.Time { return addedAt }}, noLane)
	cmd.SetOut(io.Discard)
	cmd.SetErr(io.Discard)
	cmd.SetArgs([]string{"mark", "m1", "addressed", "--drive", "brook"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	if got := read(t, ledger); !strings.Contains(got, "| m1 | Nov 2 8:05 PM | keep the nightly report |  | addressed |\n") {
		t.Fatalf("m1 not marked:\n%s", got)
	}
}

func TestTheVerbRefusesAnyOtherState(t *testing.T) {
	got, err := run(t, "mark", "m1", "done")
	if err == nil || !strings.Contains(err.Error(), "responded, addressed, no-order or due") {
		t.Fatalf("got %v, want a refusal naming the four states", err)
	}
	if !strings.Contains(got, "| m1 | Nov 2 8:05 PM | keep the nightly report |  | open |\n") {
		t.Fatalf("m1 changed:\n%s", got)
	}
}

func TestAddContinuesTheRowsAndKeepsOneRowPerLine(t *testing.T) {
	path := staged(t)
	first, err := Add(path, addedAt, "a pick | on the board\nand a line\n", opened, 0)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Add(path, addedAt, "picked \u201cYes\u201d", addressed, 346705)
	if err != nil {
		t.Fatal(err)
	}
	if first != "m13" || second != "m14" {
		t.Fatalf("got rows %s and %s", first, second)
	}
	if got, want := read(t, path), read(t, filepath.Join("testdata", "added.md")); got != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestTheFirstAddStartsTheTableUnderAHandLedger(t *testing.T) {
	path := filepath.Join(t.TempDir(), "owner-relay", "owner-ledger.md")
	if _, err := Add(path, addedAt, "first", opened, 0); err != nil {
		t.Fatal(err)
	}
	if got, want := read(t, path), "\n"+header+"| m1 | Nov 2 9:10 PM | first |  | open |\n"; got != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
	if err := Mark(path, "m1", responded, 0); err != nil {
		t.Fatal(err)
	}
}

func TestTheAddVerbWritesTheNamedDrivesLedger(t *testing.T) {
	got, err := run(t, "add", "--state", "addressed", "--routed", "346705")
	if err != nil {
		t.Fatal(err)
	}
	if want := "| m13 | Nov 2 9:10 PM | a pick \\| on the board<br>and a line | 346705 | addressed |\n"; !strings.Contains(got, want) {
		t.Fatalf("no %q in:\n%s", want, got)
	}
}

func TestTheAddVerbRefusesAResponseItCannotHaveMade(t *testing.T) {
	if _, err := run(t, "add", "--state", "responded"); err == nil || !strings.Contains(err.Error(), "open or addressed") {
		t.Fatalf("got %v, want a refusal naming the two states", err)
	}
}

type subscribeRunner struct{ argv []string }

func (r *subscribeRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	r.argv = c.Argv
	return shell.Result{Stdout: "subscribed\n"}, nil
}

func TestStartBatchesRoutineRecordsAndNeverWhatTheOwnerSentOrTapped(t *testing.T) {
	runner := &subscribeRunner{}
	cmd := Command(&app.App{Runner: runner}, noLane)
	cmd.SetOut(io.Discard)
	cmd.SetArgs([]string{"start", "--drive", "dar"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	if got, want := strings.Join(runner.argv[:6], " "), "cci subscribe --drive dar --reader owner-relay"; got != want {
		t.Fatalf("subscribe = %q, want %q", got, want)
	}
	if !slices.Contains(runner.argv, "incident") {
		t.Fatalf("subscribe %q does not hear incident broadcasts", runner.argv)
	}
	batched := strings.Split(runner.argv[slices.Index(runner.argv, "--batch-kinds")+1], ",")
	for _, urgent := range []string{"owner", "ask", "decide", "blocked", "blocker", "urgent", "incident", "mechanism", "fix-live", "correction", "defect", "lr.escalation"} {
		if slices.Contains(batched, urgent) {
			t.Errorf("%s is batched, so it could wait five minutes", urgent)
		}
	}
}

func TestANoOrderRowLeavesTheWaitingListAndStillCountsTowardTheNextID(t *testing.T) {
	path := staged(t)
	if err := Mark(path, "m2", noOrder, 0); err != nil {
		t.Fatal(err)
	}
	waiting, err := Rows(path, false)
	if err != nil {
		t.Fatal(err)
	}
	if slices.ContainsFunc(waiting, func(row string) bool { return strings.HasPrefix(row, "| m2 |") }) {
		t.Fatalf("waiting %q", waiting)
	}
	if err := Mark(path, "m2", responded, 0); err != nil {
		t.Fatal(err)
	}
	row, err := Add(path, addedAt, "next", opened, 0)
	if err != nil || row != "m13" {
		t.Fatalf("row %s, err %v", row, err)
	}
}

func TestAnAsksRowIsMarkedAloneAndTheNextMessageNumbersPastIt(t *testing.T) {
	path := filepath.Join(t.TempDir(), "owner-ledger.md")
	if err := os.WriteFile(path, []byte(splitLedger), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := Mark(path, "m203.3", responded, 384200); err != nil {
		t.Fatal(err)
	}
	row, err := Add(path, addedAt, "keep the nightly report", opened, 0)
	if err != nil {
		t.Fatal(err)
	}
	waiting, err := Rows(path, false)
	if err != nil {
		t.Fatal(err)
	}
	if row != "m205" || len(waiting) != 5 || !strings.HasSuffix(waiting[0], "| 384100 | open |") || !strings.HasSuffix(waiting[2], "| 384200 | responded |") {
		t.Fatalf("row %s, waiting %q", row, waiting)
	}
}

func TestAContentAskStaysDueUntilAReplyCarriesIt(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "owner-ledger.md")
	ledger := header + "| m1 | Nov 3 6:17 PM | the holistic update you still have not given me |  | open |\n| m2 | Nov 3 6:18 PM | merge it |  | open |\n"
	if err := os.WriteFile(path, []byte(ledger), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := carried(path, dir, "m2", ""); err != nil {
		t.Fatalf("a row asking for no write-up was refused: %v", err)
	}
	for name, reply := range map[string]string{"no reply": "", "no such reply": "missing", "a promise": "short"} {
		if err := carried(path, dir, "m1", reply); err == nil {
			t.Fatalf("%s closed a content ask", name)
		}
	}
	if err := os.WriteFile(filepath.Join(dir, "short.json"), []byte(`{"reply":{"md":"lands by 6:40 PM"}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := carried(path, dir, "m1", "short"); err == nil || !strings.Contains(err.Error(), "a promise") {
		t.Fatalf("a promise closed a content ask: %v", err)
	}
	for name, md := range map[string]string{
		"rmw6394e5":             "The redundant part Sikan found is the steps log, about 14 progress rows rewritten per publish, and finished active-call rows kept forever; removing them cuts about 30% of statement bytes and 35% of retained journal data. The steps log is still read by the dashboard's run history, compaction and session convert, so the design moves those readers to the call traces table and then deletes the log; that design is the recommended option on the question above this line, and no code has changed so far.",
		"state and a next step": "CI is green on 4f1c2d0, and it lands by 7:10 PM.",
	} {
		writeReply(t, dir, "full", map[string]any{"md": md})
		if err := carried(path, dir, "m1", "full"); err != nil {
			t.Fatalf("%s: the reply carrying it was refused: %v", name, err)
		}
	}
	for _, md := range []string{"I'll look.", "Will fix by 7:30 PM.", "Looking into it; I’ll report back tonight.", "We're going to land it next, and it will be live by 8 PM."} {
		writeReply(t, dir, "promise", map[string]any{"md": md})
		if err := carried(path, dir, "m1", "promise"); err == nil || !strings.Contains(err.Error(), "a promise") {
			t.Fatalf("%q closed a content ask: %v", md, err)
		}
	}
	writeReply(t, dir, "rmw5yuqv3", map[string]any{"md": "Which way should the journal drop the steps log?", "picks": []string{"Move the readers, then delete the log", "Keep the log"}})
	if err := carried(path, dir, "m1", "rmw5yuqv3"); err != nil {
		t.Fatalf("his own pick card was refused: %v", err)
	}
}

func writeReply(t *testing.T, dir, id string, reply map[string]any) {
	t.Helper()
	body, err := json.Marshal(map[string]any{"reply": reply})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, id+".json"), body, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestADueRowGoesFirstOnceItsClockPasses(t *testing.T) {
	now := time.Date(2026, 11, 3, 18, 41, 0, 0, pacific)
	state, err := dueBy("6:40 PM", now.Add(-time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	if want := "due Nov 3 6:40 PM"; state != want {
		t.Fatalf("got %q, want %q", state, want)
	}
	if _, err := dueBy("6:40 PM", now); err == nil {
		t.Fatal("a clock already past was accepted")
	}
	if state, err := dueBy("12:15 AM", time.Date(2026, 11, 3, 23, 29, 0, 0, pacific)); err != nil || state != "due Nov 4 12:15 AM" {
		t.Fatalf("a clock past midnight read as %q, %v", state, err)
	}
	rows := Parse([]byte(header + "| m1 | Nov 3 6:17 PM | update | 391 | " + state + " |\n| m2 | Nov 3 6:18 PM | recap |  | due Nov 3 7:00 PM |\n"))
	late := PastDue(rows, now)
	if len(late) != 1 || late[0].ID != "m1" {
		t.Fatalf("past due rows: %+v", late)
	}
	if got, want := late[0].Line(), "m1 Nov 3 6:17 PM: update [PAST DUE NOV 3 6:40 PM]"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestAQuotedRowIsClassifiedByHisWordsNotTheFraming(t *testing.T) {
	for text, want := range map[string]bool{
		`Slack DM 9:29 PM, thread on the 9:30 update card: "I cant read the options the buttons cut them off"`: false,
		`Slack DM 9:29 PM: "give me the update"`: true,
		`also the holistic update I asked for`:   true,
	} {
		if got := contentAsk.MatchString(hisWords(text)); got != want {
			t.Errorf("%q asks for an update = %v, want %v", text, got, want)
		}
	}
}

func TestRespondedWarnsUnlessTheNamedReplyAnswersThatRow(t *testing.T) {
	replies := t.TempDir()
	if err := os.WriteFile(filepath.Join(replies, "r1.json"), []byte(`{"reply":{"id":"r1","order":"m12"}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if !namedBy(replies, "m12", "r1") {
		t.Fatal("the reply naming m12 did not count for m12")
	}
	for row, reply := range map[string]string{"m13": "r1", "m12": ""} {
		if namedBy(replies, row, reply) {
			t.Fatalf("%s with reply %q counted as answered", row, reply)
		}
	}
}
