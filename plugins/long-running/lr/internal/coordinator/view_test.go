package coordinator

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

func shown(t *testing.T, v view, batch ...record) (string, string) {
	t.Helper()
	whole := filepath.Join(t.TempDir(), wholeBatchFile)
	var out bytes.Buffer
	if err := v.print(&out, batch, []ccx.PRStatus{{Number: 7, State: "OPEN", Queue: "queued"}}, whole, time.Now()); err != nil {
		t.Fatal(err)
	}
	return out.String(), whole
}

func kindMute(kind string) mute { return mute{ID: 1, Kind: kind, Why: "noise"} }

func textMute(pattern string) mute {
	return mute{ID: 2, Text: pattern, Why: "noise", pattern: regexp.MustCompile(pattern)}
}

func TestAMuteHidesItsRecordsCountsThemAndKeepsThemInTheWholeBatch(t *testing.T) {
	out, whole := shown(t, view{mutes: []mute{kindMute("state"), textMute("desk-runner: DEADLINE")}},
		at(1, "lane-a", "state"), at(2, "lane-b", "state"), at(3, "lane-a", "ask"), withText(at(4, "desk-runner", "report"), "DEADLINE x"))
	if strings.Contains(out, "#1 ") || strings.Contains(out, "#2 ") || !strings.Contains(out, "#3 ") {
		t.Fatalf("batch %q", out)
	}
	if !strings.Contains(out, "muted 3: 2 STATE, 1 /desk-runner: DEADLINE/\n") {
		t.Fatalf("batch %q does not count what it hid", out)
	}
	if !strings.Contains(out, "whole batch: "+whole) {
		t.Fatalf("batch %q does not name the file holding what it hid", out)
	}
	kept, err := os.ReadFile(whole)
	if err != nil || !strings.Contains(string(kept), "#1 ") {
		t.Fatalf("whole batch %q, %v, want the muted record", kept, err)
	}
}

func withText(r record, text string) record {
	r.Text = text
	return r
}

func TestATextMuteReadsTheLineAsItPrints(t *testing.T) {
	r := withText(at(9, "lane-a", "head"), "new head")
	if !textMute(`^#[0-9]+ [0-9:]+ [AP]M HEAD lane-a: new`).hides(r) {
		t.Fatal("a pattern over the printed line did not match")
	}
}

func TestNoMuteHidesWhatCannotWait(t *testing.T) {
	everything := view{mutes: []mute{textMute(".")}}
	for _, r := range []record{
		at(1, "lane-a", "hold"),
		at(2, "owner-relay", "ask"),
		at(3, "root", "decide"),
		at(4, "lane-a", "incident"),
		at(5, "lane-a", "urgent"),
		withText(at(6, "lane-a", "note"), "OVERRIDE-WARNING cci #7 still counts"),
	} {
		if !everything.wakes(r) || everything.muteOf(r) >= 0 {
			t.Errorf("%s %s was hidden", r.Lane, r.Kind)
		}
	}
	if everything.wakes(at(8, stallWatch, "urgent")) {
		t.Error("a muted stall-watch urgent woke the coordinator")
	}
}

func TestAWindowOfOnlyMutedRecordsKeepsWaitingForOneThatWakes(t *testing.T) {
	v := view{mutes: []mute{kindMute("state")}}
	records := make(chan record)
	got := make(chan []record)
	go func() { got <- collect(context.Background(), records, short, forever, short, v.wakes) }()
	records <- at(1, "lane-a", "state")
	time.Sleep(3 * short)
	select {
	case batch := <-got:
		t.Fatalf("batch %+v closed on a muted record alone", batch)
	default:
	}
	records <- at(2, "lane-a", "ask")
	if batch := <-got; len(batch) != 2 {
		t.Fatalf("batch %+v, want the muted record and the one that woke it", batch)
	}
}

func TestAQuietWindowOfMutedRecordsStillEndsInsideOneBashCall(t *testing.T) {
	v := view{mutes: []mute{kindMute("state")}}
	batch := collect(context.Background(), arriving(at(1, "lane-a", "state")), short, 2*short, short, v.wakes)
	if len(batch) != 1 {
		t.Fatalf("batch %+v, want the muted record so its cursor moves", batch)
	}
	out, _ := shown(t, v, batch...)
	if !strings.HasPrefix(out, "muted 1: 1 STATE\n") {
		t.Fatalf("batch %q", out)
	}
}

func TestWidthCutsARecordAndNamesTheReadPastIt(t *testing.T) {
	out, _ := shown(t, view{width: 40}, withText(at(9, "lane-a", "ask"), strings.Repeat("é", 40)))
	if !strings.Contains(out, "cut ") || !strings.Contains(out, "cci show 9 --body") || strings.Contains(out, strings.Repeat("é", 40)) {
		t.Fatalf("batch %q", out)
	}
	if !strings.HasPrefix(out, "#9 ") || strings.ContainsRune(out, '�') {
		t.Fatalf("batch %q split a character", out)
	}
}

func TestLinesStopsAfterWhatCannotWaitAndSaysHowManyAreCut(t *testing.T) {
	out, whole := shown(t, view{lines: 2}, at(1, "lane-a", "answer"), at(2, "lane-b", "answer"), at(3, "owner-relay", "ask"), at(4, "lane-a", "hold"))
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if !strings.HasPrefix(lines[0], "#4 ") || !strings.HasPrefix(lines[1], "#3 ") {
		t.Fatalf("batch %q, want the hold then what cannot wait", out)
	}
	if !strings.Contains(out, "cut 2 records past --lines 2\n") || !strings.Contains(out, "whole batch: "+whole) {
		t.Fatalf("batch %q does not say what it cut", out)
	}
}

