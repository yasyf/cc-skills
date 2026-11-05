package grading

import (
	"context"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	lrfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const queueHead = "e718a7434fa01ee9835f992a9a647a825350732c"

func pushedAt(number int, change func(*machine.Facts)) lrfacts.Pushed {
	f := machine.Facts{Number: number, Head: queueHead, Lane: "lane-" + strconv.Itoa(number)}
	if change != nil {
		change(&f)
	}
	return lrfacts.Pushed{Facts: f}
}

func queue(t *testing.T, f *poolFixture, grading string, items []store.Item, pushed map[int]lrfacts.Pushed) []QueueRow {
	t.Helper()
	rows, err := f.pool.Queue(context.Background(), grading, items, pushed, f.now)
	if err != nil {
		t.Fatal(err)
	}
	return rows
}

func numbersOf(rows []QueueRow) []int {
	numbers := make([]int, 0, len(rows))
	for _, row := range rows {
		numbers = append(numbers, row.Number)
	}
	return numbers
}

func jobAt(t *testing.T, f *poolFixture, base, result string) string {
	t.Helper()
	directory := filepath.Join(f.pool.root, grader.FidelityLane, base)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, grader.JobFile), []byte(`{}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if result != "" {
		writeResult(t, directory, result, f.now)
	}
	return directory
}

func TestAQueueListsHeadsInThePoolsOrderAndChangesNothing(t *testing.T) {
	f := newPool(t, map[string]any{"fidelity": map[string]any{"sprite": "dar-1"}})
	post(t, client.Draft{Lane: rootLane, Kind: kindDecide, Text: "#1 is first in every grading queue"})
	f.now = time.Now()
	pushed := map[int]lrfacts.Pushed{
		1: pushedAt(1, nil),
		2: pushedAt(2, nil),
		3: pushedAt(3, func(p *machine.Facts) { p.Tap = machine.TapTapped }),
		4: pushedAt(4, func(p *machine.Facts) { p.Lane = "incident-7-fix" }),
		5: pushedAt(5, func(p *machine.Facts) { p.CI, p.Approved = machine.CISuccess, true }),
	}
	items := make([]store.Item, 0, len(pushed))
	snapshot := make(map[int]machine.Facts, len(pushed))
	for _, n := range []int{2, 3, 5, 4, 1} {
		items = append(items, store.Item{ID: int64(n), Kind: ItemKind, Number: n})
		snapshot[n] = pushed[n].Facts
	}

	rows := queue(t, f, GraderFidelity, items, pushed)

	if _, err := os.Stat(filepath.Join(f.pool.root, capacityFile)); !os.IsNotExist(err) {
		t.Fatalf("the read wrote the capacity file: %v", err)
	}
	if len(f.shell.spawns) != 0 {
		t.Fatalf("the read started passes: %+v", f.shell.spawns)
	}
	planned, err := f.pool.Plan(context.Background(), items, snapshot, 0, f.now)
	if err != nil {
		t.Fatal(err)
	}
	want := make([]int, 0, len(planned))
	for _, item := range planned {
		want = append(want, item.Number)
	}
	if got := numbersOf(rows); !slices.Equal(got, want) {
		t.Fatalf("queue = %v, pool plans %v", got, want)
	}
}

func TestAQueueRowSaysWhatPassStandsAtItsHeadAndWhetherAReaderOwesIt(t *testing.T) {
	cases := []struct {
		name  string
		setup func(*testing.T, *poolFixture)
		pass  string
		owed  bool
	}{
		{"no pass yet", func(*testing.T, *poolFixture) {}, PassNone, false},
		{"a pass at an older head says nothing of this one", func(t *testing.T, f *poolFixture) {
			jobAt(t, f, "7-a269784e4900", `{"verdict":"reader"}`)
		}, PassNone, false},
		{"a live pass", func(t *testing.T, f *poolFixture) {
			directory := jobAt(t, f, "7-e718a7434fa0", "")
			if err := os.WriteFile(filepath.Join(directory, pidFile), []byte("41"), 0o600); err != nil {
				t.Fatal(err)
			}
			f.shell.running[41] = "lr grade fidelity " + directory
		}, PassRunning, false},
		{"a pass that handed the head to a reader", func(t *testing.T, f *poolFixture) {
			jobAt(t, f, "7-e718a7434fa0", `{"verdict":"reader","posted":["#1"]}`)
		}, PassReader, true},
		{"a settled pass whose grade is on its way", func(t *testing.T, f *poolFixture) {
			jobAt(t, f, "7-e718a7434fa0", `{"verdict":"ok"}`)
		}, PassSettled, false},
		{"the last attempt speaks for the head", func(t *testing.T, f *poolFixture) {
			jobAt(t, f, "7-e718a7434fa0", `{"error":"git failed"}`)
			jobAt(t, f, "7-e718a7434fa0-2", `{"verdict":"reader"}`)
		}, PassReader, true},
		{"a failed pass", func(t *testing.T, f *poolFixture) {
			jobAt(t, f, "7-e718a7434fa0", `{"error":"git failed"}`)
		}, PassFailed, false},
		{"a pass that died with no result", func(t *testing.T, f *poolFixture) {
			jobAt(t, f, "7-e718a7434fa0", "")
		}, PassLost, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newPool(t, map[string]any{"fidelity": map[string]any{}})
			c.setup(t, f)

			rows := queue(t, f, GraderFidelity, nil, map[int]lrfacts.Pushed{7: pushedAt(7, nil)})

			if len(rows) != 1 || rows[0].Pipeline != c.pass || rows[0].Owed != c.owed {
				t.Fatalf("rows = %+v, want pass %s owed %v", rows, c.pass, c.owed)
			}
		})
	}
}

func TestALivePassReadsRunningWhenTheStateDirectoryIsReachedThroughASymlink(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	state, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(t.TempDir(), "state")
	if err := os.Symlink(state, link); err != nil {
		t.Fatal(err)
	}
	runner := `{"drive": "d", "orca": {"desk_inbox": "/i", "run": "r", "briefs": {"repo": "/b"}}, "fidelity": {}}`
	if err := os.WriteFile(filepath.Join(state, runnerConfig), []byte(runner), 0o600); err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(state, grader.FidelityLane, "7-e718a7434fa0")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, pidFile), []byte("41"), 0o600); err != nil {
		t.Fatal(err)
	}
	shell := &poolShell{running: map[int]string{41: "sprite-job.sh dar-1 fidelity " + directory}}
	a := &app.App{Runner: shell, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}
	pool, err := readingPool(a, link, memory(t))
	if err != nil {
		t.Fatal(err)
	}

	rows, err := pool.Queue(context.Background(), GraderFidelity, nil, map[int]lrfacts.Pushed{7: pushedAt(7, nil)}, time.Now())
	if err != nil {
		t.Fatal(err)
	}

	if len(rows) != 1 || rows[0].Pipeline != PassRunning {
		t.Fatalf("rows = %+v, want the live pass read as running", rows)
	}
}

func TestAHeadThePipelineStoppedOnStaysOwedAfterItsGradeItemCloses(t *testing.T) {
	f := newPool(t, map[string]any{"fidelity": map[string]any{}})

	rows := queue(t, f, GraderFidelity, nil, map[int]lrfacts.Pushed{7: pushedAt(7, func(p *machine.Facts) { p.GradeStopped = "no reader posted" })})

	if len(rows) != 1 || !rows[0].Owed {
		t.Fatalf("rows = %+v", rows)
	}
}

func TestOnlyTheGraderAHeadStillOwesListsIt(t *testing.T) {
	f := newPool(t, map[string]any{"fidelity": map[string]any{}, "finder": map[string]any{}})
	pushed := map[int]lrfacts.Pushed{
		7: pushedAt(7, func(p *machine.Facts) { p.Fidelity = machine.FidelityPass }),
		8: pushedAt(8, func(p *machine.Facts) { p.Fidelity = machine.FidelityFail }),
		9: pushedAt(9, func(p *machine.Facts) { p.Draft = true }),
	}

	if got := numbersOf(queue(t, f, GraderFidelity, nil, pushed)); len(got) != 0 {
		t.Fatalf("fidelity queue = %v", got)
	}
	if got := numbersOf(queue(t, f, GraderFinder, nil, pushed)); !slices.Equal(got, []int{7, 8}) {
		t.Fatalf("finder queue = %v", got)
	}
}

func TestAPRWithTwoOpenItemsIsOneRowAtItsPushedHead(t *testing.T) {
	f := newPool(t, map[string]any{"fidelity": map[string]any{}})
	items := []store.Item{
		{ID: 1, Kind: ItemKind, Number: 7, Head: older},
		{ID: 2, Kind: "restack", Number: 7, Head: queueHead},
		{ID: 3, Kind: ItemKind, Number: 7, Head: queueHead},
	}
	pushed := map[int]lrfacts.Pushed{7: pushedAt(7, nil)}
	row := pushed[7]
	row.RowHead = older
	pushed[7] = row

	rows := queue(t, f, GraderFidelity, items, pushed)

	if len(rows) != 1 || rows[0].Head != queueHead || rows[0].RowHead != older {
		t.Fatalf("rows = %+v", rows)
	}
}

func TestAQueueRefusesAGraderWithNoDesk(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})

	if _, err := f.pool.Queue(context.Background(), GraderFidelity, nil, nil, f.now); err == nil {
		t.Fatal("a queue for a desk runner.json does not name read as empty")
	}
}

func TestAShardKeepsItsParityAndNoShardKeepsEveryHead(t *testing.T) {
	for _, c := range []struct {
		number int
		shard  string
		want   bool
	}{{4, shardEven, true}, {5, shardEven, false}, {5, shardOdd, true}, {4, shardOdd, false}, {4, "", true}, {5, "", true}} {
		if got := inShard(c.number, c.shard); got != c.want {
			t.Errorf("inShard(%d, %q) = %v", c.number, c.shard, got)
		}
	}
}

func TestAQueueLineNamesARowBehindThePushAndAHeadWithNoStamp(t *testing.T) {
	row := QueueRow{Number: 7, Head: queueHead, RowHead: older, CI: machine.CIPending, Pipeline: PassReader, Owed: true, Held: true, Lane: "lane-7", Title: "a change"}

	line := row.line(t0)

	for _, want := range []string{"#7 e718a7434fa0 owed", "ci=pending", "pipeline=reader", "age=new", "held", "row-at=" + short(older), "| a change"} {
		if !strings.Contains(line, want) {
			t.Errorf("%q lacks %q", line, want)
		}
	}
}
