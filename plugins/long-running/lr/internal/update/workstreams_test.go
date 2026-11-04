package update

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestTheWorkstreamListReadsOneRowPerWorkstreamWithItsLaneStems(t *testing.T) {
	path := filepath.Join(t.TempDir(), "workstreams.md")
	list := "# Workstreams\n\nKept by the progress desk.\n\n| Workstream | Lanes | State | Changed | Blocker | Owner action |\n|---|---|---|---|---|---|\n| Durable agents | durable-agents, agent-memory-2 | building | runs resume after a restart | nothing | nothing |\n"
	if err := os.WriteFile(path, []byte(list), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := ReadWorkstreams(path)
	if err != nil {
		t.Fatal(err)
	}
	want := []Workstream{{"Durable agents", []string{"durable-agents", "agent-memory-2"}, "building", "runs resume after a restart", "nothing", "nothing"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("workstreams = %+v", got)
	}
}

func TestTheUpdateLeadsWithEveryWorkstreamInPlainWordsAndNamesEachLiveLaneInNone(t *testing.T) {
	in := inputs(t)
	in.Lanes = append(in.Lanes, heard("durable-agents-3", "desired", 5), heard("durable-agents-fix-1", "desired", 5), heard("signal-2", "desired", 5), heard("orphan-1", "desired", 5))
	in.Streams = []Workstream{
		{"Durable agents", []string{"durable-agents"}, "building", "a run survives a pod restart", "nothing", "nothing"},
		{"The signal service", []string{"signal-1"}, "designing", "nothing yet", "your pick of two designs", "pick on the board"},
	}
	report := Render(in)
	if titles := headings(report.Text); titles[0] != "## "+workstreamsTitle {
		t.Fatalf("titles = %v", titles)
	}
	section := sectionOf(t, report.Text, workstreamsTitle)
	for _, line := range []string{
		"| Durable agents | building | a run survives a pod restart | nothing | nothing |",
		"| The signal service | designing | nothing yet | your pick of two designs | pick on the board |",
		inNoWorkstream + "orphan-1, ship-2",
	} {
		if !strings.Contains(section, line) {
			t.Errorf("no %q in\n%s", line, section)
		}
	}
	if strings.Contains(section, "durable-agents") || strings.Contains(section, "quiet-9") {
		t.Errorf("the section names a lane it holds or one not live:\n%s", section)
	}
	if !reflect.DeepEqual(report.Gaps, []string{inNoWorkstream + "orphan-1, ship-2"}) {
		t.Errorf("gaps = %v", report.Gaps)
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