func TestFullPrintsABodyPastTheBatchLimit(t *testing.T) {
	body := strings.Repeat("x", bodyShown+1)
	out, _ := shown(t, view{full: []string{"ASK"}}, withBody(t, "plan attached", body))
	if !strings.Contains(out, body) {
		t.Fatalf("batch %d bytes, want the whole body", len(out))
	}
	long := withBody(t, "plan attached", strings.Repeat("x", fullShown+1))
	out, _ = shown(t, view{full: []string{"ask"}}, long)
	if strings.Contains(out, "xxx") || !strings.Contains(out, long.Refs.Path) {
		t.Fatalf("batch %d bytes, want a pointer past the bound", len(out))
	}
}

func TestWatchClosesWithQueueStateAndMovesItsLinesThere(t *testing.T) {
	landed := withText(at(1, "lane-a", "landed"), "merged #7")
	warning := withText(at(2, "landing-desk", "note"), "OVERRIDE-WARNING cci #3 still counts")
	warning.PRs = []int{7}
	other := withText(at(4, "lane-a", "landed"), "merged #77")
	out, _ := shown(t, view{watch: []int{7}, mutes: []mute{kindMute("landed")}}, landed, warning, other)
	if !strings.Contains(out, "#7 OPEN queue=queued") {
		t.Fatalf("batch %q lacks the queue state", out)
	}
	if strings.Count(out, "merged #7\n") != 1 || !strings.Contains(out, "  #1 ") || !strings.Contains(out, "  #2 ") {
		t.Fatalf("batch %q, want each watched line once, under its pull request", out)
	}
	if strings.Contains(out, "#4 ") || !strings.Contains(out, "muted 1: 1 LANDED\n") {
		t.Fatalf("batch %q, want #77's landing muted", out)
	}
}

func TestAWatchedRecordWakesThroughAMute(t *testing.T) {
	v := view{watch: []int{7}, mutes: []mute{kindMute("landed")}}
	if !v.wakes(withText(at(1, "lane-a", "landed"), "merged #7")) {
		t.Fatal("a watched landing stayed asleep")
	}
}

func TestAPullRequestWithNoStatusSaysSo(t *testing.T) {
	if got := statusLine(9, nil); got != "#9 no status" {
		t.Fatalf("got %q", got)
	}
}

func TestPlainBatchHasNoFooterAndWritesNoFile(t *testing.T) {
	out, whole := shown(t, view{}, at(1, "lane-a", "ask"))
	if strings.Count(strings.TrimSpace(out), "\n") != 0 {
		t.Fatalf("batch %q, want the one line", out)
	}
	if _, err := os.Stat(whole); !os.IsNotExist(err) {
		t.Fatalf("whole batch written: %v", err)
	}
}

func sessionDrive(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	state := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", home)
	t.Setenv(drive.Env, "prog")
	dir := filepath.Join(home, "long-running", "drives")
	if err := os.MkdirAll(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(drive.Entry{Drive: "prog", CCIDrive: testDrive, StateDir: state, Sessions: []string{"s1"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "prog.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return state
}

func runMute(t *testing.T, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd := muteCommand()
	cmd.SetOut(&out)
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs(args)
	err := cmd.ExecuteContext(context.Background())
	return out.String(), err
}

func TestTheMuteListIsKeptAndTheNextBatchReadsIt(t *testing.T) {
	state := sessionDrive(t)
	if _, err := runMute(t, "add", "--kind", "STATE", "--why", "registered lines"); err != nil {
		t.Fatal(err)
	}
	if _, err := runMute(t, "add", "--text", "DEADLINE", "--why", "desk clock"); err != nil {
		t.Fatal(err)
	}
	listed, err := runMute(t, "ls")
	if err != nil || listed != "1 kind state: registered lines\n2 text DEADLINE: desk clock\n" {
		t.Fatalf("ls = %q, %v", listed, err)
	}
	if _, err := runMute(t, "rm", "1"); err != nil {
		t.Fatal(err)
	}
	if _, err := runMute(t, "rm", "1"); err == nil {
		t.Fatal("removing a removed mute succeeded")
	}
	if _, err := runMute(t, "add", "--text", "(", "--why", "bad"); err == nil {
		t.Fatal("a bad pattern was kept")
	}
	mutes, err := loadMutes(filepath.Join(state, "root", mutesFile))
	if err != nil || len(mutes) != 1 || !mutes[0].hides(withText(at(1, "a", "note"), "DEADLINE soon")) {
		t.Fatalf("kept %+v, %v", mutes, err)
	}
	if _, err := runMute(t, "add", "--kind", "state", "--why", "x"); err != nil {
		t.Fatal(err)
	}
	if mutes, _ := loadMutes(filepath.Join(state, "root", mutesFile)); mutes[1].ID != 3 {
		t.Fatalf("a new mute reused an id: %+v", mutes)
	}
}

func TestABareNextReadsTheSessionsDriveAndItsCursor(t *testing.T) {
	state := sessionDrive(t)
	cursor := cursorAfterNow(t)
	ask := toRoot(t, client.Draft{Lane: "lane-a", Kind: "ask", Text: "which base?"})
	root := filepath.Join(state, "root")
	if err := os.MkdirAll(root, 0o750); err != nil {
		t.Fatal(err)
	}
	saved, err := os.ReadFile(cursor)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, cursorFile), saved, 0o600); err != nil {
		t.Fatal(err)
	}
	if out := runNextWith(t, "--every", (5 * short).String()); !printed(out, ask) {
		t.Fatalf("bare batch %q misses #%d", out, ask.Seq)
	}
	moved, err := os.ReadFile(filepath.Join(root, cursorFile))
	if err != nil || string(moved) == string(saved) {
		t.Fatalf("the default cursor did not move: %q, %v", moved, err)
	}
}
