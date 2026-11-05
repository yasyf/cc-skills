package update

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestOwnerWordsNameTheRecordThatCarriesEachAndFlagTheOnesNothingCarries(t *testing.T) {
	answer := record(12, "root", "go", "spawned five-fix-1", 20)
	answer.Re = 11
	resolving := record(15, "root", "decide", "gateway-2 owns it", 5)
	resolving.Resolves = []int64{14}
	sqlTab := record(19, "root", "go", "sql-tab-fix-1 owns it", 1)
	sqlTab.Re = 18
	long := "Fix the gateway, and keep every provider the platform supports working the whole time while you move the keys over, and tell nobody until it is live. Do not deploy."
	lines, gaps := ownerWords([]Record{
		record(11, bridge, "ask", "fix the five services blockers", 30),
		answer,
		record(13, bridge, "ask", "and the devbox", 15),
		record(14, bridge, "ask", "and the gateway", 10),
		resolving,
		record(16, bridge, "ask", "and #1 in the list", 4),
		record(17, "root", "go", "see #160 for the list", 3),
		record(18, bridge, "ask", "and the SQL tab", 2),
		sqlTab,
		record(20, bridge, "ask", long, 1),
		record(21, "root", "state", "PR #20 is still red", 0),
	})
	statuses := make([]string, len(lines))
	for i, line := range lines {
		_, statuses[i], _ = strings.Cut(line, " | ")
	}
	if want := []string{"carried by root #12", noCarrier, "carried by root #15", noCarrier, "carried by root #19", noCarrier}; !reflect.DeepEqual(statuses, want) {
		t.Fatalf("statuses = %v", statuses)
	}
	if !strings.HasSuffix(lines[0], `"fix the five services blockers" | carried by root #12`) {
		t.Fatalf("line = %q", lines[0])
	}
	if !strings.Contains(lines[5], `"`+long+`"`) {
		t.Fatalf("his words were clipped: %q", lines[5])
	}
	if want := []string{"owner words no record carries: #13", "owner words no record carries: #16", "owner words no record carries: #20"}; !reflect.DeepEqual(gaps, want) {
		t.Fatalf("gaps = %v", gaps)
	}
}

func TestSinceYouLastLookedNamesNewRowsAndWhatMovedAndNothingThatStayed(t *testing.T) {
	row := func(name, who string) section {
		return section{title: name, rows: []Row{{Name: name, Who: who, Blocks: nothingBlocks}}}
	}
	current := []section{row("Step 1", nobody), row("Step 2", "x-1"), row("Step 3", "y-1")}
	before := map[string][]string{"Step 1|Step 1": {"release-5", nothingBlocks, "open"}, "Step 2|Step 2": {"x-1", nothingBlocks, "open"}}
	if got, want := sinceLines(current, before, false), []string{"- Step 1: who release-5 -> **nobody**", "- new: Step 3"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("since = %v", got)
	}
	if got := sinceLines(current, takeSnapshot(current, now).Rows, false); len(got) != 0 {
		t.Fatalf("an unmoved update moved: %v", got)
	}
	left := map[string][]string{"Step 2|Step 2": {"x-1", nothingBlocks, stateOpen}, "Step 1|Step 1": {"x-1", nothingBlocks, stateOpen}, "Open PRs|#1940": {"fix-1", nothingBlocks, stateDone}}
	if got, want := sinceLines(current[1:2], left, false), []string{"- Step 1: no longer open (landed, answered or closed)"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("left = %v", got)
	}
}

func fyiRecord(seq int64, lane, text string, minutes int) Record {
	found := record(seq, lane, "note", text, minutes)
	found.Topic = fyiTopic
	return found
}

func TestFYIsAreOnlyTheBridgesNotesOnTopicFYIGroupedByTheWordsBeforeTheFirstColonNewestFirst(t *testing.T) {
	entries := fyiEntries([]Record{
		fyiRecord(1, bridge, "Bug fixes: the share link no longer waits on uploads", 50),
		fyiRecord(2, bridge, "A plain sentence with no group", 40),
		fyiRecord(3, "root", "Lanes: red-sweep-3 recycled", 30),
		record(4, bridge, "note", "not an fyi", 20),
		fyiRecord(5, bridge, "Bug fixes: the SQL tab opens again", 10),
	})
	want := []FYI{
		{5, ago(10), "Bug fixes", "the SQL tab opens again"},
		{2, ago(40), otherGroup, "A plain sentence with no group"},
		{1, ago(50), "Bug fixes", "the share link no longer waits on uploads"},
	}
	if !reflect.DeepEqual(entries, want) {
		t.Fatalf("entries = %+v", entries)
	}
	lines := fyiLines(entries, 0)
	if lines[0] != "- **Bug fixes**" || !strings.HasSuffix(lines[1], ": the SQL tab opens again") || !strings.HasSuffix(lines[2], ": the share link no longer waits on uploads") || lines[3] != "- **Other**" {
		t.Fatalf("lines = %v", lines)
	}
}

