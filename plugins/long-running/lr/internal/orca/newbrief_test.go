package orca

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeFixture(t *testing.T, name, text string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestNewBriefWritesWhatTheLaunchChecksRead(t *testing.T) {
	state := t.TempDir()
	n := NewBrief{
		Title:    "the desk's scripts become verbs",
		BodyFile: writeFixture(t, "body.md", "## Why\n\nOwner m35 and m7 asked, and m35 again.\nNot um35 or m1234.\n"),
		Contract: writeFixture(t, "contract.md", "- Report to meta-desk only.\n"),
		Scale:    "medium",
		Place:    placeLocal,
		Topics:   []string{"verbs", "scratch"},
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
		"ccx: role=build incident=none place=local why=mac-inputs\n",
		"- Report to meta-desk only.\n\n## Why",
		"Owner orders: owner:m35 owner:m7\n",
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

func TestNewBriefRemotePlaceAndNoOwnerOrders(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeRemote}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(text, "place=local") || !strings.Contains(text, "ccx: role=build incident=none\n") {
		t.Errorf("remote brief names a local place:\n%s", text)
	}
	if strings.Contains(text, "Owner orders") {
		t.Errorf("a body citing no order carries an Owner orders line:\n%s", text)
	}
}

func TestNewBriefReadsItsBodyFromStdinAndAppendsThePostersStandingLines(t *testing.T) {
	standing := t.TempDir()
	if err := os.WriteFile(filepath.Join(standing, standingFile), []byte("A standing line.\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	n := NewBrief{Title: "t", BodyFile: bodyFromStdin, Stdin: strings.NewReader("Owner m35 said so.\n"), Scale: "small", Place: placeLocal, StandingDir: standing}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(text, "Owner m35 said so.\n\nA standing line.\n\nOwner orders: owner:m35\n") {
		t.Errorf("brief:\n%s", text)
	}
}

func TestNewBriefWithNoStandingFileWritesOnlyItsBody(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeLocal, StandingDir: t.TempDir()}
	text, err := n.text("lane-a")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(text, "\n\nplain\n") {
		t.Errorf("brief:\n%s", text)
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
		{"no title", NewBrief{BodyFile: body, Place: placeLocal}, "--title"},
		{"empty body", NewBrief{Title: "t", BodyFile: writeFixture(t, "empty.md", " \n"), Place: placeLocal}, "is empty"},
		{"missing body", NewBrief{Title: "t", BodyFile: filepath.Join(t.TempDir(), "gone.md"), Place: placeLocal}, "--body"},
		{"missing contract", NewBrief{Title: "t", BodyFile: body, Contract: filepath.Join(t.TempDir(), "gone.md"), Place: placeLocal}, "--contract"},
		{"unknown place", NewBrief{Title: "t", BodyFile: body, Place: "moon"}, "--place moon"},
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
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeLocal}
	if _, err := n.Write(state, "lane-a"); err == nil || !strings.Contains(err.Error(), "next number") {
		t.Fatalf("err = %v, want the attempt refused", err)
	}
	if _, err := os.Stat(filepath.Join(dir, briefFile)); !os.IsNotExist(err) {
		t.Errorf("a brief was written over a started lane: %v", err)
	}
}

func TestNewBriefWritesTheOwnersPickTheCostGateReads(t *testing.T) {
	n := NewBrief{Title: "t", BodyFile: writeFixture(t, "body.md", "plain\n"), Scale: "small", Place: placeLocal, OwnerPick: "owner:m836"}
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
