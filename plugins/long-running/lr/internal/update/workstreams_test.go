package update

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
)

func TestTheWorkstreamListReadsOnlyNameDeliverableLaneStemsStepsAndAnOptionalNote(t *testing.T) {
	path := filepath.Join(t.TempDir(), "workstreams.md")
	list := "# Workstreams\n\n| Workstream | Delivers | Lanes | Steps | Note |\n|---|---|---|---|---|\n| Durable agents | a run survives a restart | durable-agents, agent-memory | 3, 7.2 | design review tonight |\n| The signal service | one signal per change | signal | | |\n"
	if err := os.WriteFile(path, []byte(list), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := ReadWorkstreams(path)
	if err != nil {
		t.Fatal(err)
	}
	want := []Workstream{
		{"Durable agents", "a run survives a restart", []string{"durable-agents", "agent-memory"}, []string{"3", "7.2"}, "design review tonight"},
		{"The signal service", "one signal per change", []string{"signal"}, []string{}, ""},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("workstreams = %+v", got)
	}
}

func TestAListStillCarryingHandStateColumnsIsUnreadNamingTheColumnsItWants(t *testing.T) {
	path := filepath.Join(t.TempDir(), "workstreams.md")
	list := "| Workstream | Lanes | State | Changed | Blocker | Owner action |\n|---|---|---|---|---|---|\n| Durable agents | durable-agents | UNKNOWN | UNKNOWN | nothing | nothing |\n"
	if err := os.WriteFile(path, []byte(list), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadWorkstreams(path); err == nil || !strings.Contains(err.Error(), "want | Workstream | Delivers | Lanes | Steps | Note |") {
		t.Fatalf("err = %v", err)
	}
}

func workstreamCells(t *testing.T, section, name string) []string {
	t.Helper()
	for _, line := range strings.Split(section, "\n") {
		if strings.HasPrefix(line, "| "+name+" | ") {
			return strings.Split(strings.Trim(line, "| "), " | ")
		}
	}
	t.Fatalf("no row %q in\n%s", name, section)
	return nil
}

func TestEachWorkstreamRowIsComputedFromItsLanesRecordsPRsAndBoardAndNamesItsLiveLanes(t *testing.T) {
	in := inputs(t)
	in.Before.At = ago(60)
	signal := heard("signal-1", "retired", 26*60)
	in.Lanes = append(in.Lanes, heard("durable-agents-3", "desired", 5), heard("durable-agents-fix-1", "desired", 3), heard("orphan-1", "desired", 5), signal)
	blocked := record(21, "durable-agents-fix-1", "blocked", "waits on the review of #49509", 3)
	blocked.Status = statusOpen
	landed := record(20, "durable-agents-3", "report", "a run survives a pod restart", 20)
	in.Records = append(in.Records, landed, blocked)
	in.All = []Record{record(4, "durable-agents-3", "report", "an older report", 90), landed, blocked}
	book, err := asks.Read(map[string]map[string]string{
		"49509": {"lane": "durable-agents-fix-1", "title": "pause meters", "reported_head": "abc", "test_state": "failure"},
		"49400": {"lane": "durable-agents-3", "title": "resume runs", "state": "landed", "landed_at": ago(30).Format(time.RFC3339)},
		"48401": {"lane": "ship-2", "title": "journal readers move to the sealed format", "reported_head": "abc", "labels": "merge"},
	})
	if err != nil {
		t.Fatal(err)
	}
	in.Book = book
	in.PRRows = []store.ReviewRow{
		{Number: 49509, Lane: "durable-agents-fix-1", Title: "pause meters", Head: "abc", State: "open", CI: "red", WaitsOn: "CI is red at this head"},
		{Number: 49510, Lane: "durable-agents-3", Title: "stops the crash", Head: "def5678abcdef", State: "open", ReadyForTap: true},
		{Number: 49400, Lane: "durable-agents-3", Title: "resume runs", State: "landed", LandedAt: store.Stamp(ago(30))},
		{Number: 48401, Lane: "ship-2", Title: "journal readers move to the sealed format", Head: "abc", State: "queued"},
	}
	in.Cards = []board.Wait{
		{Kind: "tap", PR: "49509", Head: "abc1234", Since: "10:50 AM", Title: "stops the crash"},
		{Kind: "pick", Block: "card-durable-agents-store", Subject: "store", Title: "which store keeps a run", Since: "10:40 AM"},
		{Kind: "pick", Block: "card-signal-design", Subject: "design", Title: "valkey or postgres", Since: "10:30 AM"},
		{Kind: "pick", Block: "card-design-signals", Subject: "naming", Title: "what the signals page is called", Since: "10:20 AM"},
	}
	in.Streams = []Workstream{
		{Name: "Durable agents", Delivers: "a run survives a restart", Stems: []string{"durable-agents"}},
		{Name: "Sealed journal", Delivers: "every reader on the sealed format", Steps: []string{"2"}},
		{Name: "The signal service", Delivers: "one signal per change", Stems: []string{"signal"}},
	}
	report := Render(in)
	section := sectionOf(t, report.Text, workstreamsTitle)

	durable := workstreamCells(t, section, "Durable agents")
	if durable[2] != "durable-agents-3, durable-agents-fix-1" {
		t.Errorf("lanes = %q", durable[2])
	}
	if !strings.HasPrefix(durable[3], "#49509 red; #49510 ready for your tap; durable-agents-fix-1 blocked "+pacific(ago(3))+": waits on the review") {
		t.Errorf("state = %q", durable[3])
	}
	if want := "#49400 landed " + pacific(ago(30)) + "; durable-agents-3 report " + pacific(ago(20)) + ": a run survives a pod restart"; durable[4] != want {
		t.Errorf("changed = %q", durable[4])
	}
	if durable[5] != "#49509 red; durable-agents-fix-1: waits on the review of #49509" {
		t.Errorf("blocks = %q", durable[5])
	}
	if !strings.HasPrefix(durable[6], "tap #49510 at def5678: stops the crash; pick card-durable-agents-store") || strings.Contains(durable[6], "49509") || strings.Contains(durable[6], "signal") {
		t.Errorf("you must = %q", durable[6])
	}

	journal := workstreamCells(t, section, "Sealed journal")
	if journal[2] != "ship-2" || !strings.HasPrefix(journal[3], "#48401 "+queueLabelled+"; step 2 0 of 1 tasks done; ship-2 report ") {
		t.Errorf("journal = %q", journal)
	}
	if want := "nothing recorded since " + pacific(ago(60)); journal[4] != want {
		t.Errorf("changed = %q, want %q", journal[4], want)
	}
	if want := "nothing recorded since Nov 1 11:00 AM"; journal[5] != want {
		t.Errorf("blocks = %q, want %q", journal[5], want)
	}
	if want := "nothing on your board as of " + pacific(now); journal[6] != want {
		t.Errorf("you must = %q, want %q", journal[6], want)
	}

	quiet := workstreamCells(t, section, "The signal service")
	if quiet[2] != noLiveLane+"; last signal-1, heard Nov 1 9:00 AM" || !strings.HasPrefix(quiet[6], "pick card-signal-design design: valkey or postgres") || strings.Contains(quiet[6], "card-design-signals") {
		t.Errorf("signal = %q", quiet)
	}
	if strings.Contains(section, "UNKNOWN") {
		t.Errorf("a cell reads UNKNOWN:\n%s", section)
	}
	if !reflect.DeepEqual(report.Gaps, []string{inNoWorkstream + "orphan-1"}) {
		t.Errorf("gaps = %v", report.Gaps)
	}
}

func TestAWorkstreamNoLaneEverWorkedSaysSo(t *testing.T) {
	in := inputs(t)
	in.Streams = append(in.Streams, Workstream{Name: "Billing export", Delivers: "one more column", Stems: []string{"billing"}, Note: "waits on finance"})
	cells := workstreamCells(t, sectionOf(t, Render(in).Text, workstreamsTitle), "Billing export")
	if cells[2] != noLiveLane+", and no lane on it recorded" || cells[3] != "waits on finance" {
		t.Fatalf("cells = %q", cells)
	}
}

func TestAnUnreadBoardIsWhatTheOwnerActionCellSaysNeverNothingOnTheBoard(t *testing.T) {
	sources := src([]Lane{heard("ship-2", "desired", 5)}, nil, nil, errors.New("cc-present: no session"))
	sources.Streams = func() ([]Workstream, error) {
		return []Workstream{{Name: "Sealed journal", Delivers: "every reader on the sealed format", Stems: []string{"ship"}}}, nil
	}
	in, err := Collect(context.Background(), sources, testDrive, now)
	if err != nil {
		t.Fatal(err)
	}
	if cells := workstreamCells(t, sectionOf(t, Render(in).Text, workstreamsTitle), "Sealed journal"); cells[6] != "board unread: cc-present: no session" {
		t.Fatalf("you must = %q", cells[6])
	}
}

func TestAnUpdateWithNoWorkstreamListSaysSoOnOneLineAndStillRendersEveryOtherSection(t *testing.T) {
	in := inputs(t)
	in.Streams = nil
	report := Render(in)
	section := strings.TrimSpace(sectionOf(t, report.Text, workstreamsTitle))
	if !strings.HasPrefix(section, "- no workstreams: ") || strings.Contains(section, "\n") {
		t.Fatalf("section = %q", section)
	}
	if !strings.Contains(report.Text, "## "+toolingTitle) || len(report.Gaps) != 1 {
		t.Fatalf("gaps = %v\n%s", report.Gaps, report.Text)
	}
}

func TestAnUnreadWorkstreamListIsOneLineInTheUpdateNotAFailedCollect(t *testing.T) {
	sources := src([]Lane{heard("ship-2", "desired", 5)}, nil, nil, nil)
	sources.Streams = func() ([]Workstream, error) { return nil, errors.New("workstreams.md: header \"| Name |\"") }
	in, err := Collect(context.Background(), sources, testDrive, now)
	if err != nil {
		t.Fatal(err)
	}
	report := Render(in)
	if section := sectionOf(t, report.Text, workstreamsTitle); !strings.Contains(section, "- the workstream list is unread, so a workstream may be missing: workstreams.md: header") {
		t.Fatalf("section = %s", section)
	}
}