func TestAPinnedRowIsHeldByEveryWorkingLaneItsWordsNameAndByNobodyWhenNoneDoes(t *testing.T) {
	devbox := heard("devbox-perf-1", "desired", 10)
	build := heard("next-build-tester-2", "desired", 10)
	build.Deliverable = "run the next build"
	lanes := []Lane{devbox, build}
	pins := []drive.Pin{
		{Name: "Devbox speed and bug hunting", Delivers: "The perf harness and the creative tester", Words: []string{"DEVBOX"}},
		{Name: "Gateway", Delivers: "Every provider", Words: []string{"gateway"}},
	}
	rows, steps := viewOf(lanes, nil, nil).pinnedRows(Inputs{Pins: pins}, lanes)
	if rows[0].Who != "devbox-perf-1" || !reflect.DeepEqual(steps[0].Lanes, []string{"devbox-perf-1"}) {
		t.Fatalf("devbox = %+v, %+v", rows[0], steps[0])
	}
	if rows[1].Who != nobody || !rows[1].unowned() || len(steps[1].Lanes) != 0 {
		t.Fatalf("gateway = %+v, %+v", rows[1], steps[1])
	}
}

func firstCells(lines []string) []string {
	out := make([]string, len(lines))
	for i, line := range lines {
		out[i], _, _ = strings.Cut(line, " |")
	}
	return out
}

func TestAFullRowOnlyWhereABlockerOrAFailureLivesAndOneLineNamingTheRest(t *testing.T) {
	blocked := Row{"red-sweep-3", "reds", "desired", "red-sweep-3", "red-sweep-3 waits on the coordinator's answer to ask #9", "red-sweep-3: state 6:01 PM", noneRecorded, false}
	plain := Row{"flake-1", "flakes", "desired", "flake-1", nothingBlocks, "flake-1: state 6:05 PM: fixed", noneRecorded, false}
	quiet := Row{"old-lane-1", "x", "desired, last report 2:38 PM", nobody, nothingBlocks, "nobody is on it", "old-lane-1 quiet since 2:38 PM", false}
	step := Row{"Step 3", "3. The writer", "0 of 2 tasks done", "flake-1, red-sweep-3", nothingBlocks, "-", noneRecorded, false}
	closed := Row{"user-creds", "creds", "closed since 1:15 AM", nobody, nothingBlocks, "-", "-", true}
	red := Row{"#46107", "sand: unarchive a chat", "red", nobody, "held by cci #1", "-", "its head is red", false}
	held := Row{"#45623", "iris: a model pick", "red", "p8a-step7", "held by cci #2", "-", "-", false}
	quietLine := "Quiet over 2 h, nobody on them now: old-lane-1 (desired, last report 2:38 PM)"

	lanes := section{title: noStepTitle, rows: []Row{blocked, plain, quiet}}.lines(folding{})
	if got, want := firstCells(lanes[2:]), []string{"| red-sweep-3", "- flake-1: flake-1: state 6:05 PM: fixed", "", quietLine}; !reflect.DeepEqual(got, want) {
		t.Fatalf("lanes = %v", got)
	}
	steps := section{title: "Plan, step 3", rows: []Row{step, blocked, plain, quiet}, step: true}.lines(folding{})
	if got, want := firstCells(steps[2:]), []string{"| Step 3", "| red-sweep-3", "", quietLine}; !reflect.DeepEqual(got, want) {
		t.Fatalf("steps = %v", got)
	}
	if got, want := (section{title: incidentsTitle, rows: []Row{closed}}).lines(folding{}), []string{"", "Closed today: user-creds (closed since 1:15 AM)"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("incidents = %v", got)
	}
	if got, want := (section{title: openPRsTitle, rows: []Row{red, held}}).lines(folding{}), []string{"- red (2): #46107 sand: unarchive a chat; #45623 iris: a model pick [p8a-step7]"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("prs = %v", got)
	}
	if got := unownedLines([]section{{title: openPRsTitle, rows: []Row{red, held}}, {title: incidentsTitle, rows: []Row{closed}}}, folding{}); got[1] != "- Open PRs (1): #46107" || len(got) != 2 {
		t.Fatalf("unowned = %v", got)
	}
	if got, want := cell(strings.Repeat("x", 150)), strings.Repeat("x", 99)+"…"; got != want {
		t.Fatalf("cell = %q", got)
	}
}

