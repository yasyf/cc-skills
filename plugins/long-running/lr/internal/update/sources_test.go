package update

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
)

func src(lanes []Lane, records []Record, tasks []ClaudeTask, boardErr error) Sources {
	return Sources{
		Lanes: func(context.Context) ([]Lane, error) { return lanes, nil },
		Records: func(_ context.Context, since time.Time, kinds ...string) ([]Record, error) {
			var out []Record
			for _, record := range records {
				if record.At.Before(since) || (len(kinds) > 0 && !slices.Contains(kinds, record.Kind)) {
					continue
				}
				out = append(out, record)
			}
			return out, nil
		},
		Plans: func(context.Context) ([]*plans.Plan, error) { return nil, nil },
		Book:  func(context.Context) (asks.Book, error) { return asks.Book{PRs: map[string]asks.PR{}}, nil },
		Board: func(context.Context, []board.Snapshot) (board.Live, error) { return board.Live{}, boardErr },
		Tasks: func() ([]ClaudeTask, error) { return tasks, nil },
	}
}

func TestCollectSplitsOneDayOfRecordsIntoEachSectionAndReadsHoldsOfAnyAge(t *testing.T) {
	ask := record(1, "ship-2", "ask", "may I", 10)
	ask.Status = "open"
	answered := record(2, "ship-2", "ask", "may I again", 10)
	answered.Status = "resolved"
	toPeer := record(3, "ship-2", "blocker", "peer", 10)
	toPeer.Status, toPeer.To = "open", []string{"other-1"}
	incident := record(4, "api-fix", "incident", "SCOPE", 20)
	yesterday := record(5, "api-fix", "incident", "old", 23*60)
	closing := record(6, "root", "decide", "INCIDENT api CLOSED", 5)
	release := record(7, "rel-1", "release", "started", 5)
	hold := record(8, "root", "hold", "hold #48401 until the fix", 72*60)
	hold.Status = "open"
	in, err := Collect(context.Background(), src([]Lane{heard("ship-2", "desired", 5)}, []Record{ask, answered, toPeer, incident, yesterday, closing, release, hold}, nil, nil), testDrive, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(in.Waits) != 1 || in.Waits[0].Seq != 1 {
		t.Fatalf("waits = %+v", in.Waits)
	}
	if len(in.Records) != 3 {
		t.Fatalf("a non-working lane's records stayed: %+v", in.Records)
	}
	if got := seqs(in.Incidents); !reflect.DeepEqual(got, []int64{4, 6}) {
		t.Fatalf("incidents = %v", got)
	}
	if got := seqs(in.Releases); !reflect.DeepEqual(got, []int64{7}) {
		t.Fatalf("releases = %v", got)
	}
	if in.Held["48401"] != 8 {
		t.Fatalf("held = %v", in.Held)
	}
}

func seqs(records []Record) []int64 {
	out := make([]int64, len(records))
	for i, record := range records {
		out[i] = record.Seq
	}
	return out
}

func TestAHoldHoldsTheOnePRItsTextLeadsWithElseEveryPRItsRefsName(t *testing.T) {
	lead := Record{Seq: 1, Kind: "hold", Status: "open", Text: "HOLD #7 because of #8", PRs: []string{"9"}}
	refs := Record{Seq: 2, Kind: "hold", Status: "open", Text: "stop these", PRs: []string{"org/x#10", "11"}}
	closed := Record{Seq: 3, Kind: "hold", Status: "resolved", Text: "hold #12"}
	want := map[string]int64{"7": 1, "10": 2, "11": 2}
	if got := heldPRs([]Record{lead, refs, closed}); !reflect.DeepEqual(got, want) {
		t.Fatalf("held = %v", got)
	}
}

func TestABoardThatDoesNotReadIsNamedAndTheRestStillRenders(t *testing.T) {
	in, err := Collect(context.Background(), src(nil, nil, nil, errors.New("cc-present outcomes exited 1: no board")), testDrive, now)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(in.Board, []string{"board unread: cc-present outcomes exited 1: no board"}) {
		t.Fatalf("board = %v", in.Board)
	}
	if !strings.Contains(Render(in).Text, "- board unread: cc-present outcomes exited 1: no board") {
		t.Fatal("the render dropped the board's failure")
	}
}

func TestEachOpenTaskLaneWithAReportSinceTheLastUpdateIsDeliveredOnceAtItsNewest(t *testing.T) {
	tasks := []ClaudeTask{
		{"10", "Scenario list", "pending", "devbox-explore-7"},
		{"11", "Perf worker scenarios", "in_progress", "devbox-explore-7"},
		{"12", "Finished long ago", "completed", "old-lane-1"},
		{"13", "Quiet work", "in_progress", "quiet-lane-1"},
	}
	records := []Record{
		record(1, "devbox-explore-7", "report", "scenario 2 measured", 90),
		record(2, "devbox-explore-7", "report", "scenario 3 measured, see #47525", 30),
		record(3, "devbox-explore-7", "state", "still measuring", 5),
		record(4, "old-lane-1", "report", "done again", 10),
		record(5, "quiet-lane-1", "report", "before the last update", 200),
	}
	status, err := ReadStatus(context.Background(), src(nil, records, tasks, nil), now, now.Add(-2*time.Hour), nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(status.Delivered) != 1 {
		t.Fatalf("delivered = %+v", status.Delivered)
	}
	item := status.Delivered[0]
	if item.Task != "11" || item.Lane != "devbox-explore-7" || item.Seq != 2 || !reflect.DeepEqual(item.PRs, []string{"47525"}) {
		t.Fatalf("item = %+v", item)
	}
	if !strings.HasPrefix(item.Line, "delivered #11 devbox-explore-7: Perf worker scenarios | report #2 ") {
		t.Fatalf("line = %q", item.Line)
	}
	if got := status.InFlight[1].Line; !strings.HasSuffix(got, "Quiet work | last report "+pacific(ago(200))) {
		t.Fatalf("in flight = %q", got)
	}
}

func TestOnlyLimitsTheReadsToTheSectionsItNames(t *testing.T) {
	sources := src(nil, nil, nil, nil)
	sources.Book = func(context.Context) (asks.Book, error) {
		return asks.Book{}, errors.New("the ledger must not be read")
	}
	sources.Board = func(context.Context, []board.Snapshot) (board.Live, error) {
		return board.Live{}, errors.New("the board must not be read")
	}
	status, err := ReadStatus(context.Background(), sources, now, now.Add(-time.Hour), nil, []string{"delivered"})
	if err != nil {
		t.Fatal(err)
	}
	if status.Asks != nil || status.Board != nil {
		t.Fatalf("status = %+v", status)
	}
}

func TestATaskListSortsByNumberAndAnEmptyFolderIsNoTasks(t *testing.T) {
	home := t.TempDir()
	dir := filepath.Join(home, "tasks", "session-5e5510aa")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	for name, body := range map[string]string{"10.json": `{"id":"10","subject":"b"}`, "9.json": `{"id":"9","subject":"a"}`} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	tasks, err := ReadTasks(home, "5e5510aa-0000")
	if err != nil || len(tasks) != 2 || tasks[0].ID != "9" {
		t.Fatalf("tasks = %+v, %v", tasks, err)
	}
	if empty, err := ReadTasks(home, "absent"); err != nil || len(empty) != 0 {
		t.Fatalf("empty = %+v, %v", empty, err)
	}
}

func TestTheBoardsDatesComeFromBoardWatchSnapshotsInTheSameDayOfRecords(t *testing.T) {
	snapshot := Record{Seq: 3, Lane: boardWatch, Topic: boardTopic, Kind: "note", At: ago(5), Board: `{"rows":[]}`}
	other := Record{Seq: 4, Lane: boardWatch, Topic: "elsewhere", Kind: "note", At: ago(5), Board: `{"rows":[]}`}
	impostor := Record{Seq: 5, Lane: "someone", Topic: boardTopic, Kind: "note", At: ago(5), Board: `{"rows":[]}`}
	if got := snapshotsOf([]Record{snapshot, other, impostor}); len(got) != 1 || got[0].Seq != 3 {
		t.Fatalf("snapshots = %+v", got)
	}
}
