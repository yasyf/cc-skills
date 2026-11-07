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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	testDrive     = "durable-agents-rebuild"
	onlyDeskBrief = "ccx: role=desk place=remote\nStanding: the duty\n"
	oneShotBrief  = "ccx: role=fix place=remote\nDeliverable: one PR\n"
)

var (
	t0        = time.Date(2026, 11, 2, 19, 0, 0, 0, time.UTC)
	everyDesk = []string{"fidelity-desk", "red-sweep", "alerts-desk", "drive-watchdog", "board-refresher", "install-desk", "obs-desk", "old-tree-port-desk", "meta-desk"}
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

func newDrive(t *testing.T, lane string) (*app.App, *machine, string) {
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
	m := &machine{lane: lane}
	return &app.App{ConfigPath: path, Runner: m, Now: func() time.Time { return t0 }}, m, state
}

func briefOf(t *testing.T, text string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "brief.md")
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func opened(t *testing.T, state string) *store.Store {
	t.Helper()
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func put(t *testing.T, state string, rows ...store.Lane) {
	t.Helper()
	s := opened(t, state)
	for _, row := range rows {
		row.Model, row.Effort, row.Role = "opus", "high", "lane"
		if _, err := s.WantLane(row, t0); err != nil {
			t.Fatal(err)
		}
	}
}

func down(t *testing.T, names ...string) []store.Lane {
	t.Helper()
	brief := briefOf(t, onlyDeskBrief)
	rows := make([]store.Lane, 0, len(names))
	for _, name := range names {
		rows = append(rows, store.Lane{Name: name, Brief: brief, State: store.LaneRetired})
	}
	return rows
}

func posted(t *testing.T, state, name string, at time.Time, seq int64) {
	t.Helper()
	if err := opened(t, state).LaneHeard(name, at, "report", seq); err != nil {
		t.Fatal(err)
	}
}

func TestACommunicatorBoundWindowStartsNoDesk(t *testing.T) {
	a, m, _ := newDrive(t, communicator)
	err := start(context.Background(), a, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "the coordinator (lane root) owns every desk") {
		t.Fatalf("got %v, want a refusal naming the coordinator", err)
	}
	if lines := m.lines(""); !slices.Equal(lines, []string{"cci me"}) {
		t.Fatalf("a refused start ran %q", lines)
	}
}

func TestALauncherNamedCommunicatorWindowIsRefusedWithoutAskingCci(t *testing.T) {
	a, m, _ := newDrive(t, "unread")
	t.Setenv(lanes.LaneEnv, communicator)
	if err := start(context.Background(), a, &bytes.Buffer{}); err == nil || !strings.Contains(err.Error(), "refused") {
		t.Fatalf("got %v, want a refusal", err)
	}
	if lines := m.lines(""); len(lines) != 0 {
		t.Fatalf("a refused start ran %q", lines)
	}
}

func TestTheCoordinatorRelaunchesEveryDownStandingDeskTheRegistryHolds(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root)
	rows := make([]string, 0, len(everyDesk))
	for _, name := range everyDesk {
		rows = append(rows, name+"-3")
	}
	put(t, state, down(t, rows...)...)
	if err := start(context.Background(), a, io.Discard); err != nil {
		t.Fatal(err)
	}
	all := m.lines("")
	rebind := slices.Index(all, "lr relay rebind --config "+filepath.Join(state, "lr.json"))
	first := slices.IndexFunc(all, func(line string) bool { return strings.HasPrefix(line, "desk-launch.py") })
	if rebind < 0 || first < rebind {
		t.Fatalf("this terminal must take the Run before any desk launches: %q", all)
	}
	launched := m.lines("desk-launch.py lane")
	if len(launched) != len(everyDesk) {
		t.Fatalf("launched %q, want every standing desk in %q", launched, everyDesk)
	}
	for _, name := range everyDesk {
		want := fmt.Sprintf("desk-launch.py lane %s-4 ", name)
		suffix := fmt.Sprintf("%s --drive %s --lane %s", filepath.Join(state, name+"-4", "brief.md"), testDrive, lanes.Root)
		if !slices.ContainsFunc(launched, func(line string) bool { return strings.HasPrefix(line, want) && strings.HasSuffix(line, suffix) }) {
			t.Fatalf("no launch of %s-4 posted as the coordinator from its brief in %q", name, launched)
		}
	}
	for _, cost := range []string{"lane fidelity-desk-4 opus medium ", "lane alerts-desk-4 sonnet low ", "lane install-desk-4 opus medium ", "lane obs-desk-4 opus high "} {
		if !slices.ContainsFunc(launched, func(line string) bool { return strings.Contains(line, cost) }) {
			t.Fatalf("no launch at %q in %q", cost, launched)
		}
	}
}

func TestOnlyAStandingDutyHeldByNoLiveLaneIsRelaunched(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root)
	duty, oneShot := briefOf(t, onlyDeskBrief), briefOf(t, oneShotBrief)
	put(t, state,
		store.Lane{Name: "fix-lane-3", Brief: oneShot, State: store.LaneRetired},
		store.Lane{Name: lanes.Root, Brief: duty, State: store.LaneRetired},
		store.Lane{Name: communicator, Brief: duty, State: store.LaneRetired},
		store.Lane{Name: "stable-after-next-head-2", Brief: duty, State: store.LaneRetired},
		store.Lane{Name: "stable-after-next-head-3", Brief: oneShot, State: store.LaneRetired},
		store.Lane{Name: "go-desk-8", Brief: duty, State: store.LaneRetired, RetiredBy: "landing-pass-1"},
		store.Lane{Name: "landing-pass-1", Brief: oneShot, State: store.LaneDesired},
	)
	if err := start(context.Background(), a, io.Discard); err != nil {
		t.Fatal(err)
	}
	if launched := m.lines("desk-launch.py"); len(launched) != 0 {
		t.Fatalf("launched %q, want nothing: no down lane holds a duty of its own", launched)
	}
}

