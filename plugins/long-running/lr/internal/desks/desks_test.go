package desks

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const testDrive = "durable-agents-rebuild"

var (
	t0            = time.Date(2026, 11, 2, 19, 0, 0, 0, time.UTC)
	everyDesk     = []string{"fidelity-desk", "red-sweep", "alerts-desk", "drive-watchdog", "board-refresher"}
	onlyDeskBrief = "ccx: role=desk place=remote\nStanding: the duty\n"
)

type machine struct {
	mu     sync.Mutex
	lane   string
	refuse string
	calls  []shell.Command
}

func (m *machine) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.calls = append(m.calls, c)
	line := strings.Join(c.Argv, " ")
	switch {
	case line == "cci me":
		return shell.Result{Stdout: fmt.Sprintf("window lane drive=%s lane=%s branch=b\n", testDrive, m.lane)}, nil
	case m.refuse != "" && strings.Contains(line, m.refuse):
		return shell.Result{Code: 1, Stdout: m.refuse + " LAUNCH-FAILED\n"}, &shell.ExitError{Argv: c.Argv, Code: 1}
	}
	return shell.Result{Stdout: c.Argv[0] + " ran\n"}, nil
}

func (m *machine) lines(prefix string) []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	var found []string
	for _, call := range m.calls {
		if line := strings.Join(call.Argv, " "); strings.HasPrefix(line, prefix) {
			found = append(found, line)
		}
	}
	return found
}

func newDrive(t *testing.T, lane string, briefs ...string) (*app.App, *machine, string) {
	t.Helper()
	t.Setenv(lanes.LaneEnv, "")
	state := t.TempDir()
	config, err := json.Marshal(map[string]any{"repo": "o/r", "drive": testDrive, "checkout": "/c", "author": "a", "label": "l", "movers": []string{"lanes"}})
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(state, "lr.json")
	if err := os.WriteFile(path, config, 0o600); err != nil {
		t.Fatal(err)
	}
	for _, name := range briefs {
		writeBrief(t, state, name, onlyDeskBrief)
	}
	m := &machine{lane: lane}
	return &app.App{ConfigPath: path, Runner: m, Now: func() time.Time { return t0 }}, m, state
}

func writeBrief(t *testing.T, state, name, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(state, name), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, name, "brief.md"), []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

func wantLane(t *testing.T, state, name, brief string, laneState store.LaneState) {
	t.Helper()
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if _, err := s.WantLane(store.Lane{Name: name, Brief: brief, Model: "opus", Effort: "high", Role: "lane", State: laneState}, t0); err != nil {
		t.Fatal(err)
	}
}

func TestACommunicatorBoundWindowStartsNoDesk(t *testing.T) {
	a, m, _ := newDrive(t, communicator, everyDesk...)
	err := start(context.Background(), a, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "the coordinator (lane root) owns every desk") {
		t.Fatalf("got %v, want a refusal naming the coordinator", err)
	}
	if lines := m.lines(""); !slices.Equal(lines, []string{"cci me"}) {
		t.Fatalf("a refused start ran %q", lines)
	}
}

func TestALauncherNamedCommunicatorWindowIsRefusedWithoutAskingCci(t *testing.T) {
	a, m, _ := newDrive(t, "unread", everyDesk...)
	t.Setenv(lanes.LaneEnv, communicator)
	if err := start(context.Background(), a, &bytes.Buffer{}); err == nil || !strings.Contains(err.Error(), "refused") {
		t.Fatalf("got %v, want a refusal", err)
	}
	if lines := m.lines(""); len(lines) != 0 {
		t.Fatalf("a refused start ran %q", lines)
	}
}

func TestTheCoordinatorStartsEveryDesk(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, everyDesk...)
	if err := start(context.Background(), a, io.Discard); err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(state, "lr.json")
	all := m.lines("")
	rebind := slices.Index(all, "lr relay rebind --config "+config)
	first := slices.IndexFunc(all, func(line string) bool { return strings.HasPrefix(line, "desk-launch.py") })
	if rebind < 0 || first < rebind {
		t.Fatalf("this terminal must take the Run before any desk launches: %q", all)
	}
	launched := m.lines("desk-launch.py lane")
	if len(launched) != len(everyDesk) {
		t.Fatalf("launched %q, want every desk in %q", launched, everyDesk)
	}
	for _, name := range everyDesk {
		want := fmt.Sprintf("desk-launch.py lane %s ", name)
		suffix := fmt.Sprintf("%s --drive %s --lane %s", filepath.Join(state, name, "brief.md"), testDrive, lanes.Root)
		if !slices.ContainsFunc(launched, func(line string) bool { return strings.HasPrefix(line, want) && strings.HasSuffix(line, suffix) }) {
			t.Fatalf("no launch of %s posted as the coordinator from its brief in %q", name, launched)
		}
	}
}

func TestADeskUpUnderAnyNumberIsNotStartedAgain(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, "red-sweep", "alerts-desk", "drive-watchdog", "board-refresher")
	wantLane(t, state, "fidelity-desk-3", "/b.md", store.LaneDesired)
	var out bytes.Buffer
	if err := start(context.Background(), a, &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "fidelity-desk is up as fidelity-desk-3") {
		t.Fatalf("got %q", out.String())
	}
	if launched := m.lines("desk-launch.py lane fidelity-desk"); len(launched) != 0 {
		t.Fatalf("relaunched a live desk: %q", launched)
	}
	if rebound := m.lines("lr relay rebind"); len(rebound) != 1 {
		t.Fatalf("a relaunched coordinator must still take the Run, ran %q", rebound)
	}
}

