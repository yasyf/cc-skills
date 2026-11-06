package waste

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx/ccxtest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var (
	testDrive = config.Config{Drive: "drive-1", Repo: "org/drive"}
	t0        = time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	inside    = t0.Add(-10 * time.Minute)
	earlier   = t0.Add(-90 * time.Minute)
)

type fakeBus struct {
	records []client.Record
	history []client.Record
	peers   []client.Peer
	merges  []client.Record
	tails   int
	whos    int
}

func (b *fakeBus) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	b.tails++
	if len(q.Kinds) > 0 {
		return b.merges, nil
	}
	if len(q.Lanes) == 0 {
		return b.records, nil
	}
	var kept []client.Record
	for _, record := range b.history {
		if slices.Contains(q.Lanes, record.Lane) {
			kept = append(kept, record)
		}
	}
	return kept, nil
}

func (b *fakeBus) Who(context.Context, string) ([]client.Peer, error) {
	b.whos++
	return b.peers, nil
}

type fakeRunner struct {
	files map[string]string
	lists int
	shows int
	owed  string
}

func (r *fakeRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[0] == "cc-slack" {
		return shell.Result{Stdout: cmp.Or(r.owed, `{"replies":[],"holder_session":""}`)}, nil
	}
	if c.Argv[1] == "list" {
		r.lists++
		var listed strings.Builder
		for path, content := range r.files {
			fmt.Fprintf(&listed, `{"path":%q,"mtime":%d,"size":%d}`+"\n", path, t0.Unix(), len(content))
		}
		return shell.Result{Stdout: listed.String()}, nil
	}
	r.shows++
	_, err := io.WriteString(c.Stdout, r.files[c.Argv[2]])
	return shell.Result{}, err
}

type world struct {
	bus     *fakeBus
	runner  *fakeRunner
	retired []store.Lane
	cfg     Config
	gh      map[string]string
	running *Running
}

func newWorld(t *testing.T) *world {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	cfg := defaults()
	cfg.HookLogs = t.TempDir()
	cfg.CodexSessions = t.TempDir()
	gh := map[string]string{tagCommits(lrTag): "", tagCommits(packTag): ""}
	running := &Running{CCX: &ccxtest.Fake{GHOut: gh}, Repo: "yasyf/cc-skills", LR: "0.9.1", Pack: "0.7.9", Since: t0.Add(-time.Hour)}
	return &world{bus: &fakeBus{}, runner: &fakeRunner{files: map[string]string{}}, cfg: cfg, gh: gh, running: running}
}

const (
	lrTag   = "lr--v0.9.1"
	packTag = "long-running--v0.7.9"
)

func tagCommits(tag string) string {
	return fmt.Sprintf("api --paginate repos/yasyf/cc-skills/commits?sha=%s&since=%s&per_page=100 --jq .[].commit.message | split(\"\\n\")[0]", tag, t0.Add(-48*time.Hour).UTC().Format(time.RFC3339))
}

func (w *world) released(number int, tags ...string) {
	for _, tag := range tags {
		w.gh[tagCommits(tag)] += fmt.Sprintf("lr: change %d (#%d)\n", number, number)
	}
}

func (w *world) sources() Sources {
	return Sources{
		Bus:     w.bus,
		Runner:  w.runner,
		Retired: func() ([]store.Lane, error) { return w.retired, nil },
		Running: w.running,
	}
}

func (w *world) read(t *testing.T, window time.Duration) Report {
	t.Helper()
	report, err := Read(context.Background(), w.sources(), w.cfg, testDrive, t0, window)
	if err != nil {
		t.Fatal(err)
	}
	return report
}

func (w *world) session(lane, session string, events ...string) {
	w.bus.peers = append(w.bus.peers, client.Peer{Session: session, Lane: lane})
	w.runner.files["/projects/p/"+session+".jsonl"] = strings.Join(events, "\n") + "\n"
}

func rec(at time.Time, lane, text string, to ...string) client.Record {
	return client.Record{Lane: lane, At: at, Text: text, To: to, Kind: "note"}
}

func ask(lane, text string) client.Record {
	return client.Record{Lane: lane, At: inside, Text: text, Kind: "ask"}
}

func signal(report Report, name string) []string {
	rows := []string{}
	for _, finding := range report.Findings {
		if finding.Signal == name {
			rows = append(rows, fmt.Sprintf("%s %d", finding.Who, finding.Size))
		}
	}
	return rows
}