func TestADutyOnlyTheRowCarriesIsWrittenIntoTheSuccessorsBrief(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root)
	put(t, state, store.Lane{Name: "board-refresher-14", Brief: briefOf(t, "ccx: role=read place=remote\n"), State: store.LaneRetired, Standing: "refresh the owner's cards"})
	if err := start(context.Background(), a, io.Discard); err != nil {
		t.Fatal(err)
	}
	if launched := m.lines("desk-launch.py lane board-refresher-15 "); len(launched) != 1 {
		t.Fatalf("launched %q, want board-refresher-15 once", m.lines("desk-launch.py"))
	}
	written, err := os.ReadFile(filepath.Join(state, "board-refresher-15", "brief.md"))
	if err != nil || string(written) != "Standing: refresh the owner's cards\nccx: role=read place=remote\n" {
		t.Fatalf("the successor's brief holds %q (%v), want its duty's Standing: line", written, err)
	}
}

func TestADeskUpUnderAnyNumberIsNotStartedAgain(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root)
	put(t, state, down(t, "fidelity-desk-2")...)
	put(t, state, store.Lane{Name: "fidelity-desk-3", Brief: briefOf(t, onlyDeskBrief), State: store.LaneDesired})
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

func TestADownDeskRelaunchesUnderItsNextNameFromItsRecordedBrief(t *testing.T) {
	for _, state := range []store.LaneState{store.LaneHeld, store.LaneRetired} {
		t.Run(string(state), func(t *testing.T) {
			a, m, dir := newDrive(t, lanes.Root)
			put(t, dir, store.Lane{Name: "fidelity-desk-3", Brief: briefOf(t, onlyDeskBrief), State: state})
			var out bytes.Buffer
			if err := start(context.Background(), a, &out); err != nil {
				t.Fatal(err)
			}
			successor := filepath.Join(dir, "fidelity-desk-4", "brief.md")
			want := fmt.Sprintf("desk-launch.py lane fidelity-desk-4 opus medium %s --drive %s --lane %s", successor, testDrive, lanes.Root)
			if launched := m.lines("desk-launch.py lane fidelity-desk"); !slices.Equal(launched, []string{want}) {
				t.Fatalf("launched %q, want %q", launched, want)
			}
			if copied, err := os.ReadFile(successor); err != nil || string(copied) != onlyDeskBrief {
				t.Fatalf("the successor's brief holds %q (%v), want fidelity-desk-3's recorded brief", copied, err)
			}
			if !strings.Contains(out.String(), "fidelity-desk is "+string(state)+" as fidelity-desk-3, so fidelity-desk-4 launches") {
				t.Fatalf("got %q", out.String())
			}
		})
	}
}

