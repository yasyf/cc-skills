package orca

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

func writeFixture(t *testing.T, name, text string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestALaunchWithAStepNamesItInTheSliceTheOwnerUpdateReads(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeRemote, Step: "4"}
	path, err := n.Write(t.TempDir(), "telemetry-1")
	if err != nil {
		t.Fatal(err)
	}
	said, err := lanes.BriefOf(path)
	if err != nil {
		t.Fatal(err)
	}
	if said.Step != "4" {
		t.Fatalf("the registry reads step %q from the brief at %s", said.Step, path)
	}
}

func TestNewBriefWritesWhatTheLaunchChecksRead(t *testing.T) {
	state := t.TempDir()
	n := NewBrief{
		Title:    "the desk's scripts become verbs",
		BodyFile: writeFixture(t, "body.md", "## Why\n\nOwner owner:m35 and owner:m7 asked, and owner:m35 again.\nNot um35 or m1234.\n"),
		Contract: writeFixture(t, "contract.md", "- Report to meta-desk only.\n"),
		Scale:    "medium",
		Place:    placeLocal,
		Why:      "mac-build",
		Topics:   []string{"verbs", "scratch"},
		Orders:   []string{"owner:m7", "owner:m35.2"},
	}
	path, err := n.Write(state, "desk-verbs-1")
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(state, "desk-verbs-1", "brief.md"); path != want {
		t.Errorf("path = %s, want %s", path, want)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	for _, want := range []string{
		"# desk-verbs-1: the desk's scripts become verbs\n",
		"Register slice: lane=desk-verbs-1 step=all topics=verbs,scratch\n",
		"Scale: medium\n",
		"ccx: role=build incident=none place=local why=mac-build\n",
		"- Report to meta-desk only.\n\n## Why",
		"Assigned orders: owner:m7 owner:m35.2\n",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("brief lacks %q:\n%s", want, text)
		}
	}
	order := Order{Lane: "desk-verbs-1", Model: defaultLaneModel, Effort: defaultLaneEffort, Brief: path}
	if faults := order.launchFaults(text, true); len(faults) != 0 {
		t.Errorf("the launch refuses a brief it wrote: %v", faults)
	}
	if !briefScale.MatchString(text) {
		t.Errorf("the launch reads no Scale in:\n%s", text)
	}
}

func TestABodyWithNoSliceGetsOneNamingTheLaneWordsAndThePathsItNames(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "Edit go/sand/next/internal/x.go:118 and (api/src/a.ts), again go/sand/next/internal/x.go.\n"), Scale: "small", Place: placeRemote}
	text, err := n.text("item-90-sidebar-1")
	if err != nil {
		t.Fatal(err)
	}
	if want := "\nRegister slice: lane=item-90-sidebar-1 step=all topics=item,sidebar paths=go/sand/next/internal/x.go,api/src/a.ts\nScale: small\n"; !strings.Contains(text, want) {
		t.Errorf("brief lacks %q:\n%s", want, text)
	}
}

func TestABodyWithItsOwnSliceKeepsItAsTheOnlyOne(t *testing.T) {
	body := writeFixture(t, "body.md", "Register slice: lane=lane-a step=1 topics=launch\nDo it.\n")
	text, err := (NewBrief{Title: "t", BodyFile: body, Scale: "small", Place: placeRemote}).text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(text, sliceLine) != 1 || !strings.Contains(text, "\n\nRegister slice: lane=lane-a step=1 topics=launch\nDo it.\n") {
		t.Errorf("brief:\n%s", text)
	}
	if _, err := (NewBrief{Title: "t", BodyFile: body, Scale: "small", Place: placeRemote, Topics: []string{"relay"}}).text("lane-a"); err == nil {
		t.Error("--topics beside the body's own slice line wrote a brief")
	}
}

func TestATopicWithASpaceIsRefusedBeforeTheLaunchPosts(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeRemote, Topics: []string{"iris sidebar", "agents"}}
	text, err := n.text("item-90-fix-1")
	if err != nil {
		t.Fatal(err)
	}
	order := Order{Lane: "item-90-fix-1", Model: defaultLaneModel, Effort: defaultLaneEffort, Brief: "brief.md"}
	if faults := strings.Join(order.launchFaults(text, true), "; "); !strings.Contains(faults, "rulings.py cannot read") {
		t.Errorf("faults = %q for:\n%s", faults, text)
	}
}

func TestNewBriefRemotePlaceAndNoOwnerOrders(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain, citing m876 and m829 as background\n"), Scale: "small", Place: placeRemote}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(text, "place=local") || !strings.Contains(text, "ccx: role=build incident=none\n") {
		t.Errorf("remote brief names a local place:\n%s", text)
	}
	if strings.Contains(text, "orders:") {
		t.Errorf("a launch with no --order carries an orders line:\n%s", text)
	}
}

func TestNewBriefAssignsNoOrderTheBodyQuotes(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "His owner:m835 ruling is background.\n"), Scale: "small", Place: placeRemote}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(text, "orders:") {
		t.Errorf("a quoted owner:m<row> became an orders line:\n%s", text)
	}
}

func TestNewBriefRefusesAnAssignmentItDidNotWrite(t *testing.T) {
	for name, n := range map[string]NewBrief{
		"a body writing the line":   {Title: "t", BodyFile: writeFixture(t, "body.md", "plain\nAssigned orders: owner:m835\n"), Scale: "small", Place: placeRemote},
		"an --order that is no row": {Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeRemote, Orders: []string{"m835"}},
	} {
		t.Run(name, func(t *testing.T) {
			if text, err := n.text("lane-a"); err == nil {
				t.Errorf("wrote a brief:\n%s", text)
			}
		})
	}
}

