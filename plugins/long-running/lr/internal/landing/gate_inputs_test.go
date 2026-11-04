package landing

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type boardRunner struct {
	stdout string
	reads  int
}

func (r *boardRunner) Run(_ context.Context, _ shell.Command) (shell.Result, error) {
	r.reads++
	return shell.Result{Stdout: r.stdout}, nil
}

const openBoard = `{"rev":1,"doc":{"blocks":[{"type":"card","id":"card-49064","title":"May #49064 land?","blocks":[{"type":"choice","id":"choice-49064","prompt":"May it land?"}]}]},"interactions":{}}`

func publisher(t *testing.T, stdout string, now *time.Time) (*InputsPublisher, *boardRunner) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	dir := t.TempDir()
	runners := `{"drive":"d","orca":{"briefs":{"repo":"o/r"},"desk_inbox":"i","run":"r"},"landing":{"go_classes":["class owner-pick"],"tap_rules":{"rule/1":["go/sand/*"]}}}`
	if err := os.WriteFile(filepath.Join(dir, runnerFile), []byte(runners), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := `{"repo":"o/r","drive":"d","checkout":"/repo","author":"a","label":"l","movers":["lanes"],"go_lanes":"go-sweep-10"}`
	path := filepath.Join(dir, "lr.json")
	if err := os.WriteFile(path, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	runner := &boardRunner{stdout: stdout}
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return &InputsPublisher{
		Runner: runner, Store: s, Session: "board", Config: path, Now: func() time.Time { return *now },
		FixLane:    func(lane string) (bool, error) { return strings.HasPrefix(lane, "dd-1-fix"), nil },
		LiveClocks: func() ([]string, error) { return []string{"dd-1-fix-2"}, nil },
	}, runner
}

func lane(t *testing.T, s *store.Store, name string, state store.LaneState, retiredBy string, at time.Time, prs ...int) {
	t.Helper()
	if _, err := s.WantLane(store.Lane{Name: name, State: store.LaneDesired, PRs: prs}, at); err != nil {
		t.Fatal(err)
	}
	if state != store.LaneDesired {
		if err := s.SetLaneState(name, state, at, retiredBy, ""); err != nil {
			t.Fatal(err)
		}
	}
}

func mirrored(t *testing.T, p *InputsPublisher) *InputsMirror {
	t.Helper()
	if err := p.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	m := &InputsMirror{Drive: "d", Store: s, CopyLanes: true, Now: p.Now}
	if err := m.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	return m
}

func posted(t *testing.T) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"note"}, Lanes: []string{self}})
	if err != nil {
		t.Fatal(err)
	}
	return records
}

func TestASpriteReadsTheBoardsOpenCardAndTheRunnersRulesFromTheRecordTheMacPosted(t *testing.T) {
	now := time.Now()
	p, _ := publisher(t, openBoard, &now)
	if err := p.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	inputs, err := readGateInputs(context.Background(), "d", now)
	if err != nil {
		t.Fatal(err)
	}
	if len(inputs.Cards) != 1 || inputs.Cards[0].ID != "card-49064" || len(inputs.GoLanes) != 1 || inputs.GoLanes[0] != "go-sweep-10" || len(inputs.TapRules["rule/1"]) != 1 || inputs.Session != "board" {
		t.Fatalf("inputs = %+v", inputs)
	}
	claim := gates.Claim{Repo: "o/r", PR: 49064, Head: strings.Repeat("a", 40)}
	if _, refusals := gates.ExemptGo(claim, gates.Read{Cards: inputs.Cards, TapRules: inputs.TapRules, Exempt: inputs.Exempt, GoLanes: inputs.GoLanes}); !strings.Contains(strings.Join(refusals, "; "), "owner card card-49064 is open") {
		t.Fatalf("the gate did not refuse on the published card: %q", refusals)
	}
}