func TestADownDeskNeverTakesANameAnotherLanePostedUnder(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root)
	put(t, state, down(t, "red-sweep-2")...)
	posted(t, state, "red-sweep-3", t0.Add(-time.Hour), 7)
	if err := start(context.Background(), a, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if launched := m.lines("desk-launch.py lane red-sweep"); len(launched) != 1 || !strings.HasPrefix(launched[0], "desk-launch.py lane red-sweep-4 ") {
		t.Fatalf("got %q, want red-sweep-4 launched once", launched)
	}
}

func TestARoleWhoseRowPostsUnderAnyNameLaunchesNoSecondWriter(t *testing.T) {
	for _, tc := range []struct {
		name, holder string
		state        store.LaneState
	}{
		{"its registry row is live", "board-refresher-7", store.LaneDesired},
		{"its registry row is held while it posts", "board-refresher-7", store.LaneHeld},
		{"its registry row carries its Orca workspace name", "dar-board-refresher-7", store.LaneRetired},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a, m, state := newDrive(t, lanes.Root)
			a.Now = func() time.Time { return t0.Add(time.Minute) }
			put(t, state, down(t, "board-refresher-5", "board-refresher-6")...)
			put(t, state, store.Lane{Name: tc.holder, Brief: briefOf(t, onlyDeskBrief), State: tc.state})
			posted(t, state, tc.holder, t0.Add(30*time.Second), 360698)
			var out bytes.Buffer
			if err := start(context.Background(), a, &out); err != nil {
				t.Fatal(err)
			}
			if launched := m.lines("desk-launch.py lane board-refresher"); len(launched) != 0 {
				t.Fatalf("launched a second board writer: %q", launched)
			}
			want := "board-refresher is up as board-refresher-7; launching another takes `lr request stand-down board-refresher-7 --successor <lane> --open '<items>'` first"
			if !strings.Contains(out.String(), want) {
				t.Fatalf("got %q, want %q", out.String(), want)
			}
		})
	}
}

