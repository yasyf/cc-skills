package desks

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
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

const testDrive = "brook"

var (
	t0            = time.Date(2026, 11, 2, 19, 0, 0, 0, time.UTC)
	everyDesk     = []string{"landing-desk", "fidelity-desk", "red-sweep", "alerts-desk", "drive-watchdog", "board-refresher"}
	onlyDeskBrief = "ccx: role=desk place=remote\nStanding: the duty\n"
)

type machine struct {
	mu     sync.Mutex
	lane   string
	alive  map[string]bool
	starts bool
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
	case strings.HasPrefix(line, "cci tail"):
		for desk, alive := range m.alive {
			if alive && slices.Contains(c.Argv, "--topic="+desk) {
				return shell.Result{Stdout: fmt.Sprintf(`{"at":%q,"kind":"state"}`+"\n", t0.Add(-time.Minute).Format(time.RFC3339Nano))}, nil
			}
		}
		return shell.Result{}, nil
	case m.refuse != "" && strings.Contains(line, m.refuse):
		return shell.Result{Code: 1, Stdout: m.refuse + " LAUNCH-FAILED\n"}, &shell.ExitError{Argv: c.Argv, Code: 1}
	case strings.Contains(line, "desk-runner.py run") && m.starts:
		m.alive[launchRunner] = true
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
	t.Setenv(laneEnv, "")
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
	m := &machine{lane: lane, alive: map[string]bool{}, starts: true}
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

func wantLane(t *testing.T, state, name string, laneState store.LaneState) {
	t.Helper()
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if _, err := s.WantLane(store.Lane{Name: name, Brief: "/b.md", Model: "opus", Effort: "xhigh", Role: "lane", State: laneState}, t0); err != nil {
		t.Fatal(err)
	}
}

func TestACommunicatorBoundWindowStartsNoDesk(t *testing.T) {
	a, m, _ := newDrive(t, communicator, everyDesk...)
	err := start(context.Background(), a, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "the coordinator (lane root) owns desk-runner, the launch runner and every desk") {
		t.Fatalf("got %v, want a refusal naming the coordinator", err)
	}
	if lines := m.lines(""); !slices.Equal(lines, []string{"cci me"}) {
		t.Fatalf("a refused start ran %q", lines)
	}
}

func TestALauncherNamedCommunicatorWindowIsRefusedWithoutAskingCci(t *testing.T) {
	a, m, _ := newDrive(t, "unread", everyDesk...)
	t.Setenv(laneEnv, communicator)
	if err := start(context.Background(), a, &bytes.Buffer{}); err == nil || !strings.Contains(err.Error(), "refused") {
		t.Fatalf("got %v, want a refusal", err)
	}
	if lines := m.lines(""); len(lines) != 0 {
		t.Fatalf("a refused start ran %q", lines)
	}
}

func TestTheCoordinatorStartsEveryDesk(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, everyDesk...)
	var out bytes.Buffer
	if err := start(context.Background(), a, &out); err != nil {
		t.Fatal(err)
	}
	runner := filepath.Join(state, "runner.json")
	if rebound := m.lines("desk-runner.py rebind"); !slices.Equal(rebound, []string{"desk-runner.py rebind --config " + runner}) {
		t.Fatalf("rebound %q", rebound)
	}
	all := m.lines("")
	rebind := slices.IndexFunc(all, func(line string) bool { return strings.HasPrefix(line, "desk-runner.py rebind") })
	start := "desk-runner.py run --config " + runner + " --desk orca"
	launcher := slices.IndexFunc(all, func(line string) bool { return strings.Contains(line, start) })
	if rebind < 0 || launcher < rebind {
		t.Fatalf("the launch runner must start after this terminal takes the Run: %q", all)
	}
	if installed := m.lines("desk-creds.py install"); !slices.Equal(installed, []string{"desk-creds.py install --config " + runner + " --desk landing"}) {
		t.Fatalf("the landing runner must start remote through desk-creds, ran %q", installed)
	}
	launched := m.lines("desk-launch.py lane")
	if len(launched) != len(everyDesk) {
		t.Fatalf("launched %q, want every desk in %q", launched, everyDesk)
	}
	for _, name := range everyDesk {
		want := fmt.Sprintf("desk-launch.py lane %s ", name)
		suffix := fmt.Sprintf("%s --drive %s --lane %s", filepath.Join(state, name, "brief.md"), testDrive, lanes.Root)
		if !slices.ContainsFunc(launched, func(line string) bool { return strings.HasPrefix(line, want) && strings.HasSuffix(line, suffix) }) {
			t.Fatalf("no launch of %s posted as root from its brief in %q", name, launched)
		}
	}
	if !strings.Contains(out.String(), "orca runner started from this Orca terminal") {
		t.Fatalf("the output must say where the launch runner runs: %q", out.String())
	}
}

func TestADeskUpUnderAnyNumberIsNotStartedAgain(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, "fidelity-desk", "red-sweep", "alerts-desk", "drive-watchdog", "board-refresher")
	m.alive = map[string]bool{launchRunner: true, landingRunner: true}
	wantLane(t, state, "landing-desk-3", store.LaneDesired)
	var out bytes.Buffer
	if err := start(context.Background(), a, &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "landing-desk is up as landing-desk-3") {
		t.Fatalf("got %q", out.String())
	}
	if launched := m.lines("desk-launch.py lane landing-desk"); len(launched) != 0 {
		t.Fatalf("relaunched a live desk: %q", launched)
	}
	if started := append(m.lines("desk-creds.py"), m.lines("sh -c")...); len(started) != 0 {
		t.Fatalf("restarted a live runner: %q", started)
	}
	if rebound := m.lines("desk-runner.py rebind"); len(rebound) != 1 {
		t.Fatalf("a relaunched coordinator must still take the Run, ran %q", rebound)
	}
}

func TestARetiredDeskIsStarted(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, everyDesk...)
	wantLane(t, state, "red-sweep-2", store.LaneRetired)
	if err := start(context.Background(), a, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if launched := m.lines("desk-launch.py lane red-sweep "); len(launched) != 1 {
		t.Fatalf("got %q, want red-sweep launched once", launched)
	}
}

func TestABriefThatStaysOnTheMacOrIsMissingStartsNothing(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, "landing-desk", "fidelity-desk", "red-sweep", "drive-watchdog")
	writeBrief(t, state, "alerts-desk", "ccx: role=watch place=local\n")
	err := start(context.Background(), a, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "alerts-desk") || !strings.Contains(err.Error(), "every desk runs remote") || !strings.Contains(err.Error(), "board-refresher has no brief") {
		t.Fatalf("got %v, want each bad desk named", err)
	}
	if ran := m.lines(""); !slices.Equal(ran, []string{"cci me"}) {
		t.Fatalf("started with a bad brief: %q", ran)
	}
}

func TestALaunchRunnerThatNeverBeatsLaunchesNoDesk(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root, everyDesk...)
	m.starts = false
	now := t0
	a.Now = func() time.Time {
		now = now.Add(2 * beatWait)
		return now
	}
	err := start(context.Background(), a, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), filepath.Join(state, "orca-runner.log")) {
		t.Fatalf("got %v, want the runner's log named", err)
	}
	if launched := m.lines("desk-launch.py"); len(launched) != 0 {
		t.Fatalf("launched behind a dead runner: %q", launched)
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