func TestNewBriefReadsItsBodyFromStdinAndAppendsThePostersStandingLines(t *testing.T) {
	standing := t.TempDir()
	if err := os.WriteFile(filepath.Join(standing, standingFile), []byte("A standing line.\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	n := NewBrief{Title: "t", BodyFile: bodyFromStdin, Stdin: strings.NewReader("Owner owner:m35 said so.\n"), Scale: "small", Place: placeRemote, StandingDir: standing}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(text, "Owner owner:m35 said so.\n\nA standing line.\n") {
		t.Errorf("brief:\n%s", text)
	}
}

func TestNewBriefWithNoStandingFileWritesOnlyItsBody(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeRemote, StandingDir: t.TempDir()}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(text, "\n\nplain\n") {
		t.Errorf("brief:\n%s", text)
	}
}

func TestABriefLrLaunchLaneWritesWithItsDefaultPlaceStartsOnASprite(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: laneCommand(nil).Flags().Lookup("place").DefValue}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if placedLocal(text) || !strings.Contains(text, "ccx: role=build incident=none\n") {
		t.Errorf("the default brief does not start on a Sprite:\n%s", text)
	}
}

func TestALaneThatNeedsTheMachineProfileLaunchesOnTheMac(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeLocal, Why: "aws-machine-profile"}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if !placedLocal(text) || !strings.Contains(text, "ccx: role=build incident=none place=local why=aws-machine-profile\n") {
		t.Errorf("a lane needing the machine profile does not start on the Mac:\n%s", text)
	}
	n.Why = "aws"
	if _, err := n.text("lane-a"); err == nil || !strings.Contains(err.Error(), "why=aws-machine-profile") {
		t.Errorf("a refused need does not name the machine-profile reason: %v", err)
	}
}

func TestNewBriefCCXIsTheWholeLineAndKeepsACommaInItsReason(t *testing.T) {
	for give, want := range map[string]string{
		"role=read,place=local,why=two rows, no compile": "ccx: role=read place=local why=two rows, no compile incident=none\n",
		"role=build, place=remote":                       "ccx: role=build place=remote incident=none\n",
		"role=build,incident=sev1,place=local":           "ccx: role=build incident=sev1 place=local\n",
	} {
		n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeLocal, CCX: give}
		text, err := n.text("lane-a")
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(text, want) {
			t.Errorf("--ccx %q, want %q in:\n%s", give, want, text)
		}
	}
}

func TestNewBriefRefusals(t *testing.T) {
	body := writeFixture(t, "body.md", "plain\n")
	for _, tt := range []struct {
		name string
		n    NewBrief
		want string
	}{
		{"no title", NewBrief{BodyFile: body, Place: placeRemote}, "--title"},
		{"empty body", NewBrief{Title: "t", BodyFile: writeFixture(t, "empty.md", " \n"), Place: placeRemote}, "is empty"},
		{"missing body", NewBrief{Title: "t", BodyFile: filepath.Join(t.TempDir(), "gone.md"), Place: placeRemote}, "--body"},
		{"missing contract", NewBrief{Title: "t", BodyFile: body, Contract: filepath.Join(t.TempDir(), "gone.md"), Place: placeRemote}, "--contract"},
		{"unknown place", NewBrief{Title: "t", BodyFile: body, Place: "moon"}, "--place moon"},
		{"the Mac with no need", NewBrief{Title: "t", BodyFile: body, Place: placeLocal}, "--place local takes --why naming a need only this Mac meets: why=sentry"},
		{"the Mac for a need it does not hold alone", NewBrief{Title: "t", BodyFile: body, Place: placeLocal, Why: "mac-inputs"}, "--place local takes --why"},
		{"a need on a Sprite", NewBrief{Title: "t", BodyFile: body, Place: placeRemote, Why: "sentry"}, "--why sentry keeps a lane on this Mac, so it takes --place local"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := tt.n.Write(t.TempDir(), "lane-a"); err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("err = %v, want %q", err, tt.want)
			}
		})
	}
}

func TestNewBriefKeepsABriefThatReachedWorkerStart(t *testing.T) {
	state := t.TempDir()
	dir := filepath.Join(state, "lane-a")
	if err := os.MkdirAll(filepath.Join(dir, workerAttempt), 0o750); err != nil {
		t.Fatal(err)
	}
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeRemote}
	if _, err := n.Write(state, "lane-a"); err == nil || !strings.Contains(err.Error(), "next number") {
		t.Fatalf("err = %v, want the attempt refused", err)
	}
	if _, err := os.Stat(filepath.Join(dir, briefFile)); !os.IsNotExist(err) {
		t.Errorf("a brief was written over a started lane: %v", err)
	}
}

func TestNewBriefWritesTheOwnersPickTheCostGateReads(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeRemote, OwnerPick: "owner:m836"}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if faults := (Order{Lane: "lane-a", Model: "fable", Effort: "xhigh"}).costFaults(text); len(faults) != 0 {
		t.Errorf("%v\n%s", faults, text)
	}
	n.OwnerPick = "m836"
	if _, err := n.text("lane-a"); err == nil || !strings.Contains(err.Error(), "--owner-pick takes owner:m<row>") {
		t.Fatal(err)
	}
}