func TestADeskBriefOnTheMacWithNoMacNeedLaunchesRemoteAndAMissingBriefFaultsOnlyItsOwnDesk(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root)
	put(t, state, down(t, "fidelity-desk-3", "red-sweep-3", "drive-watchdog-3")...)
	put(t, state,
		store.Lane{Name: "alerts-desk-3", Brief: briefOf(t, "ccx: role=watch place=local\nStanding: watch the monitors\n"), State: store.LaneRetired},
		store.Lane{Name: "install-desk-3", Brief: briefOf(t, "ccx: role=desk place=local\nStanding: install merged packs\n"), State: store.LaneRetired},
		store.Lane{Name: "obs-desk", State: store.LaneRetired, Standing: "answer reads"},
	)
	err := start(context.Background(), a, &bytes.Buffer{})
	if err == nil || strings.Contains(err.Error(), "alerts-desk") || !strings.Contains(err.Error(), "obs-desk has no brief") {
		t.Fatalf("got %v, want only the desk with no brief named", err)
	}
	placed, readErr := os.ReadFile(filepath.Join(state, "alerts-desk-4", "brief.md"))
	if readErr != nil || !strings.HasPrefix(string(placed), "ccx: place=remote\nccx: role=watch place=local\n") {
		t.Fatalf("alerts-desk launched from %q (%v), want its brief placed remote", placed, readErr)
	}
	installer, readErr := os.ReadFile(filepath.Join(state, "install-desk-4", "brief.md"))
	if readErr != nil || !strings.HasPrefix(string(installer), "ccx: place=local why=mac-build\nccx: role=desk place=local\n") {
		t.Fatalf("install-desk launched from %q (%v), want its brief placed on the Mac for its install", installer, readErr)
	}
	for _, good := range []string{"fidelity-desk", "red-sweep", "drive-watchdog", "alerts-desk", "install-desk"} {
		if strings.Contains(err.Error(), good) {
			t.Fatalf("got %v, want only the bad desks named", err)
		}
		if launched := m.lines("desk-launch.py lane " + good + "-4 "); len(launched) != 1 {
			t.Fatalf("%s launched %q, want once", good, launched)
		}
	}
	if launched := m.lines("desk-launch.py lane"); len(launched) != 4 {
		t.Fatalf("launched %q, want every desk with a brief", launched)
	}
}

func TestOneFailedLaunchIsNamedAndTheOthersStillRun(t *testing.T) {
	a, m, state := newDrive(t, lanes.Root)
	rows := make([]string, 0, len(everyDesk))
	for _, name := range everyDesk {
		rows = append(rows, name+"-3")
	}
	put(t, state, down(t, rows...)...)
	m.refuse = "desk-launch.py lane red-sweep"
	var out bytes.Buffer
	err := start(context.Background(), a, &out)
	if err == nil || !strings.Contains(err.Error(), "red-sweep-4:") {
		t.Fatalf("got %v, want red-sweep named", err)
	}
	if launched := m.lines("desk-launch.py lane"); len(launched) != len(everyDesk) {
		t.Fatalf("got %q, want every launch tried", launched)
	}
	if !strings.Contains(out.String(), "LAUNCH-FAILED") {
		t.Fatalf("the runner's verdict must reach the output: %q", out.String())
	}
}

type countedRegistry struct {
	registry
	reads int
}

func (c *countedRegistry) AllLanes() ([]store.Lane, error) {
	c.reads++
	return c.registry.AllLanes()
}

func (c *countedRegistry) Heard() (map[string]time.Time, error) {
	c.reads++
	return c.registry.Heard()
}

func TestDesksStartReadsTheRegistryTheSameAtOneLaneAndAtMany(t *testing.T) {
	reads := func(n int) int {
		a, _, state := newDrive(t, lanes.Root)
		brief := briefOf(t, onlyDeskBrief)
		rows := make([]store.Lane, n)
		for i := range rows {
			rows[i] = store.Lane{Name: fmt.Sprintf("desk-%d-1", i), Brief: brief, Model: "opus", Effort: "high", Role: "lane", State: []store.LaneState{store.LaneDesired, store.LaneRetired}[i%2]}
		}
		s := opened(t, state)
		if err := s.PutLanes(rows, t0); err != nil {
			t.Fatal(err)
		}
		d, err := loaded(a)
		if err != nil {
			t.Fatal(err)
		}
		counted := &countedRegistry{registry: s}
		owed, _, err := d.owed(counted, io.Discard)
		if err != nil {
			t.Fatal(err)
		}
		if len(owed) != n/2 {
			t.Fatalf("owed %d launches over %d desks, want every down one", len(owed), n)
		}
		return counted.reads
	}
	if one, many := reads(2), reads(scaletest.N()); one != many {
		t.Fatalf("lr desks start read the store %d times over 2 lanes and %d over %d", one, many, scaletest.N())
	}
}
