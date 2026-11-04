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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
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
	return &InputsPublisher{Runner: runner, Session: "board", Config: path, Now: func() time.Time { return *now }}, runner
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
	if len(inputs.Cards) != 1 || inputs.Cards[0].ID != "card-49064" || len(inputs.GoLanes) != 1 || inputs.GoLanes[0] != "go-sweep-10" || len(inputs.TapRules["rule/1"]) != 1 {
		t.Fatalf("inputs = %+v", inputs)
	}
	claim := gates.Claim{Repo: "o/r", PR: 49064, Head: strings.Repeat("a", 40)}
	if _, refusals := gates.ExemptGo(claim, gates.Read{Cards: inputs.Cards, TapRows: inputs.Taps, TapRules: inputs.TapRules, Exempt: inputs.Exempt, GoLanes: inputs.GoLanes}); !strings.Contains(strings.Join(refusals, "; "), "owner card card-49064 is open") {
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
