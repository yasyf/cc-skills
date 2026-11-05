package update

import (
	"fmt"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const testDrive = "d1"

var now = time.Date(2026, 11, 2, 19, 0, 0, 0, time.UTC)

func ago(minutes int) time.Time {
	return now.Add(-time.Duration(minutes) * time.Minute)
}

func record(seq int64, lane, kind, text string, minutes int) Record {
	return Record{Seq: seq, Drive: testDrive, Lane: lane, Kind: kind, Text: text, At: ago(minutes), To: []string{"root"}}
}

func heard(name, state string, minutes int) Lane {
	return Lane{Name: name, State: state, Heard: ago(minutes), Brief: "/b/" + name + ".md"}
}

func viewOf(lanes []Lane, latest []Record, waits []Record) view {
	return newView(lanes, latest, waits, now)
}

func TestTheUpdateNamesEveryLanePostingWithinADayRegisteredOrNotAndEveryStandingOne(t *testing.T) {
	memory := heard("memory-siblings-2", "desired", 60)
	memory.Step = "memory"
	perf := Lane{Name: "devbox-perf-1", State: "launching", Standing: "devbox perf and bug hunting"}
	explore := heard("devbox-explore-7", "retired", 600)
	explore.Standing = "devbox perf and bug hunting"
	lanes := []Lane{memory, heard("red-sweep-3", "unregistered", 30), heard("root", "unregistered", 1), heard("cohort-flag-1", "desired", 30*60), perf, explore, heard("old-lane-1", "retired", 5)}
	want := []string{"memory-siblings-2", "red-sweep-3", "devbox-perf-1"}
	if got := namesOf(working(lanes, now)); !reflect.DeepEqual(got, want) {
		t.Fatalf("working = %v", got)
	}
}

func TestALaneSitsUnderThePlanStepItsBriefNamesByNumberAndItsSubSteps(t *testing.T) {
	step := &plans.Step{Number: "7", Title: "7. B3: the reader and writer"}
	for named, want := range map[string]bool{"7": true, "7.7": true, "17": false, "reader": false, "all": false, "": false} {
		if got := onStep(Lane{Step: named}, step); got != want {
			t.Errorf("step %q = %v, want %v", named, got, want)
		}
	}
}

func TestAStandingDutyReadsTheEndOfItsSuccessorChainAndNoSuccessorOnceThatEndRetiredAlone(t *testing.T) {
	devbox := heard("devbox-explore-7", "retired", 600)
	devbox.Standing, devbox.RetiredBy = "devbox perf and bug hunting", "done"
	old := heard("board-refresher-6", "retired", 90)
	old.Standing, old.RetiredBy = "refresh the board", "board-refresher-7"
	fresh := heard("board-refresher-7", "desired", 2)
	fresh.Standing = "refresh the board"
	rows := viewOf([]Lane{devbox, old, fresh}, nil, nil).dutyRows()
	if len(rows) != 2 {
		t.Fatalf("rows = %+v", rows)
	}
	first, second := rows[0], rows[1]
	if first.Who != nobody || first.Blocks != "devbox-explore-7 retired by done with no successor" || first.Unblock != "the coordinator launches its successor from /b/devbox-explore-7.md this turn" || !first.unowned() {
		t.Fatalf("devbox row = %+v", first)
	}
	if second.Name != "refresh the board" || second.Who != "board-refresher-7" || second.unowned() {
		t.Fatalf("board row = %+v", second)
	}
}

func TestWorkIsHeldOnlyByLanesHeardWithinTwoHoursWithTheirNextRecordDueAndRootWaitsNamed(t *testing.T) {
	v := viewOf(
		[]Lane{heard("release-5", "desired", 10), heard("old-desk-1", "desired", 300), heard("root", "unregistered", 1)},
		[]Record{record(7, "release-5", "head", "pushed #48700", 10)},
		[]Record{record(8, "release-5", "blocker", "needs the owner's word on deletes", 5)},
	)
	w := v.workOf([]string{"release-5", "old-desk-1", "root"})
	if w.who != "release-5" || !strings.HasPrefix(w.blocks, "release-5 waits on the coordinator's answer to blocker #8 since ") {
		t.Fatalf("work = %+v", w)
	}
	if !strings.HasPrefix(w.unblock, "release-5: head ") || !strings.Contains(w.unblock, "; next record due by ") {
		t.Fatalf("unblock = %q", w.unblock)
	}
	if !strings.HasPrefix(w.wrong, "old-desk-1 quiet since ") {
		t.Fatalf("wrong = %q", w.wrong)
	}
	idle := v.workOf([]string{"old-desk-1", "root"})
	if idle.who != nobody || idle.unblock != "nobody is on it, so the coordinator starts a lane on it this turn" {
		t.Fatalf("idle = %+v", idle)
	}
}

func TestARowTellsARecordsTimeFromItsStampWhetherItsTextCarriesNoClockOrAStaleOne(t *testing.T) {
	v := viewOf(
		[]Lane{heard("release-5", "desired", 10), heard("relay-6", "desired", 10)},
		[]Record{record(7, "release-5", "head", "pushed #48700", 25), record(9, "relay-6", "head", "2:13 AM relay-6: pushed #48701", 25)},
		nil,
	)
	for _, lane := range []string{"release-5", "relay-6"} {
		if got, want := v.workOf([]string{lane}).unblock, lane+": head "+pacific(ago(25))+": "; !strings.HasPrefix(got, want) {
			t.Fatalf("unblock = %q, want prefix %q", got, want)
		}
	}
}

func TestAPlanWithNoNumberedStepsRowsEachPieceItsTitleListsAndALaneJoinsAPieceByItsWord(t *testing.T) {
	plan := &plans.Plan{Title: "SandSQL five pieces: ssql-server (two modes), ssql-replicator, ssql-signal; migration R0-R8", Steps: []*plans.Step{{Number: plans.Unmapped, Title: "No step label"}}}
	if got, want := pieces(plan), []string{"ssql-server", "ssql-replicator", "ssql-signal"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("pieces = %v", got)
	}
	if !onPiece(Lane{Name: "uploads-to-replicator-1"}, "ssql-replicator") || !onPiece(Lane{Name: "x-1", Deliverable: "read the sandsql-server supervisor"}, "ssql-server") {
		t.Fatal("a lane did not join its piece")
	}
	if onPiece(Lane{Name: "signal-valkey-2"}, "ssql-replicator") {
		t.Fatal("a lane joined another piece")
	}
}

func TestAStepWithEveryTaskDoneIsNeverUnownedAndAnOpenOneWithNobodyIs(t *testing.T) {
	v := viewOf(nil, nil, nil)
	done := v.stepRow("Step 2", "2. Data", &plans.Step{Tasks: []plans.Task{{Title: "Port the consumer", State: plans.Done}}}, nil)
	open := v.stepRow("Step 0", "0. The base", &plans.Step{Tasks: []plans.Task{{Title: "Port the consumer", State: plans.Done}, {Title: "Port the consumer", State: plans.Blocked, Waiting: "#45723 review"}}}, nil)
	if done.unowned() || !open.unowned() {
		t.Fatalf("done = %+v, open = %+v", done, open)
	}
	if open.State != "1 of 2 tasks done, 0 in progress, 1 blocked" || open.Blocks != "task Port the consumer waits on #45723 review" {
		t.Fatalf("open = %+v", open)
	}
}

func TestATableCellNeverBreaksItsRow(t *testing.T) {
	got := table([]Row{{"#48401", "a | b", "red", nobody, "waits: red", "x", "its head is red", false}})[2]
	if want := "| #48401 | a / b | red | **nobody** | waits: red | x | its head is red |"; got != want {
		t.Fatalf("row = %q", got)
	}
}

func TestAnIncidentRecordIsFiledUnderTheSlugItOpensElseItsLaneElseTheLaneACoordinatorAddresses(t *testing.T) {
	cases := []struct {
		lane, text string
		to         []string
		want       string
	}{
		{"root", "6:11 PM root INCIDENT OPEN ssql-cred-rotation: prod errors", []string{"dev-health-desk-1"}, "ssql-cred-rotation"},
		{"root", "6:25 AM root: user-creds closed", []string{"drive-watchdog-2@d1"}, "drive-watchdog-2"},
		{"run-creds-fix-2", "PR up", []string{"root"}, "run-creds-fix-2"},
	}
	for _, c := range cases {
		if got := incidentName(Record{Lane: c.lane, Text: c.text, To: c.to}); got != c.want {
			t.Errorf("%q = %q, want %q", c.text, got, c.want)
		}
	}
}

func TestARootCloseEndsAnIncidentOnlyForTheRecordsBeforeIt(t *testing.T) {
	first := Record{Seq: 1, Lane: "api-fix", Kind: "incident", Status: "open", To: []string{"root"}}
	closing := Record{Seq: 2, Lane: "root", Kind: "decide", Text: "INCIDENT api CLOSED", To: []string{"api-fix"}}
	later := Record{Seq: 3, Lane: "api-fix", Kind: "incident", Status: "open", To: []string{"root"}}
	got := openIncidents([]Record{first, closing, later})
	if got[1] || !got[3] {
		t.Fatalf("open = %v", got)
	}
}

func TestAPRRowsOwnerIsTheNewestLanePushingItEvenWhenTheLedgerNamesAnotherAndNoDeskCounts(t *testing.T) {
	v := viewOf(
		[]Lane{heard("stale-1", "desired", 300), heard("ship-2", "desired", 5), heard("older-3", "desired", 5), heard("progress-desk-2", "unregistered", 1), heard("finder-desk", "unregistered", 1)},
		[]Record{
			record(1, "older-3", "head", "#48401 at abc", 30),
			record(2, "ship-2", "head", "#48401 rebased", 10),
			record(3, "root", "decide", "#48401 goes", 1),
			record(4, "ship-2", "note", "other work on #484010", 1),
			record(5, "progress-desk-2", "evidence", "#48401 is red", 1),
			record(6, "finder-desk", "review", "finder: major on #48401", 1),
		},
		nil,
	)
	row := v.prRow(store.ReviewRow{Number: 48401, Lane: "stale-1", State: "open", Title: "the title"}, nil)
	if row.Who != "ship-2" {
		t.Fatalf("who = %q", row.Who)
	}
	if !strings.HasPrefix(row.Wrong, "stale-1 quiet since ") {
		t.Fatalf("wrong = %q", row.Wrong)
	}
}

func TestAPRClaimedFromAnotherNodeUnderALaneAliasIsOwnedByThatAliasesLiveLane(t *testing.T) {
	alias := Lane{Name: "spa-edge-role", State: "unregistered", PRs: []int{50150, 50212}}
	sprite := heard("spa-edge-role-1", "desired", 2)
	older := heard("spa-edge-role-0", "desired", 30)
	v := viewOf(
		[]Lane{alias, older, sprite, heard("ship-2", "desired", 5)},
		[]Record{
			record(1, "ship-2", "note", "#50212 rebased onto dev", 40),
			{Seq: 4465, Drive: testDrive, Lane: "spa-edge-role", Kind: "claim", Text: "live on Sprite pool-7yt, owns #50212 and #50150", PRs: []string{"Forge-AI/monorepo#50212"}, At: ago(20), To: []string{root}},
		},
		nil,
	)
	row := v.prRow(store.ReviewRow{Number: 50212, State: "open", Title: "the edge role"}, nil)
	if row.Who != "spa-edge-role" {
		t.Fatalf("who = %q, want the lane whose claim from the Sprite names the PR", row.Who)
	}
	if row.Wrong != noneRecorded || !strings.Contains(row.Unblock, "next record due by "+pacific(sprite.Heard.Add(store.StaleAfter))) {
		t.Fatalf("wrong = %q, unblock = %q; want the alias answered by its newest live lane", row.Wrong, row.Unblock)
	}
}

func TestAPRRowSaysWhatHoldsItFromItsLedgerRow(t *testing.T) {
	v := viewOf(nil, nil, nil)
	held := v.prRow(store.ReviewRow{Number: 7, State: "open", CI: "red", WaitsOn: "CI is red at this head"}, map[string]int64{"7": 91})
	if held.Blocks != "held by cci #91" || held.State != "red" || held.Wrong != "its head is red" {
		t.Fatalf("held = %+v", held)
	}
	red := v.prRow(store.ReviewRow{Number: 8, State: "open", CI: "red", WaitsOn: "CI is red at this head"}, nil)
	if red.Blocks != "waits: CI is red at this head" {
		t.Fatalf("red = %+v", red)
	}
	queued := v.prRow(store.ReviewRow{Number: 9, State: "queued"}, nil)
	if queued.State != queueLabelled || !queued.Done || queued.unowned() {
		t.Fatalf("queued = %+v", queued)
	}
	ready := v.prRow(store.ReviewRow{Number: 10, State: "open", ReadyForTap: true, WaitsOn: "Only your approval."}, nil)
	if ready.State != "ready for your tap" || ready.Blocks != "waits: Only your approval." {
		t.Fatalf("ready = %+v", ready)
	}
}

func TestTheOpenPRSectionListsLedgerRowsAndNoOtherStore(t *testing.T) {
	rows := openPRRows([]store.ReviewRow{{Number: 1, State: "open"}, {Number: 2, State: "landed"}, {Number: 3, State: "superseded"}, {Number: 4, State: "queued"}})
	if len(rows) != 2 || rows[0].Number != 1 || rows[1].Number != 4 {
		t.Fatalf("open rows = %+v, want the open and queued rows alone", rows)
	}
}

func inputs(t *testing.T) Inputs {
	t.Helper()
	book, err := asks.Read(map[string]map[string]string{
		"ask/000001": {"lane": "ship-2", "text": "measure idle resources across every worker", "asked_at": ago(300).Format(time.RFC3339)},
		"48401":      {"lane": "ship-2", "title": "journal readers move to the sealed format", "reported_head": "abc"},
	})
	if err != nil {
		t.Fatal(err)
	}
	ship := heard("ship-2", "desired", 5)
	ship.Step, ship.Deliverable = "2", "the sealed journal format reaches every reader"
	return Inputs{
		Drive:   testDrive,
		Now:     now,
		Book:    book,
		PRRows:  []store.ReviewRow{{Number: 48401, Lane: "ship-2", Title: "journal readers move to the sealed format", Head: "abc", State: "open"}},
		Held:    map[string]int64{},
		Lanes:   []Lane{ship, heard("quiet-9", "desired", 30*60)},
		Records: []Record{record(5, "ship-2", "report", "readers ported", 10)},
		Plans: []*plans.Plan{{Title: "The rebuild", Steps: []*plans.Step{
			{Number: "1", Title: "1. The base", Tasks: []plans.Task{{Title: "Base", State: plans.Done}}},
			{Number: "2", Title: "2. The readers", Tasks: []plans.Task{{Title: "Readers", State: plans.InProgress}}},
			{Number: "3", Title: "3. The writers", Tasks: []plans.Task{{Title: "Writers", State: plans.Open}}},
		}}},
		Programs: []Program{{"cci token cut", "building", "nothing", "lands tonight"}},
		Streams:  []Workstream{{Name: "Sealed journal", Delivers: "every reader on the sealed format", Stems: []string{"ship"}}},
	}
}

func TestADoneStepFoldsIntoOneLineAndAnOpenOneWithNobodyLeadsAsUnowned(t *testing.T) {
	report := Render(inputs(t))
	if strings.Contains(report.Text, "### The base") {
		t.Fatalf("a done step keeps its section:\n%s", report.Text)
	}
	if !strings.Contains(report.Text, "## Done plan steps\n- The rebuild: The base") {
		t.Fatalf("a done step is gone from the update:\n%s", report.Text)
	}
	unowned := report.Text[strings.Index(report.Text, "## Unowned work"):strings.Index(report.Text, "## Standing duties")]
	if !strings.Contains(unowned, "Step 3") || strings.Contains(unowned, "Step 2") {
		t.Fatalf("unowned = %s", unowned)
	}
}

func TestTheUpdateCarriesTheNewestReapPassesAndDemandsThemOfAWrittenOne(t *testing.T) {
	in := inputs(t)
	for pass := range reapPasses + 1 {
		text := fmt.Sprintf("sessions %d to %d, worktrees 9 to 8, terminals 7 to 6; this pass: removed=1", 100-pass, 99-pass)
		in.All = append(in.All, Record{Seq: int64(900 + pass), Drive: testDrive, Lane: "supervisor", Kind: "state", Topic: lanes.ReapTopic, Text: text, At: ago(30 - pass)})
	}
	report := Render(in)
	if !strings.Contains(report.Text, "## "+reapTitle) || !strings.Contains(report.Text, "sessions 94 to 93") {
		t.Fatalf("no reap section in\n%s", report.Text)
	}
	if strings.Contains(report.Text, "sessions 100 to 99") {
		t.Fatalf("the oldest pass stayed in\n%s", report.Text)
	}
	if !slices.Contains(report.Needs, Need{reapTitle, reapTitle}) {
		t.Fatalf("needs lack the reap section: %v", report.Needs)
	}
}

func TestTheRenderCoversEveryNeedItDerives(t *testing.T) {
	report := Render(inputs(t))
	if left := Missing(report.Text, report.Needs); len(left) != 0 {
		t.Fatalf("missing = %v in\n%s", left, report.Text)
	}
	ids := make([]string, len(report.Needs))
	for i, need := range report.Needs {
		ids[i] = need.ID
	}
	for _, want := range []string{"ship-2", "ask/000001", "#48401", "Open PRs", "Unowned work"} {
		if !slices.Contains(ids, want) {
			t.Errorf("needs lack %q: %v", want, ids)
		}
	}
	if slices.Contains(ids, "quiet-9") {
		t.Errorf("a lane quiet over a day is a need: %v", ids)
	}
}

func TestAWrittenUpdateMissesEachIdItDoesNotNameWhole(t *testing.T) {
	needs := []Need{{"tooling-codify-1", ""}, {"release-5", ""}, {"ask:abc123", ""}, {"#48401", ""}, {"tooling-codify-1", ""}}
	got := Missing("tooling-codify-1 landed #48401; release-5-2 still runs", needs)
	if want := []string{"release-5", "ask:abc123"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("missing = %v", got)
	}
}

func TestAPlainWordsUpdatePassesOnItsSubjectsWithNoRawIds(t *testing.T) {
	needs := []Need{{"#48401", "journal readers move to the sealed format"}, {"ask/000001", "measure idle resources across every worker"}, {"#48402", "billing export learns another column"}}
	written := "The journal readers are moving to the sealed format. We are measuring idle resources across every worker: measure is half done."
	if got, want := Missing(written, needs), []string{"#48402"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("missing = %v", got)
	}
}

func TestAStandingDeskIsOnNoPlanStepEvenWhenItsStepOrItsRecordsNameOne(t *testing.T) {
	in := inputs(t)
	desk := heard("meta-desk-3", "desired", 5)
	desk.Standing, desk.Step = "drive tooling", "3"
	in.Lanes = append(in.Lanes, desk)
	in.Records = append(in.Records, record(6, "meta-desk-3", "report", "plan step 2 and step 3 tooling rows", 4))
	report := Render(in)
	for _, title := range []string{"### The readers\n", "### The writers\n"} {
		_, after, found := strings.Cut(report.Text, title)
		if !found {
			t.Fatalf("no %q in\n%s", title, report.Text)
		}
		step, _, _ := strings.Cut(after, "\n#")
		if strings.Contains(step, "meta-desk-3") {
			t.Errorf("%s carries the desk:\n%s", title, step)
		}
	}
}

func TestAStepWithTasksInProgressAndNoLaneCallsThemStaleAndNamesTheLastLaneThatWorkedIt(t *testing.T) {
	old := heard("readers-1", "retired", 600)
	desk := heard("meta-desk-3", "desired", 5)
	desk.Standing = "drive tooling"
	v := viewOf([]Lane{old, desk}, []Record{record(1, "readers-1", "report", "step 4 half ported", 300), record(2, "meta-desk-3", "report", "step 4 tooling", 5)}, nil)
	tasks := []plans.Task{{Title: "Port", State: plans.InProgress}, {Title: "Base", State: plans.Done}}
	worked := v.stepRow("Step 4", "4. The readers", &plans.Step{Number: "4", Tasks: tasks}, nil)
	if want := "1 of 2 tasks done, 1 stale with no lane on them, 0 blocked; last worked by readers-1, report " + pacific(ago(300)); worked.State != want {
		t.Fatalf("state = %q", worked.State)
	}
	untouched := v.stepRow("Step 5", "5. The writers", &plans.Step{Number: "5", Tasks: tasks}, nil)
	if want := "1 of 2 tasks done, 1 stale with no lane on them, 0 blocked; no lane record names it"; untouched.State != want {
		t.Fatalf("state = %q", untouched.State)
	}
}

func TestAnOpenStepWithNoLiveLaneNamesTheLastLaneThatWorkedItAndWhen(t *testing.T) {
	v := viewOf([]Lane{heard("writers-1", "retired", 600)}, []Record{record(1, "writers-1", "report", "step 6 half written", 26*60)}, nil)
	open := []plans.Task{{Title: "Write", State: plans.Open}}
	row := v.stepRow("Step 6", "6. The writers", &plans.Step{Number: "6", Tasks: open}, nil)
	if want := "0 of 1 tasks done, 0 in progress, 0 blocked; " + noLiveLane + ", last worked by writers-1, report Nov 1 9:00 AM"; row.State != want {
		t.Fatalf("state = %q", row.State)
	}
	if done := v.stepRow("Step 7", "7. Done", &plans.Step{Number: "7", Tasks: []plans.Task{{Title: "Write", State: plans.Done}}}, nil); done.State != "1 of 1 tasks done, 0 in progress, 0 blocked" {
		t.Fatalf("done state = %q", done.State)
	}
}

func TestAForwardPortWithNoMoveInAnHourLeadsTheUpdateWithWhatItWaitsOn(t *testing.T) {
	in := inputs(t)
	in.PRRows = []store.ReviewRow{
		{Number: 50849, Title: "steptree wrapper port", State: "open", Port: prrow.PortOf + "50525", WaitsOn: "port-lane: resolve the restack conflict", MovedAt: store.Stamp(in.Now.Add(-90 * time.Minute))},
		{Number: 50850, Title: "a fresh port", State: "open", Port: prrow.PortOf + "50526", WaitsOn: "CI", MovedAt: store.Stamp(in.Now.Add(-10 * time.Minute))},
		{Number: 50851, Title: "a still fix", State: "open", Port: prrow.None, WaitsOn: "CI", MovedAt: store.Stamp(in.Now.Add(-3 * time.Hour))},
	}

	report := Render(in)

	if !strings.Contains(report.Text, "Forward ports with no move in an hour (1):\n- #50849, port of #50525, has had no new head, grade or queue move since") || !strings.Contains(report.Text, "it waits on port-lane: resolve the restack conflict") {
		t.Fatalf("the stalled port is missing from the update:\n%s", report.Text)
	}
	if strings.Contains(report.Text, "- #50850, port of") || strings.Contains(report.Text, "- #50851,") {
		t.Fatalf("a moving port or a PR that is no port is listed as stalled:\n%s", report.Text)
	}
}