func recordedBrief(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "fidelity-desk-3.brief.md")
	if err := os.WriteFile(path, []byte(onlyDeskBrief), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestADownDeskRelaunchesUnderItsNextNameFromItsRecordedBrief(t *testing.T) {
	for _, down := range []store.LaneState{store.LaneHeld, store.LaneRetired} {
		t.Run(string(down), func(t *testing.T) {
			a, m, state := newDrive(t, lanes.Root, "red-sweep", "alerts-desk", "drive-watchdog", "board-refresher")
			wantLane(t, state, "fidelity-desk-3", recordedBrief(t), down)
			var out bytes.Buffer
			if err := start(context.Background(), a, &out); err != nil {
				t.Fatal(err)
			}
			successor := filepath.Join(state, "fidelity-desk-4", "brief.md")
			want := fmt.Sprintf("desk-launch.py lane fidelity-desk-4 opus high %s --drive %s --lane %s", successor, testDrive, lanes.Root)
			if launched := m.lines("desk-launch.py lane fidelity-desk"); !slices.Equal(launched, []string{want}) {
				t.Fatalf("launched %q, want %q", launched, want)
			}
			if copied, err := os.ReadFile(successor); err != nil || string(copied) != onlyDeskBrief {
				t.Fatalf("the successor's brief holds %q (%v), want fidelity-desk-3's recorded brief", copied, err)
			}
			if !strings.Contains(out.String(), "fidelity-desk is "+string(down)+" as fidelity-desk-3, so fidelity-desk-4 launches") {
				t.Fatalf("got %q", out.String())
			}
		})
	}
}

func TestADownDeskNeverTakesANameAnotherLanePostedUnder(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, everyDesk...)
	wantLane(t, state, "red-sweep-2", recordedBrief(t), store.LaneRetired)
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	if err := s.LaneHeard("red-sweep-3", t0.Add(-time.Hour), "note", 7); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	if err := start(context.Background(), a, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if launched := m.lines("desk-launch.py lane red-sweep"); len(launched) != 1 || !strings.HasPrefix(launched[0], "desk-launch.py lane red-sweep-4 ") {
		t.Fatalf("got %q, want red-sweep-4 launched once", launched)
	}
}

func posted(t *testing.T, state, name string, at time.Time, seq int64) {
	t.Helper()
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if err := s.LaneHeard(name, at, "report", seq); err != nil {
		t.Fatal(err)
	}
}

func TestARoleWhoseHolderPostsUnderAnyNameLaunchesNoSecondWriter(t *testing.T) {
	for _, tc := range []struct {
		name, holder string
		state        store.LaneState
	}{
		{"its registry row is live", "board-refresher-7", store.LaneDesired},
		{"its registry row is held while it posts", "board-refresher-7", store.LaneHeld},
		{"its registry row carries its Orca workspace name", "dar-board-refresher-7", store.LaneRetired},
		{"it posts with no registry row", "dar-board-refresher-7", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a, m, state := newDrive(t, lanes.Root, everyDesk...)
			a.Now = func() time.Time { return t0.Add(time.Minute) }
			wantLane(t, state, "board-refresher-5", recordedBrief(t), store.LaneRetired)
			wantLane(t, state, "board-refresher-6", recordedBrief(t), store.LaneRetired)
			if tc.state != "" {
				wantLane(t, state, tc.holder, recordedBrief(t), tc.state)
			}
			posted(t, state, tc.holder, t0.Add(30*time.Second), 360698)
			var out bytes.Buffer
			if err := start(context.Background(), a, &out); err != nil {
				t.Fatal(err)
			}
			if launched := m.lines("desk-launch.py lane board-refresher"); len(launched) != 0 {
				t.Fatalf("launched a second board writer: %q", launched)
			}
			want := "board-refresher is up as board-refresher-7, newest record #360698; launching another takes `lr request stand-down board-refresher-7 --successor <lane> --open '<items>'` first"
			if !strings.Contains(out.String(), want) {
				t.Fatalf("got %q, want %q", out.String(), want)
			}
		})
	}
}

func TestABriefThatStaysOnTheMacOrIsMissingStartsNothing(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, "fidelity-desk", "red-sweep", "drive-watchdog")
	writeBrief(t, state, "alerts-desk", "ccx: role=watch place=local\n")
	err := start(context.Background(), a, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "alerts-desk") || !strings.Contains(err.Error(), "every desk runs remote") || !strings.Contains(err.Error(), "board-refresher has no brief") {
		t.Fatalf("got %v, want each bad desk named", err)
	}
	if ran := m.lines(""); !slices.Equal(ran, []string{"cci me"}) {
		t.Fatalf("started with a bad brief: %q", ran)
	}
}

func TestOneFailedLaunchIsNamedAndTheOthersStillRun(t *testing.T) {
	a, m, _ := newDrive(t, lanes.Root, everyDesk...)
	m.refuse = "desk-launch.py lane red-sweep"
	var out bytes.Buffer
	err := start(context.Background(), a, &out)
	if err == nil || !strings.Contains(err.Error(), "red-sweep:") {
		t.Fatalf("got %v, want red-sweep named", err)
	}
	if launched := m.lines("desk-launch.py lane"); len(launched) != len(everyDesk) {
		t.Fatalf("got %q, want every launch tried", launched)
	}
	if !strings.Contains(out.String(), "LAUNCH-FAILED") {
		t.Fatalf("the runner's verdict must reach the output: %q", out.String())
	}
}