func TestARowNeverClipsAnOwnerOffItsWhoCell(t *testing.T) {
	who := strings.TrimSuffix(strings.Repeat("a-long-lane-name-1, ", 10), ", ")
	if got := table([]Row{{Name: "Step 3", Who: who}})[2]; !strings.Contains(got, "| "+who+" |") {
		t.Fatalf("row = %q", got)
	}
}

func TestALaneRowSaysWhatWentWrongWithThePreventionOfEachOpenRetroRowItOwns(t *testing.T) {
	v := viewOf([]Lane{heard("release-5", "desired", 10)}, nil, nil)
	v.retro = []Retro{
		{"release-5", "A cancel mid-apply broke prod", "the release tool refuses a cancel after apply starts", "building", ago(-30)},
		{"release-5", "No mechanism yet", "", "open", ago(-30)},
		{"release-5", "Closed long ago", "fixed", retroLive, ago(60)},
		{"other-1", "Not this lane", "-", "open", ago(60)},
	}
	wrong := v.workOf([]string{"release-5"}).wrong
	if !strings.HasPrefix(wrong, "A cancel mid-apply broke prod, prevented by the release tool refuses a cancel after apply starts (building, due ") {
		t.Fatalf("wrong = %q", wrong)
	}
	if !strings.Contains(wrong, "No mechanism yet, prevented by no prevention yet (open, due ") || strings.Contains(wrong, "Closed long ago") || strings.Contains(wrong, "Not this lane") {
		t.Fatalf("wrong = %q", wrong)
	}
}

type retroLedger struct {
	ledger.Store
	rows []ledger.Row
}

func (l retroLedger) Rows(context.Context, string) ([]ledger.Row, error) {
	return l.rows, nil
}