func equal(t *testing.T, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func encoded(t any) string {
	raw, err := json.Marshal(t)
	if err != nil {
		panic(err)
	}
	return string(raw)
}

func assistant(at time.Time, request string, tokens int, calls ...any) string {
	return encoded(map[string]any{
		"kind":       "assistant",
		"meta":       map[string]any{"timestamp": at.Format(time.RFC3339Nano)},
		"request_id": request,
		"usage":      map[string]any{"input_tokens": tokens},
		"blocks":     calls,
	})
}

func call(name string, input map[string]any) map[string]any {
	return map[string]any{"type": "tool_use", "name": name, "input": input}
}

func bash(command string) map[string]any {
	return call("Bash", map[string]any{"command": command})
}

func readOf(path string) map[string]any {
	return call("Read", map[string]any{"file_path": path})
}

func hookContext(at time.Time, hook, text string) string {
	return encoded(map[string]any{
		"kind":            "attachment",
		"attachment_type": "hook_additional_context",
		"meta":            map[string]any{"timestamp": at.Format(time.RFC3339Nano)},
		"detail":          map[string]any{"hook_name": hook, "content": []string{text}},
	})
}

func attachment(at time.Time, class string, rendered any) string {
	raw := map[string]any{}
	if rendered != nil {
		raw["rendered"] = rendered
	}
	return encoded(map[string]any{
		"kind":            "attachment",
		"attachment_type": class,
		"meta":            map[string]any{"timestamp": at.Format(time.RFC3339Nano)},
		"detail":          map[string]any{"raw": raw},
	})
}

func TestDeadRecipientsRankByDeliveriesAndNameTheirEmitters(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Dead = 2
	w.cfg.Ignore = []string{"owner"}
	gone := t0.Add(-30 * time.Minute)
	w.retired = []store.Lane{{Name: "stood-down", Since: gone}}
	w.bus.records = []client.Record{
		rec(inside, "emit-a", "x", "never-posted"),
		rec(inside, "emit-a", "x", "never-posted"),
		rec(inside, "emit-a", "x", "never-posted"),
		rec(inside, "emit-b", "x", "never-posted"),
		rec(inside, "emit-a", "x", "stood-down"),
		rec(inside, "emit-a", "x", "stood-down"),
		rec(gone.Add(-time.Minute), "emit-a", "x", "stood-down"),
		rec(inside, "emit-a", "x", "quiet-but-alive"),
		rec(inside, "emit-a", "x", "quiet-but-alive"),
		rec(inside, "emit-a", "x", "owner"),
		rec(inside, "emit-a", "x", "owner"),
		rec(inside, "emit-a", "x", "emit-b"),
		rec(inside, "emit-a", "x", "emit-b"),
		rec(inside, "emit-a", "x", "once"),
	}
	w.bus.history = []client.Record{rec(earlier, "quiet-but-alive", "x"), rec(earlier, "stood-down", "x")}

	report := w.read(t, Window)

	equal(t, signal(report, signalDead), []string{"never-posted 4", "stood-down 2"})
	equal(t, report.Findings[0].Detail, "from emit-a 3, emit-b 1")
}

func TestARetiredRowDoesNotMakeALaneThatStillPostsDead(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Dead = 1
	gone := t0.Add(-30 * time.Minute)
	w.retired = []store.Lane{{Name: "root", Since: gone}, {Name: "really-gone", Since: gone}}
	w.bus.records = []client.Record{
		rec(inside, "emit", "x", "root"),
		rec(inside, "root", "still here"),
		rec(gone.Add(-time.Minute), "really-gone", "last words"),
		rec(inside, "emit", "x", "really-gone"),
	}

	equal(t, signal(w.read(t, Window), signalDead), []string{"really-gone 1"})
}

func TestFanOutCountsDeliveriesAnHourByEmitterAndShape(t *testing.T) {
	wide := make([]string, 10)
	for i := range wide {
		wide[i] = fmt.Sprintf("lane-%d", i)
	}
	records := []client.Record{
		rec(inside, "root", "4:00 AM root: Register classify, 1 rows: extended prompt/12", wide...),
		rec(inside, "root", "Register classify, 22 rows: extended rule/9", wide...),
		rec(inside, "desk", "a different wide record", wide[:9]...),
		rec(inside, "narrow", "under the recipient floor", wide[:7]...),
	}

	w := newWorld(t)
	w.cfg.Floors.FanOut = 5
	w.bus.records = records
	report := w.read(t, Window)
	equal(t, signal(report, signalFanOut), []string{"root 20", "desk 9"})

	w = newWorld(t)
	w.cfg.Floors.FanOut = 5
	w.bus.records = records
	equal(t, signal(w.read(t, 2*Window), signalFanOut), []string{"root 10"})
}

func TestInjectedContextRanksHooksAndClassesByCharacters(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Injected = 50
	large := strings.Repeat("reminder ", 40)
	small := strings.Repeat("s", 60)
	w.session("lane-1", "s-1",
		hookContext(inside, "PreToolUse:Bash", large),
		hookContext(inside, "PreToolUse:Bash", large),
		hookContext(inside, "SessionStart", "tiny"),
		hookContext(earlier, "PostToolUse", large),
		attachment(inside, "output_style", []map[string]string{{"content": small}}),
		attachment(inside, "hook_success", nil),
		attachment(inside, "prompt_snapshot", nil),
	)

	report := w.read(t, Window)

	who := make([]string, 0, len(report.Findings))
	for _, finding := range report.Findings {
		who = append(who, finding.Who)
	}
	equal(t, who, []string{"PreToolUse:Bash reminder reminder reminder reminder reminder reminder", "output_style"})
	if !strings.Contains(report.Findings[0].Detail, "2 injections") {
		t.Fatalf("detail %q does not count the injections", report.Findings[0].Detail)
	}
}

func TestHookSkipsCountThisWindowPerSessionAndHook(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Skips = 2
	w.session("lane-1", "s-1", assistant(inside, "r", 1))
	w.session("lane-2", "s-2", assistant(inside, "r", 1))
	w.session("lane-3", "s-3", assistant(inside, "r", 1))
	stamp := func(at time.Time) string { return at.In(time.Local).Format(logStamp) }
	skip := func(at time.Time, hook string) string {
		return fmt.Sprintf("%s WARNING captain_hook.worker.fail_open: hook skipped: evidence incomplete | {'hook': '%s', 'status': 'deadline'}\n", stamp(at), hook)
	}
	abandon := fmt.Sprintf("%s WARNING captain_hook.dispatch: caller deadline reached; abandoning this hook's verdict | {'hook': 'steer'}\n", stamp(inside))
	noise := fmt.Sprintf("%s INFO captain_hook.worker.runtime: dispatch | {'event': 'PreToolUse'}\n", stamp(inside))
	logs := map[string]string{
		"s-1": skip(earlier, "steer") + skip(inside, "steer") + noise + abandon + skip(inside, "steer") + "  File \"traceback.py\"\n" + skip(inside, "other"),
		"s-2": skip(inside, "steer"),
	}
	for session, log := range logs {
		if err := os.WriteFile(filepath.Join(w.cfg.HookLogs, session+".log"), []byte(log), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	report := w.read(t, Window)

	equal(t, signal(report, signalSkips), []string{"lane-1 4"})
	equal(t, report.Findings[0].Detail, "session s-1, steer 3, other 1")
}

func TestASessionLogIsReadBackAcrossChunksAndStopsAtTheWindow(t *testing.T) {
	path := filepath.Join(t.TempDir(), "s.log")
	line := func(at time.Time, rest string) string { return at.In(time.Local).Format(logStamp) + " " + rest + "\n" }
	skipped := "WARNING x: hook skipped: evidence incomplete | {'hook': 'steer'}"
	filler := strings.Repeat(line(inside, "INFO y: dispatch"), 60_000)
	log := line(earlier, skipped) + filler + line(inside, skipped) + line(inside, skipped) + filler
	if len(filler) < 2*logChunk {
		t.Fatalf("the filler is %d bytes, too few to cross a chunk", len(filler))
	}
	if err := os.WriteFile(path, []byte(log), 0o600); err != nil {
		t.Fatal(err)
	}

	hooks, err := sessionSkips(path, t0.Add(-Window))
	if err != nil {
		t.Fatal(err)
	}
	equal(t, hooks, map[string]int64{"steer": 2})
}

func TestASessionWithNoHookLogCountsNothing(t *testing.T) {
	hooks, err := sessionSkips(filepath.Join(t.TempDir(), "absent.log"), t0)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(hooks), 0)
}

func TestChoresRankRepeatedCommandShapesPerLane(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Chores = 2
	w.session("lane-1", "s-1", assistant(inside, "r1", 1,
		bash("gh pr view 12"),
		bash("cd /tmp/x && gh pr view 99 --json state"),
		bash("LR_LANE=x gh pr view 3"),
		bash("cci post --lane a"),
		bash("cci post --lane b"),
		bash("ls"),
	), assistant(earlier, "r0", 1, bash("ls"), bash("ls")))

	report := w.read(t, Window)

	equal(t, signal(report, signalChore), []string{"lane-1 3", "lane-1 2"})
	equal(t, report.Findings[0].Detail, `"gh pr view"`)
}

func TestARepeatReadNeedsNoInterveningChange(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Reads = 1
	w.session("repeats", "s-1",
		assistant(earlier, "r0", 1, readOf("/a/old.go")),
		assistant(inside, "r1", 1, readOf("/a/old.go"), readOf("/a/same.go"), readOf("/a/same.go"), readOf("/a/same.go")),
	)
	w.session("edits-between", "s-2", assistant(inside, "r1", 1,
		readOf("/a/same.go"),
		call("Edit", map[string]any{"file_path": "/a/same.go"}),
		readOf("/a/same.go"),
		call("Read", map[string]any{"file_path": "/a/same.go", "offset": 40}),
	))

	report := w.read(t, Window)

	equal(t, signal(report, signalRepeatRead), []string{"repeats 3"})
	equal(t, report.Findings[0].Detail, "unchanged: same.go 2, old.go 1")
}

func TestCostComparesEachLaneWithItsPreviousWindow(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Cost = 1000
	w.session("expensive", "s-1",
		assistant(earlier, "r0", 500),
		assistant(inside, "r1", 100),
		assistant(inside, "r1", 3000),
	)
	w.runner.files["/projects/p/s-1/subagents/agent-a.jsonl"] = assistant(inside, "r9", 1000) + "\n"
	w.session("cheap", "s-2", assistant(inside, "r2", 1500))
	w.session("idle", "s-3", assistant(inside, "r3", 10))
	w.session("unbound", "s-4", assistant(inside, "r4", 9_000_000))
	w.bus.peers = w.bus.peers[:3]
	w.bus.records = []client.Record{rec(inside, "expensive", "x"), rec(inside, "expensive", "x")}

	report := w.read(t, Window)

	equal(t, signal(report, signalCost), []string{"expensive 4000", "cheap 1500"})
	equal(t, report.Findings[0].Detail, "previous window 500/h, 2000 per record, 1 sessions")
	equal(t, report.Findings[1].Detail, "previous window 0/h, no records, 1 sessions")
	equal(t, w.runner.shows, 4)
}

func TestATeammateFileCountsToItsOwnLaneAndAHelperToItsSession(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Cost = 1000
	w.cfg.Floors.Skips = 1
	w.bus.peers = []client.Peer{
		{Session: "s-1", Agent: "aworker-1-abc", Lane: "worker-1"},
		{Session: "s-1", Lane: "owner-relay"},
		{Session: "s-1", Agent: "aworker-2-def", Lane: "worker-2"},
	}
	w.runner.files["/projects/p/s-1.jsonl"] = assistant(inside, "r1", 3000) + "\n"
	w.runner.files["/projects/p/s-1/subagents/agent-aworker-1-abc.jsonl"] = assistant(inside, "r2", 2000) + "\n" + assistant(inside, "r5", 3000) + "\n"
	w.runner.files["/projects/p/s-1/subagents/agent-aworker-2-def.jsonl"] = assistant(inside, "r3", 1500) + "\n"
	w.runner.files["/projects/p/s-1/subagents/agent-ahelper.jsonl"] = assistant(inside, "r4", 500) + "\n"
	log := inside.In(time.Local).Format(logStamp) + " WARNING x: hook skipped: evidence incomplete | {'hook': 'steer'}\n"
	if err := os.WriteFile(filepath.Join(w.cfg.HookLogs, "s-1.log"), []byte(log), 0o600); err != nil {
		t.Fatal(err)
	}

	report := w.read(t, Window)

	equal(t, signal(report, signalCost), []string{"worker-1 5000", "owner-relay 3500", "worker-2 1500"})
	equal(t, signal(report, signalSkips), []string{"owner-relay 1"})
}

func TestDuplicateForwardsNeedTheSameWordsFromTwoLanes(t *testing.T) {
	w := newWorld(t)
	said := `"also why am I suggesting these things you should be figuring this out"`
	w.bus.records = []client.Record{
		ask("owner-relay", "3:51 AM owner-relay: owner m37, verbatim: "+said),
		ask("meta-desk-2", "meta-desk-2: forwarded for root "+strings.ToUpper(said)),
		ask("solo", "solo: the same lane asks a question twice over"),
		ask("solo", "solo: the same lane asks a question twice over"),
		ask("first", "4:00 AM first: please merge the stack when green"),
		ask("second", "4:01 AM second: please merge the stack when green"),
	}
	w.bus.records[1].Kind = "lr.ask"
	note := rec(inside, "third", "4:02 AM third: please merge the stack when green")
	w.bus.records = append(w.bus.records, note)

	report := w.read(t, Window)

	equal(t, signal(report, signalDuplicate), []string{"meta-desk-2 1, owner-relay 1 2", "first 1, second 1 2"})
}

func TestALaneWarningAboutARaceOrAClobberAsksForAFix(t *testing.T) {
	w := newWorld(t)
	w.cfg.Lane = "meta-desk"
	relay := rec(inside, "owner-relay", "a lane warning about clobbering opens a fix")
	relay.Kind = "ask"
	own := rec(inside, "supervisor", "race-warning 1 warnings lane-1")
	own.Kind = "lr.note"
	w.bus.records = []client.Record{
		{Seq: 7, Lane: "lane-1", At: inside, Kind: "report", Text: "4:00 AM lane-1: two writers would clobber the board"},
		{Seq: 9, Lane: "lane-1", At: inside, Kind: "report", Text: "4:10 AM lane-1: two writers would clobber the row"},
		{Seq: 8, Lane: "lane-2", At: inside, Kind: "blocker", Text: "the poster is racing the desk"},
		rec(inside, "lane-3", "go test -race passed and the trace is clean, no brace missing"),
		rec(inside, "lane-4", "the race-warning signal shipped"),
		rec(inside, "meta-desk", "I saw the clobber"),
		relay,
		own,
	}

	report := w.read(t, Window)

	equal(t, signal(report, signalRace), []string{"lane-1 2", "lane-2 1"})
	equal(t, report.Findings[0].Detail, `open a fix, not a report: #9 "two writers would clobber the row"`)
}

func merged(t *testing.T, at time.Time, lane, kind, pr string, attrs map[string]string) client.Record {
	t.Helper()
	var record client.Record
	raw := encoded(map[string]any{
		"lane":   lane,
		"kind":   kind,
		"at":     at.Format(time.RFC3339),
		"refs":   map[string]any{"prs": []string{pr}},
		"fields": map[string]any{"attrs": attrs},
	})
	if err := json.Unmarshal([]byte(raw), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func TestAMergedToolingPRWithNoInstallIsFlaggedAfterHalfAnHour(t *testing.T) {
	w := newWorld(t)
	old := t0.Add(-45 * time.Minute)
	proof := map[string]string{"installed": "0.7.1198", "proof": "saw it run"}
	w.bus.merges = []client.Record{
		merged(t, t0.Add(-3*time.Hour), "meta-helper-1", "landed", "yasyf/cc-skills#1", nil),
		merged(t, old, "meta-helper-2", "landed", "yasyf/cc-skills#2", nil),
		merged(t, old, "meta-helper-3", "done", "yasyf/cc-skills#3", nil),
		merged(t, t0.Add(-10*time.Minute), "meta-helper-4", "landed", "yasyf/cc-skills#4", nil),
		merged(t, old, "meta-helper-5", "landed", "yasyf/cc-skills#5", nil),
		merged(t, old.Add(time.Minute), "meta-desk", "done", "yasyf/cc-skills#5", proof),
		merged(t, old, "meta-helper-6", "landed", "yasyf/cc-skills#6", nil),
		merged(t, old.Add(time.Minute), "meta-desk", "done", "6", proof),
		merged(t, old, "drive-lane-1", "landed", "48043", nil),
		merged(t, old, "drive-lane-2", "landed", "org/drive#48044", nil),
	}

	report := w.read(t, Window)

	equal(t, signal(report, signalUninstalled), []string{"meta-helper-1 180", "meta-helper-2 45", "meta-helper-3 45"})
	if !strings.Contains(report.Findings[0].Detail, "yasyf/cc-skills#1 merged") {
		t.Fatalf("detail %q does not name the PR", report.Findings[0].Detail)
	}
}

func TestAMergeTheRunningLRAndPackBothHoldOwesOnlyItsProof(t *testing.T) {
	w := newWorld(t)
	old := t0.Add(-45 * time.Minute)
	w.released(1, lrTag, packTag)
	w.released(2, packTag)
	w.released(3, lrTag)
	w.bus.merges = []client.Record{
		merged(t, old, "meta-helper-1", "landed", "yasyf/cc-skills#1", nil),
		merged(t, old, "meta-helper-2", "landed", "yasyf/cc-skills#2", nil),
		merged(t, old, "meta-helper-3", "landed", "yasyf/cc-skills#3", nil),
		merged(t, old, "meta-helper-4", "landed", "yasyf/cc-skills#4", nil),
		merged(t, old, "meta-helper-5", "landed", "yasyf/cc-skills#5", nil),
		merged(t, old.Add(time.Minute), "meta-desk", "done", "yasyf/cc-skills#5", map[string]string{"installed": "0.7.9", "proof": "saw it run"}),
	}

	report := w.read(t, Window)

	equal(t, signal(report, signalUnproven), []string{"meta-helper-1 45"})
	equal(t, signal(report, signalUninstalled), []string{"meta-helper-2 45", "meta-helper-3 45", "meta-helper-4 45"})
	for _, finding := range report.Findings {
		if finding.Signal == signalUnproven {
			equal(t, finding.Detail, "yasyf/cc-skills#1 installed in lr 0.9.1 and pack 0.7.9 since 3:00 AM, proof owed")
		}
	}

	w.running.CCX = &ccxtest.Fake{GHOut: map[string]string{}}
	equal(t, signal(w.read(t, Window), signalUnproven), []string{"meta-helper-1 45"})
}

func TestTheUnprovenSignalReadsEachRunningTagOnceAtAnyMergeCount(t *testing.T) {
	reads := func(merges int) int {
		w := newWorld(t)
		fake := &ccxtest.Fake{GHOut: w.gh}
		w.running.CCX = fake
		for number := 1; number <= merges; number++ {
			w.released(number, lrTag, packTag)
			w.bus.merges = append(w.bus.merges, merged(t, t0.Add(-45*time.Minute), fmt.Sprintf("meta-helper-%d", number), "landed", fmt.Sprintf("yasyf/cc-skills#%d", number), nil))
		}
		equal(t, len(signal(w.read(t, Window), signalUnproven)), merges)
		return len(fake.Calls())
	}

	equal(t, reads(1), reads(100))
	equal(t, reads(1), 2)
}

func TestAnUninstalledMergeRepostsAsItAges(t *testing.T) {
	w := newWorld(t)
	w.bus.merges = []client.Record{merged(t, t0.Add(-30*time.Minute), "meta-helper-1", "landed", "yasyf/cc-skills#1", nil)}
	p, posts := poster(t, w)

	pass(t, p)
	equal(t, len(*posts), 1)

	w.bus.merges = []client.Record{merged(t, t0.Add(-59*time.Minute), "meta-helper-1", "landed", "yasyf/cc-skills#1", nil)}
	pass(t, p)
	equal(t, len(*posts), 1)

	w.bus.merges = []client.Record{merged(t, t0.Add(-time.Hour), "meta-helper-1", "landed", "yasyf/cc-skills#1", nil)}
	pass(t, p)
	equal(t, len(*posts), 2)
}

func TestAnOwnerReplyWithNoAnswerIsFlaggedAfterTwoMinutes(t *testing.T) {
	w := newWorld(t)
	w.bus.peers = []client.Peer{
		{Session: "s-1", Agent: "aworker-1-abc", Lane: "worker-1"},
		{Session: "s-1", Lane: "owner-relay"},
	}
	reply := func(age time.Duration, text string) map[string]string {
		sent := t0.Add(-age)
		return map[string]string{"ts": fmt.Sprintf("%d.%06d", sent.Unix(), 131059), "text": text, "permalink": "https://slack/" + text}
	}
	w.runner.owed = encoded(map[string]any{
		"holder_session": "s-1",
		"replies":        []map[string]string{reply(time.Minute, "fresh"), reply(10*time.Minute, "oldest"), reply(3*time.Minute, "older")},
	})

	report := w.read(t, Window)

	equal(t, signal(report, signalOwedReply), []string{"owner-relay 10", "owner-relay 3"})
	equal(t, report.Findings[0].Detail, `https://slack/oldest "oldest"`)
}

func TestSignalsRankTogetherByHowFarPastTheirFloor(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Dead = 10
	w.cfg.Floors.Chores = 2
	calls := make([]any, 0, 10)
	for range 10 {
		calls = append(calls, bash("gh run view"))
	}
	w.session("lane-1", "s-1", assistant(inside, "r", 1, calls...))
	for range 20 {
		w.bus.records = append(w.bus.records, rec(inside, "emit", "x", "dead-1"))
	}

	report := w.read(t, Window)

	equal(t, []string{report.Findings[0].Signal, report.Findings[1].Signal}, []string{signalChore, signalDead})
}

func crowded(t *testing.T, lanes int) *world {
	t.Helper()
	w := newWorld(t)
	w.cfg.Transcripts = 50
	w.cfg.Floors = Floors{Dead: 1, FanOut: 1, Injected: 1, Skips: 1, Chores: 1, Reads: 1, Cost: 1, Duplicates: 2, Races: 1, Uninstalled: 30, OwedReplies: 2, Compactions: 6}
	everyone := make([]string, lanes)
	for i := range lanes {
		lane := fmt.Sprintf("a-lane-with-a-fairly-long-name-%d", i)
		everyone[i] = lane
		w.session(lane, fmt.Sprintf("session-%d", i),
			assistant(inside, "r", 1000+i, bash(fmt.Sprintf("tool-%d run", i)), readOf("/a/f.go"), readOf("/a/f.go")),
			hookContext(inside, "PreToolUse:Bash", fmt.Sprintf("hook number %d says", i)),
		)
		w.bus.records = append(w.bus.records, rec(inside, "emit", fmt.Sprintf("record %d", i), "dead-"+lane))
	}
	w.bus.records = append(w.bus.records, rec(inside, "root", "broadcast to all", everyone...))
	return w
}

func TestOutputStaysUnderTheCapWithFlatReadsAtOneAndManyLanes(t *testing.T) {
	one, many := crowded(t, 1), crowded(t, scaletest.N())
	small := one.read(t, Window)
	large := many.read(t, Window)

	for _, report := range []Report{small, large} {
		text, shown := Render(report.Findings, report.note())
		if len(text) >= ByteCap {
			t.Fatalf("the report is %d bytes", len(text))
		}
		if shown == 0 {
			t.Fatal("nothing fit under the cap")
		}
	}
	equal(t, []int{many.bus.tails, many.bus.whos, many.runner.lists}, []int{one.bus.tails, one.bus.whos, one.runner.lists})
	equal(t, one.runner.shows, 1)
	equal(t, many.runner.shows, many.cfg.Transcripts)
	text, _ := Render(large.Findings, large.note())
	if !strings.Contains(text, fmt.Sprintf("read the 50 largest of %d changed transcripts", scaletest.N())) || !strings.Contains(text, " rows\n") {
		t.Fatalf("the report does not say what it left out:\n%s", text)
	}
}

type posted struct {
	to   []string
	body bus.Body
}

func poster(t *testing.T, w *world) (*Poster, *[]posted) {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	posts := &[]posted{}
	return &Poster{
		Sources: w.sources(),
		Config:  w.cfg,
		Store:   s,
		Drive:   testDrive,
		Now:     func() time.Time { return t0 },
		Post: func(to []string, body bus.Body) error {
			*posts = append(*posts, posted{to, body})
			return nil
		},
	}, posts
}

func pass(t *testing.T, p *Poster) {
	t.Helper()
	if err := p.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func deliveries(count int, to string) []client.Record {
	records := make([]client.Record, count)
	for i := range records {
		records[i] = rec(inside, "emit", "x", to)
	}
	return records
}

func TestAQuietWindowPostsNothing(t *testing.T) {
	w := newWorld(t)
	w.session("lane-1", "s-1", assistant(inside, "r", 10, bash("ls")))
	w.bus.records = deliveries(1, "dead-1")
	p, posts := poster(t, w)

	pass(t, p)

	equal(t, len(*posts), 0)
}

func TestDeliveriesToAStemWhoseLaneStillPostsAreNeverDead(t *testing.T) {
	w := newWorld(t)
	w.cfg.Lane = "meta-desk"
	if _, err := client.Post(context.Background(), client.Draft{Drive: testDrive.Drive, Lane: "desk-3", Kind: "state", Text: "desk-3 up"}); err != nil {
		t.Fatal(err)
	}
	w.bus.records = append(deliveries(30, "desk"), rec(inside, "desk-3", "working"))
	p, posts := poster(t, w)

	pass(t, p)

	equal(t, len(*posts), 0)
}

func TestAFindingPostsOnceToTheMetaLaneThenWaitsUntilItGrows(t *testing.T) {
	w := newWorld(t)
	w.cfg.Lane = "meta-desk"
	w.bus.records = deliveries(30, "dead-1")
	p, posts := poster(t, w)

	pass(t, p)
	equal(t, len(*posts), 1)
	equal(t, (*posts)[0].to, []string{"meta-desk"})
	if text := (*posts)[0].body.(bus.Note).Text; !strings.Contains(text, "dead-recipient 30 deliveries dead-1") {
		t.Fatalf("the note reads %q", text)
	}

	pass(t, p)
	equal(t, len(*posts), 1)

	w.bus.records = deliveries(59, "dead-1")
	pass(t, p)
	equal(t, len(*posts), 1)

	w.bus.records = deliveries(60, "dead-1")
	pass(t, p)
	equal(t, len(*posts), 2)

	w.bus.records = deliveries(20, "dead-1")
	pass(t, p)
	equal(t, len(*posts), 2)
}

func TestANoteTooLongForItsHeadKeepsEveryRowInItsBody(t *testing.T) {
	w := newWorld(t)
	w.cfg.Lane = "meta-desk"
	const rows = 40
	for i := range rows {
		w.bus.records = append(w.bus.records, deliveries(30, fmt.Sprintf("a-dead-lane-whose-name-runs-long-enough-that-forty-rows-overflow-the-head-%02d", i))...)
	}
	p, _ := poster(t, w)
	sender := bus.Bus{Drive: testDrive.Drive, Lane: "supervisor", Bodies: t.TempDir()}
	p.Post = func(to []string, body bus.Body) error {
		_, err := sender.Post(context.Background(), to, body)
		return err
	}

	pass(t, p)

	notes, err := client.Tail(context.Background(), client.Query{Drive: testDrive.Drive, Kinds: []string{"lr.note"}})
	if err != nil || len(notes) != 1 {
		t.Fatalf("notes = %+v, %v", notes, err)
	}
	if len(notes[0].Text) >= ByteCap || !strings.Contains(notes[0].Text, fmt.Sprintf(" more of %d rows\n", rows)) {
		t.Fatalf("the head is %d bytes and reads %q", len(notes[0].Text), notes[0].Text)
	}
	body, err := os.ReadFile(notes[0].Refs.Path)
	if err != nil {
		t.Fatal(err)
	}
	for i := range rows {
		if !strings.Contains(string(body), fmt.Sprintf("-head-%02d: from emit 30\n", i)) {
			t.Fatalf("row %d is missing from the body:\n%s", i, body)
		}
	}
}

func TestAPostCarriesOnlyWhatIsNewOrGrown(t *testing.T) {
	w := newWorld(t)
	w.bus.records = deliveries(30, "dead-1")
	p, posts := poster(t, w)
	pass(t, p)

	w.bus.records = append(deliveries(30, "dead-1"), deliveries(25, "dead-2")...)
	pass(t, p)

	equal(t, len(*posts), 2)
	text := (*posts)[1].body.(bus.Note).Text
	if strings.Contains(text, "dead-1") || !strings.Contains(text, "dead-2") {
		t.Fatalf("the second note reads %q", text)
	}
}

func TestAFindingCutByTheCapPostsNextTime(t *testing.T) {
	w := newWorld(t)
	w.cfg.Floors.Dead = 1
	for i := range 200 {
		w.bus.records = append(w.bus.records, rec(inside, "emit", "x", fmt.Sprintf("dead-lane-with-a-long-enough-name-%d", i)))
	}
	p, posts := poster(t, w)

	pass(t, p)
	pass(t, p)

	equal(t, len(*posts), 2)
	first, second := (*posts)[0].body.(bus.Note).Text, (*posts)[1].body.(bus.Note).Text
	for line := range strings.SplitSeq(strings.TrimSpace(second), "\n") {
		if strings.HasPrefix(line, signalDead) && strings.Contains(first, line) {
			t.Fatalf("%q posted twice", line)
		}
	}
}

func TestAFailedPostMarksNothing(t *testing.T) {
	w := newWorld(t)
	w.bus.records = deliveries(30, "dead-1")
	p, posts := poster(t, w)
	refusal := errors.New("the bus refused")
	working := p.Post
	p.Post = func([]string, bus.Body) error { return refusal }
	if err := p.Pass(context.Background()); !errors.Is(err, refusal) {
		t.Fatalf("got %v", err)
	}

	p.Post = working
	pass(t, p)

	equal(t, len(*posts), 1)
}

func TestConfigOverridesTheFloorsItNamesAndKeepsTheRest(t *testing.T) {
	t.Setenv("CAPTAIN_HOOK_LOG_DIR", "")
	t.Setenv("XDG_CACHE_HOME", "/cache")
	state := t.TempDir()

	cfg, err := ReadConfig(state)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, []any{cfg.Lane, cfg.Floors.Dead, cfg.HookLogs}, []any{"meta", defaults().Floors.Dead, "/cache/captain-hook/logs"})

	runner := `{"waste": {"lane": "meta-desk", "ignore": ["owner"], "growth": 3, "hook_logs": "/logs", "floors": {"dead": 7}}}`
	if err := os.WriteFile(filepath.Join(state, "runner.json"), []byte(runner), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err = ReadConfig(state)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, []any{cfg.Lane, cfg.Ignore, cfg.Growth, cfg.HookLogs, cfg.Floors.Dead, cfg.Floors.Chores}, []any{"meta-desk", []string{"owner"}, 3.0, "/logs", int64(7), defaults().Floors.Chores})

	t.Setenv("CAPTAIN_HOOK_LOG_DIR", "/env-logs")
	if err := os.Remove(filepath.Join(state, "runner.json")); err != nil {
		t.Fatal(err)
	}
	cfg, err = ReadConfig(state)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, cfg.HookLogs, "/env-logs")
}

func TestATranscriptThatDoesNotParseNamesItsFile(t *testing.T) {
	runner := &fakeRunner{files: map[string]string{"/t.jsonl": "{not json\n" + strings.Repeat("x", 1<<20)}}

	_, err := readTranscripts(context.Background(), runner, []transcript{{Path: "/t.jsonl", lane: "l"}}, t0, time.Hour)

	if err == nil || !strings.Contains(err.Error(), "/t.jsonl") {
		t.Fatalf("err %v", err)
	}
}