func TestAnUnchangedBoardPostsOnlyWhenTheRecordWouldGoStale(t *testing.T) {
	now := time.Now()
	p, runner := publisher(t, openBoard, &now)
	for _, step := range []time.Duration{0, time.Minute, 3 * time.Minute} {
		now = now.Add(step)
		if err := p.Pass(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	if got := len(posted(t)); got != 1 {
		t.Fatalf("an unchanged board posted %d records in 4 minutes, want 1", got)
	}
	now = now.Add(inputsRepost)
	if err := p.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := len(posted(t)); got != 2 {
		t.Fatalf("after the repost interval there are %d records, want 2", got)
	}
	if runner.reads != 4 {
		t.Fatalf("board read %d times, want once a pass", runner.reads)
	}
}

func TestAChangedBoardPostsAtOnce(t *testing.T) {
	now := time.Now()
	p, runner := publisher(t, openBoard, &now)
	if err := p.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	runner.stdout = `{"rev":2,"doc":{"blocks":[]},"interactions":{}}`
	now = now.Add(time.Minute)
	if err := p.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := len(posted(t)); got != 2 {
		t.Fatalf("a changed board left %d records, want 2", got)
	}
	inputs, err := readGateInputs(context.Background(), "d", now)
	if err != nil {
		t.Fatal(err)
	}
	if len(inputs.Cards) != 0 {
		t.Fatalf("the gate read the older record: %+v", inputs.Cards)
	}
}

func TestAGateWithNoFreshRecordRefusesToReadTheBoardAsEmpty(t *testing.T) {
	now := time.Now()
	p, _ := publisher(t, openBoard, &now)
	if _, err := readGateInputs(context.Background(), "d", now); err == nil || !strings.Contains(err.Error(), gateInputsTopic) {
		t.Fatalf("no record read as %v", err)
	}
	if err := p.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := readGateInputs(context.Background(), "d", now.Add(inputsStale+time.Minute)); err == nil {
		t.Fatal("a record older than the stale limit was read as the board")
	}
}

func TestALandingOnlyStoreRoutesEachLaneWhereTheLanesMachineDoes(t *testing.T) {
	now := time.Now()
	p, _ := publisher(t, openBoard, &now)
	lane(t, p.Store, "api-fix-2", store.LaneDesired, "", now)
	lane(t, p.Store, "api-fix", store.LaneRetired, "api-fix-2", now, 5)
	lane(t, p.Store, "parked", store.LaneHeld, "", now)
	lane(t, p.Store, "done", store.LaneRetired, "", now, 6)
	m := mirrored(t, p)

	owners, err := m.Store.OwnerOf([]int{5, 6})
	if err != nil {
		t.Fatal(err)
	}
	if owners[5] != "api-fix" || owners[6] != "" {
		t.Fatalf("owners = %v, want #5 owned by api-fix and #6 by no mirrored lane", owners)
	}
	for name, want := range map[string]string{owners[5]: "api-fix-2", "parked": lanes.Root, "done": lanes.Root} {
		if got, err := lanes.Running(m.Store, "d", name); err != nil || got != want {
			t.Fatalf("Running(%s) = %q, %v; want %q", name, got, err, want)
		}
	}
	if got, err := lanes.Holder(m.Store, "d", "parked"); err != nil || got != "parked" {
		t.Fatalf("Holder(parked) = %q, %v", got, err)
	}
	if clocks, err := m.LiveClocks(); err != nil || len(clocks) != 1 || clocks[0] != "dd-1-fix-2" {
		t.Fatalf("clocks = %v, %v", clocks, err)
	}
}

func TestAFixLaneTheLanesMachineLaunchedStillSquashesOnALandingOnlyMachine(t *testing.T) {
	now := time.Now()
	p, _ := publisher(t, openBoard, &now)
	lane(t, p.Store, "dd-1-fix", store.LaneRetired, "", now.Add(-time.Hour), 7)
	lane(t, p.Store, "dd-1-fix-old", store.LaneRetired, "", now.Add(-incidentHorizon-time.Hour), 8)
	lane(t, p.Store, "ordinary", store.LaneDesired, "", now, 9)
	m := mirrored(t, p)
	q := &Squash{Store: m.Store, Trunk: "dev", FixLane: m.FixLane}

	owners, err := m.Store.OwnerOf([]int{7, 8, 9})
	if err != nil {
		t.Fatal(err)
	}
	snapshot := make([]machine.Facts, 0, len(owners))
	for _, number := range []int{7, 8, 9} {
		snapshot = append(snapshot, machine.Facts{Repo: "o/r", Number: number, Base: "dev", Lane: owners[number]})
	}
	named, err := q.tagged(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if len(named) != 1 || named[0].Number != 7 {
		t.Fatalf("squash takes %+v, want only #7 from the retired fix lane", named)
	}
}

func TestALandingOnlyMachineWithNoInputsRefusesToCallAnyLaneAFix(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	m := &InputsMirror{Drive: "d", Now: time.Now}
	if err := m.Pass(context.Background()); err == nil {
		t.Fatal("a mirror with no inputs record read")
	}
	if _, err := m.FixLane("dd-1-fix"); err == nil {
		t.Fatal("a mirror with no inputs record answered whether a lane is a fix")
	}
}

func TestABadInputsRecordLeavesTheGateOnTheRecordBeforeIt(t *testing.T) {
	now := time.Now()
	p, _ := publisher(t, openBoard, &now)
	bad := client.Draft{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "bad", Text: "bad", Attrs: map[string]string{gateInputsAttr: `{""cards"":[]}`}}
	if _, err := client.Post(context.Background(), bad); err != nil {
		t.Fatal(err)
	}
	if _, err := readGateInputs(context.Background(), "d", now); err == nil || !strings.Contains(err.Error(), gateInputsTopic) {
		t.Fatalf("a bad record alone read as %v", err)
	}
	if err := p.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	bad.ID = "bad-2"
	if _, err := client.Post(context.Background(), bad); err != nil {
		t.Fatal(err)
	}
	inputs, err := readGateInputs(context.Background(), "d", now)
	if err != nil {
		t.Fatal(err)
	}
	if len(inputs.Cards) != 1 || inputs.Cards[0].ID != "card-49064" {
		t.Fatalf("the gate did not read the record before the bad one: %+v", inputs)
	}
}
