package update

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestTheToolingListReadsOneProgramPerTableRowAndSkipsItsProse(t *testing.T) {
	path := filepath.Join(t.TempDir(), "programs.md")
	list := "# Drive tooling\n\nKept by meta-desk-1.\n\n| Program | State | Blocker | Next |\n|---|:--|---|---|\n| cci token cut | building | #2110 is red | fix the fixture by 3 AM |\n| the Go loop | merged, not installed | the installer's quiet point | install at the next one |\n"
	if err := os.WriteFile(path, []byte(list), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := ReadPrograms(path)
	if err != nil {
		t.Fatal(err)
	}
	want := []Program{{"cci token cut", "building", "#2110 is red", "fix the fixture by 3 AM"}, {"the Go loop", "merged, not installed", "the installer's quiet point", "install at the next one"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("programs = %+v", got)
	}
}

func TestAToolingListWithoutItsHeaderOrWithAShortRowIsRefused(t *testing.T) {
	for name, list := range map[string]string{
		"header": "| Item | State | Blocker | Next |\n|---|---|---|---|\n",
		"short":  "| Program | State | Blocker | Next |\n|---|---|---|---|\n| hook tiers | building |\n",
	} {
		path := filepath.Join(t.TempDir(), "programs.md")
		if err := os.WriteFile(path, []byte(list), 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadPrograms(path); err == nil {
			t.Errorf("%s: read with no error", name)
		}
	}
}

func TestAMissingToolingListReadsAsNoPrograms(t *testing.T) {
	got, err := ReadPrograms(filepath.Join(t.TempDir(), "programs.md"))
	if err != nil || got != nil {
		t.Fatalf("programs = %v, err = %v", got, err)
	}
}

func TestTheUpdateCarriesEveryProgramWithItsBlockerAfterTheStandingDuties(t *testing.T) {
	in := inputs(t)
	in.Programs = []Program{{"cci token cut", "building", "#2110 is red", "fix the fixture by 3 AM"}, {"hook tiers", "designing", "", "a card for the owner"}}
	report := Render(in)
	titles := headings(report.Text)
	duties := strings.Index(strings.Join(titles, "\n"), "## Standing duties\n## "+toolingTitle)
	if duties < 0 {
		t.Fatalf("titles = %v", titles)
	}
	section := sectionOf(t, report.Text, toolingTitle)
	if !strings.Contains(section, "| cci token cut | building | #2110 is red | fix the fixture by 3 AM |") || !strings.Contains(section, "| hook tiers | designing |  | a card for the owner |") {
		t.Fatalf("section = %s", section)
	}
	if want := []string{"hook tiers" + noBlocker}; !reflect.DeepEqual(report.Gaps, want) {
		t.Fatalf("gaps = %v", report.Gaps)
	}
}

func TestAnUpdateWithNoToolingProgramsIsAGap(t *testing.T) {
	in := inputs(t)
	in.Programs = nil
	report := Render(in)
	if len(report.Gaps) != 1 || !strings.HasPrefix(report.Gaps[0], toolingTitle+" has no programs") {
		t.Fatalf("gaps = %v", report.Gaps)
	}
}

func TestAWrittenUpdateNamesEachProgramWithItsBlockerOnTheSameLine(t *testing.T) {
	programs := []Program{
		{"cci token cut", "building", "#2110 is red", "fix the fixture"},
		{"the Go loop", "merged", "the installer's quiet point", "install"},
		{"Sprites", "live", "nothing", "watch"},
		{"dashboard", "designing", "the owner's pick of two mockups", "a card"},
	}
	written := "The cci token cut is building; #2110 is red.\nThe Go loop merged.\nIt waits on the installer's quiet point.\nSprites are live, nothing blocks them."
	if got, want := ToolingLeft(written, programs), []string{"the Go loop" + noBlocker, "dashboard"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("left = %v", got)
	}
}