func TestTheRetroTableIsItsLedgersRetroRowsLessDuplicatesAndNoneBeforeItsFirstRow(t *testing.T) {
	dir := t.TempDir()
	source := retroLedger{rows: []ledger.Row{
		{Key: "retro/000001", Fields: map[string]string{"owner": "release-5", "failure": "a cancel broke prod", "mechanism": "refuse it", "state": "building", "due": ago(-30).Format("2006-01-02T15:04:05Z07:00")}},
		{Key: "retro/000002", Fields: map[string]string{"owner": "release-5", "failure": "the same cancel", "dup_of": "retro/000001"}},
		{Key: "48401", Fields: map[string]string{"owner": "release-5", "failure": "not a retro row"}},
		{Key: "retro/000003", Fields: map[string]string{"owner": "x-1", "failure": "a due nobody could read", "due": "soon"}},
	}}
	if found, err := ReadRetro(context.Background(), source, dir); err != nil || found != nil {
		t.Fatalf("before its first row = %v, %v", found, err)
	}
	if err := os.WriteFile(filepath.Join(dir, retroFile), []byte(`{"ledger":"rl"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	found, err := ReadRetro(context.Background(), source, dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(found) != 2 || found[0].Failure != "a cancel broke prod" || !found[0].Due.Equal(ago(-30)) || found[1].Failure != "a due nobody could read" || !found[1].Due.IsZero() {
		t.Fatalf("found = %+v", found)
	}
}

func TestADashboardStepSaysItsBlockerNextStepAndFailuresInPlainWordsAndKeepsTheRowsCellsForTheDrillIn(t *testing.T) {
	row := Row{"Step 3", "3. Switch the chat writer", "1 of 2 tasks done, 1 in progress, 0 blocked", "chat-1, chat-2", "chat-1 waits on the coordinator's answer to ask #9", "chat-1: head 6:01 PM: pushed; next record due by 6:31 PM", noneRecorded, false}
	first := heard("chat-1", "desired", 5)
	first.PRs = []int{48701, 48700}
	second := heard("chat-2", "desired", 15)
	second.PRs = []int{48701}
	lanes := []Lane{first, second}
	wait := record(9, "chat-1", "ask", "which writer?", 20)
	v := viewOf(lanes, nil, []Record{wait})
	v.retro = []Retro{{Owner: "chat-2", Failure: "The writer shipped a layout no reader knew.", Mechanism: "readers first", State: "building"}}
	step := numbered([]Step{{}, v.stepEntry("3. Switch the chat writer", "Plan, step 3", row, lanes)})[1]
	if step.ID != "s1" || step.Label != "3. Switch" || step.Tone != "moving" || !reflect.DeepEqual(step.Who, []string{"chat-1", "chat-2"}) || !reflect.DeepEqual(step.PRs, []int{48700, 48701}) || !reflect.DeepEqual(step.Lanes, []string{"chat-1", "chat-2"}) {
		t.Fatalf("step = %+v", step)
	}
	if *step.Blocker != "Waiting on a coordinator decision since "+pacific(wait.At)+"." {
		t.Fatalf("blocker = %q", *step.Blocker)
	}
	if want := "2 workers are on it; the next word is due by " + pacific(ago(15).Add(store.StaleAfter)) + "."; step.Next != want || step.Unblocking != want {
		t.Fatalf("next = %q", step.Next)
	}
	if step.WentWrong != "The writer shipped a layout no reader knew. Prevention: readers first." {
		t.Fatalf("went wrong = %q", step.WentWrong)
	}
	if want := (StepDetail{row.Blocks, row.Unblock, noneRecorded}); step.Detail != want {
		t.Fatalf("detail = %+v", step.Detail)
	}
	idle := v.stepEntry("x", "s", Row{Who: nobody, Blocks: nothingBlocks}, nil)
	if idle.Tone != "later" || len(idle.Who) != 0 || idle.Blocker != nil || idle.Next != noWorker || idle.WentWrong != nothingYet {
		t.Fatalf("idle = %+v", idle)
	}
	done := v.stepEntry("x", "s", Row{Who: nobody, Blocks: nothingBlocks, Done: true}, nil)
	if done.Tone != stateDone || done.Next != "Done." || done.WentWrong != "Nothing open." {
		t.Fatalf("done = %+v", done)
	}
	planned := v.stepEntry("x", "s", Row{Who: nobody, Blocks: "task t waits on #1"}, nil)
	if *planned.Blocker != plannedWait {
		t.Fatalf("planned = %q", *planned.Blocker)
	}
}

func headings(text string) []string {
	var out []string
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "## ") {
			out = append(out, line)
		}
	}
	return out
}

func TestTheUpdateOpensWithWhatMovedAndHisWordsEndsWithItsGapsAndPublishesTheSnapshotItDiffsAgainst(t *testing.T) {
	in := inputs(t)
	answer := record(12, "root", "go", "spawned five-fix-1", 20)
	answer.Re = 11
	in.All = []Record{
		record(11, bridge, "ask", "fix the five services blockers", 30),
		answer,
		record(13, bridge, "ask", "and the devbox", 15),
		fyiRecord(15, bridge, "Bug fixes: the chat list fix ships in the next release", 25),
		fyiRecord(16, "root", "Lanes: board-refresher-7 recycled", 24),
	}
	in.Before = Snapshot{At: ago(120)}
	in.Pins = []drive.Pin{{Name: "The next build", Delivers: "Bugs fixed against open, and the platform team's flip back", Words: []string{"next-build"}}}
	in.Retro = []Retro{{"ship-2", "The five services dropped from two updates", "pieces rows from the plan", "building", ago(-60)}}
	in.Releases = []Record{record(14, "release-5", kindRelease, "llm-gateway live", 60)}

	report := Render(in)

	titles := headings(report.Text)
	want := []string{"## " + unownedStepsTitle, "## " + workstreamsTitle, "## Since you last looked (" + pacific(ago(120)) + ")", "## Decisions made for you (FYI)", "## Your words since the last update", "## Unowned work", "## Pinned for you"}
	if !reflect.DeepEqual(titles[:len(want)], want) {
		t.Fatalf("titles = %v", titles)
	}
	if titles[len(titles)-1] != "## Gaps in this update" || !strings.Contains(report.Text, "- owner words no record carries: #13") {
		t.Fatalf("gaps are not the last section:\n%s", report.Text)
	}
	for _, line := range []string{
		"- **Bug fixes**",
		": the chat list fix ships in the next release",
		"| The next build | Bugs fixed against open, and the platform team's flip back |",
		`"fix the five services blockers" | carried by root #12`,
		firstUpdate,
		"The five services dropped from two updates, prevented by pieces rows from the plan (building, due ",
	} {
		if !strings.Contains(report.Text, line) {
			t.Errorf("no %q in\n%s", line, report.Text)
		}
	}
	if strings.Contains(report.Text, "board-refresher-7 recycled") {
		t.Errorf("a coordinator's record became an FYI:\n%s", report.Text)
	}
	if left := Missing(report.Text, report.Needs); len(left) != 0 {
		t.Errorf("missing = %v", left)
	}
	if !reflect.DeepEqual(report.Gaps, []string{"owner words no record carries: #13"}) {
		t.Errorf("gaps = %v", report.Gaps)
	}

	dir := t.TempDir()
	steps := filepath.Join(dir, progressDesk, stepsFile)
	if err := report.Publish(dir, false); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(steps, []byte(`{"steps":[],"pr_state":{"48401":{"state":"Open"}},"history":{"lane:x-1":{"events":[]}}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := report.Publish(dir, false); err != nil {
		t.Fatal(err)
	}
	if before, err := ReadSnapshot(dir); err != nil || !before.At.IsZero() {
		t.Fatalf("publishing without sending saved a snapshot: %+v, %v", before, err)
	}
	data, err := os.ReadFile(steps)
	if err != nil {
		t.Fatal(err)
	}
	var published struct {
		Steps []map[string]any `json:"steps"`
		FYI   []map[string]any `json:"fyi"`
		Now   struct {
			Release map[string]any `json:"release"`
		} `json:"now"`
		PRState map[string]any `json:"pr_state"`
		History map[string]any `json:"history"`
	}
	if err := json.Unmarshal(data, &published); err != nil {
		t.Fatal(err)
	}
	if first := published.Steps[0]; first["id"] != "s0" || first["title"] != "The next build" || first["tone"] != "later" || first["went_wrong"] != nothingYet {
		t.Fatalf("first step = %v", first)
	}
	if len(published.FYI) != 1 || published.FYI[0]["text"] != "the chat list fix ships in the next release" {
		t.Fatalf("fyi = %v", published.FYI)
	}
	if published.Now.Release["id"] != "release:release-5" || published.Now.Release["name"] != "release-5" {
		t.Fatalf("now = %+v", published.Now)
	}
	if published.PRState["48401"] == nil || published.History["lane:x-1"] == nil {
		t.Fatalf("publishing dropped what the overview keeps: %+v, %+v", published.PRState, published.History)
	}

	if err := report.Publish(dir, true); err != nil {
		t.Fatal(err)
	}
	in.Before, err = ReadSnapshot(dir)
	if err != nil {
		t.Fatal(err)
	}
	_, body, _ := strings.Cut(Render(in).Text, "## Since you last looked")
	body, _, _ = strings.Cut(body, "\n## ")
	lines := strings.Split(strings.TrimSpace(body), "\n")
	if lines[len(lines)-1] != nothing {
		t.Fatalf("an unmoved update moved:\n%s", body)
	}
}

func TestADoneStepStillReachesTheDashboardAndTheSnapshot(t *testing.T) {
	report := Render(inputs(t))
	found := false
	for _, step := range report.Published.Steps {
		found = found || (step.Title == "The base" && step.Tone == "done")
	}
	if !found {
		t.Fatalf("steps = %+v", report.Published.Steps)
	}
	if got := report.Snapshot.Rows["The rebuild, The base|Step 1"]; !reflect.DeepEqual(got, []string{nobody, nothingBlocks, "done"}) {
		t.Fatalf("snapshot = %v", report.Snapshot.Rows)
	}
}

func TestTheFlagsOwnerUpdatePyCallersNameReachTheVerbs(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	for _, args := range [][]string{
		{"--drive", "absent", "--render"},
		{"--drive", "absent", "--render", "--publish"},
		{"--drive", "absent", "--render", "--sent"},
		{"--drive", "absent", "--check", "-"},
		{"--drive", "absent", "--json", "--only", "delivered", "--since", "2026-11-02T19:00:00Z", "--kind", "report"},
		{"render", "--drive", "absent", "--sent"},
		{"status", "--drive", "absent", "--json"},
	} {
		cmd := Command(&app.App{Now: func() time.Time { return now }})
		cmd.SetArgs(args)
		cmd.SilenceUsage, cmd.SilenceErrors = true, true
		err := cmd.Execute()
		if err == nil || !strings.Contains(err.Error(), "absent") {
			t.Errorf("%v: %v", args, err)
		}
	}
}
