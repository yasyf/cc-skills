package update

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
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
		Plans:    func(context.Context) ([]*plans.Plan, error) { return nil, nil },
		Book:     func(context.Context) (asks.Book, error) { return asks.Book{PRs: map[string]asks.PR{}}, nil },
		PRRows:   func() ([]store.ReviewRow, error) { return nil, nil },
		Board:    func(context.Context, []board.Snapshot) (board.Live, error) { return board.Live{}, boardErr },
		Tasks:    func() ([]ClaudeTask, error) { return tasks, nil },
		Before:   func() (Snapshot, error) { return Snapshot{}, nil },
		Retro:    func(context.Context) ([]Retro, error) { return nil, nil },
		Owed:     func(context.Context) ([]deletions.Owed, error) { return nil, nil },
		Programs: func() ([]Program, error) { return nil, nil },
		Streams:  func() ([]Workstream, error) { return nil, nil },
		TapQueue: func() (string, error) { return "", nil },
		Rulings:  func(context.Context) ([]PlanRuling, error) { return nil, nil },
		Overdue:  func(time.Time) ([]store.Item, error) { return nil, nil },
		Orders:   func(context.Context) ([]owner.Row, error) { return nil, nil },
	}
}

func TestCollectReadsRecordsBackToTheLastUpdateAndKeepsTheOtherSectionsToADay(t *testing.T) {
	stale := record(2, "ship-2", "blocker", "old blocker", 30*60)
	stale.Status = statusOpen
	sources := src(nil, []Record{record(1, bridge, "ask", "before the day", 30*60), stale}, nil, nil)
	sources.Before = func() (Snapshot, error) { return Snapshot{At: ago(40 * 60)}, nil }
	in, err := Collect(context.Background(), sources, testDrive, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(in.All) != 2 || len(in.Waits) != 0 {
		t.Fatalf("all = %+v, waits = %+v", in.All, in.Waits)
	}
}

func TestAnOwedDeletionWhoseOwnerHasNoLiveLaneShowsOnTheUpdate(t *testing.T) {
	alone := heard("old-reader-1", "retired", 30)
	handed := heard("old-reader-2", "retired", 30)
	handed.RetiredBy = "new-reader-3"
	by := time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC)
	rows := []deletions.Owed{
		{ID: "e00667e1", Title: "Owed deletion: the Go wasm host", Owner: "one-reader-1", Dirs: []string{"go/sand/next"}, Symbols: []string{"ReplicaHost"}, Due: by},
		{ID: "a1b2c3d4", Title: "Owed deletion: the incarnation", Owner: "old-reader-1", Dirs: []string{"go/sand/next"}, Symbols: []string{"Incarnation"}, Due: by},
		{ID: "4ec66f9b", Title: "Owed deletion: epoch fields", Owner: "old-reader-2", Paths: []string{"go/sand/next/cmd/iris_serve_share.go"}, Symbols: []string{"Epoch"}, Due: by},
		{ID: "9c99a5d5", Title: "Owed deletion: Go client of the server's sync", Paths: []string{"go/sand/next/internal/sandsql/local/lifecycle.go"}, Symbols: []string{"Sync"}, Due: by},
		{ID: "c4015d76", Title: "Owed deletion: the old follower", Owner: "one-reader-delete-1", Dirs: []string{"go/sandsql-follower"}},
	}
	sources := src([]Lane{heard("one-reader-1", "desired", 5), alone, handed, heard("new-reader-3", "desired", 5)}, nil, nil, nil)
	sources.Owed = func(context.Context) ([]deletions.Owed, error) { return rows, nil }

	in, err := Collect(context.Background(), sources, testDrive, now)
	if err != nil {
		t.Fatal(err)
	}

	section := sectionOf(t, Render(in).Text, "Owed deletions with no live owner")
	for _, shown := range []string{`task a1b2c3d "Owed deletion: the incarnation" (go/sand/next, #Incarnation; owner old-reader-1, due 2026-11-09)`, "(go/sand/next/internal/sandsql/local/lifecycle.go, #Sync; owner nobody, due 2026-11-09)", "(go/sandsql-follower; owner one-reader-delete-1, no due date)"} {
		if !strings.Contains(section, shown) {
			t.Errorf("section lacks %s:\n%s", shown, section)
		}
	}
	for _, hidden := range []string{"#ReplicaHost", "#Epoch"} {
		if strings.Contains(section, hidden) {
			t.Errorf("section names %s, whose owner's line is live:\n%s", hidden, section)
		}
	}
}

func TestAnOwedDeletionPastItsDueDayShowsOnTheUpdateWhoeverOwnsIt(t *testing.T) {
	rows := []deletions.Owed{
		{ID: "025443e4", Title: "Owed deletion: Iris stops reading uploaded", Owner: "journal-positions-trim-1", Paths: []string{"go/sand/next/internal/journal/client.go"}, Symbols: []string{"Uploaded"}, Due: time.Date(2026, 10, 31, 0, 0, 0, 0, time.UTC)},
		{ID: "4ec66f9b", Title: "Owed deletion: epoch fields", Owner: "journal-positions-trim-1", Paths: []string{"go/sand/next/cmd/iris_serve_share.go"}, Symbols: []string{"Epoch"}, Due: time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)},
	}
	sources := src([]Lane{heard("journal-positions-trim-1", "desired", 5)}, nil, nil, nil)
	sources.Owed = func(context.Context) ([]deletions.Owed, error) { return rows, nil }

	in, err := Collect(context.Background(), sources, testDrive, now)
	if err != nil {
		t.Fatal(err)
	}

	section := sectionOf(t, Render(in).Text, "Owed deletions past due")
	if !strings.Contains(section, `task 025443e "Owed deletion: Iris stops reading uploaded"`) || strings.Contains(section, "4ec66f9") {
		t.Fatalf("past due section:\n%s", section)
	}
}

func overdueStore(t *testing.T, items ...store.NewItem) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	for i, item := range items {
		if _, err := s.OpenItem(item, ago(60-i)); err != nil {
			t.Fatal(err)
		}
	}
	return s
}

func renderedWith(t *testing.T, s *store.Store) []string {
	t.Helper()
	sources := src(nil, nil, nil, nil)
	sources.Overdue = s.Overdue
	in, err := Collect(context.Background(), sources, testDrive, now)
	if err != nil {
		t.Fatal(err)
	}
	return strings.Split(Render(in).Text, "\n")
}

func TestTheCoordinatorsOverdueItemsOpenTheUpdateOldestFirstWithOneDueThisMomentCounted(t *testing.T) {
	s := overdueStore(t,
		store.NewItem{Kind: "unfinished", Repo: "unfinished", Head: "ask/000001", Mover: machine.MoverRoot, Deadline: time.Hour},
		store.NewItem{Kind: "ask", Repo: "ask", Number: 12, Mover: machine.MoverRoot, Deadline: 15 * time.Minute},
		store.NewItem{Kind: "repeat", Repo: "repeat", Head: "grade overdue", Mover: machine.MoverRoot, Deadline: 2 * time.Hour},
		store.NewItem{Kind: "gate", Repo: "gate", Number: 48401, Head: "ship-2", Mover: machine.MoverLane},
	)

	lines := renderedWith(t, s)

	want := []string{"", "2 overdue:", "- ask #12, due " + pacific(ago(44)), "- unfinished ask rows: 1, oldest due " + pacific(now), ""}
	if !strings.HasPrefix(lines[0], "# Owner update: ") || !reflect.DeepEqual(lines[1:6], want) || !strings.HasPrefix(lines[6], "## "+workstreamsTitle) {
		t.Fatalf("the update opens:\n%s", strings.Join(lines[:7], "\n"))
	}
}

func TestUnfinishedRowsCountPerLedgerAtTheirOldestClockInsteadOfOneIDEach(t *testing.T) {
	items := []store.Item{
		{Kind: "unfinished", Head: "pick/313956", Deadline: ago(90)},
		{Kind: "lift", Number: 48593, Deadline: ago(80)},
		{Kind: "unfinished", Head: "retro/000018", Deadline: ago(70)},
		{Kind: "unfinished", Head: "pick/314286", Deadline: ago(60)},
	}

	want := []string{"", "4 overdue:", "- unfinished pick rows: 2, oldest due " + pacific(ago(90)), "- lift #48593, due " + pacific(ago(80)), "- unfinished retro rows: 1, oldest due " + pacific(ago(70))}
	if got := overdueLines(items); !reflect.DeepEqual(got, want) {
		t.Fatalf("overdue lines:\n%s", strings.Join(got, "\n"))
	}
}

func TestAnOrderListOverItsCapShowsTheNewestAndCountsTheRest(t *testing.T) {
	rows := make([]owner.Row, newestOrders+3)
	for i := range rows {
		rows[i] = owner.Row{ID: fmt.Sprintf("m%d", i), At: "Nov 3 4:50 AM", Text: "an ask"}
	}

	lines := orderLines(rows)

	if !strings.HasSuffix(lines[1], fmt.Sprintf("(%d):", len(rows))) || !strings.HasPrefix(lines[2], "- m3 ") || lines[len(lines)-1] != "- The 3 older ones are in `lr owner audit`." || len(lines) != newestOrders+3 {
		t.Fatalf("order lines:\n%s", strings.Join(lines, "\n"))
	}
}

func TestUndeliveredOwnerOrdersFollowTheOverdueItemsAtTheTop(t *testing.T) {
	sources := src(nil, nil, nil, nil)
	sources.Orders = func(context.Context) ([]owner.Row, error) {
		return []owner.Row{{ID: "m85", At: "Nov 3 4:50 AM", Text: "build the phone app for approvals"}}, nil
	}
	in, err := Collect(context.Background(), sources, testDrive, now)
	if err != nil {
		t.Fatal(err)
	}

	lines := strings.Split(Render(in).Text, "\n")

	want := []string{"", "Owner asks nobody has delivered, orders with no live lane, open PR or delivered proof and questions nobody answered (1):", "- m85 Nov 3 4:50 AM: build the phone app for approvals", ""}
	if !reflect.DeepEqual(lines[1:5], want) || !strings.HasPrefix(lines[5], "## "+workstreamsTitle) {
		t.Fatalf("the update opens:\n%s", strings.Join(lines[:6], "\n"))
	}
}

func TestNothingOverdueAddsNoLine(t *testing.T) {
	s := overdueStore(t, store.NewItem{Kind: "ask", Repo: "ask", Number: 12, Mover: machine.MoverRoot, Deadline: 2 * time.Hour})

	lines := renderedWith(t, s)

	if lines[1] != "" || !strings.HasPrefix(lines[2], "## "+workstreamsTitle) {
		t.Fatalf("the update opens:\n%s", strings.Join(lines[:3], "\n"))
	}
}

func sectionOf(t *testing.T, text, title string) string {
	t.Helper()
	_, after, found := strings.Cut(text, "## "+title+"\n")
	if !found {
		t.Fatalf("the update has no %q section", title)
	}
	section, _, _ := strings.Cut(after, "\n## ")
	return section
}

func TestAFirstUpdateLooksBackTwoHours(t *testing.T) {
	in, err := Collect(context.Background(), src(nil, nil, nil, nil), testDrive, now)
	if err != nil {
		t.Fatal(err)
	}
	if !in.Before.At.Equal(ago(120)) {
		t.Fatalf("before = %v", in.Before.At)
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
	hold.Status, hold.PRs = "open", []string{"48401"}
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

func TestAHoldHoldsEveryPRItWasPostedWithAndNoneItsTextCites(t *testing.T) {
	cites := Record{Seq: 1, Kind: "hold", Status: "open", Text: "HOLD #7 because of #8", PRs: []string{"9"}}
	refs := Record{Seq: 2, Kind: "hold", Status: "open", Text: "stop these", PRs: []string{"org/x#10", "11"}}
	closed := Record{Seq: 3, Kind: "hold", Status: "resolved", Text: "hold #12", PRs: []string{"12"}}
	want := map[string]int64{"9": 1, "10": 2, "11": 2}
	if got := heldPRs([]Record{cites, refs, closed}); !reflect.DeepEqual(got, want) {
		t.Fatalf("held = %v", got)
	}
}

func TestTheUpdateLinksHisTapsAndNeverListsThem(t *testing.T) {
	in := Inputs{Now: now, TapQueue: "https://dash.example/#/needs", Cards: []board.Wait{{Kind: "tap", PR: "49545"}, {Kind: "pick", Title: "Should the credential write runs?"}}}
	lines := yourTaps(in)
	if !reflect.DeepEqual(lines, []string{"- [2 open, in the order they free work](https://dash.example/#/needs)"}) {
		t.Fatalf("taps = %v", lines)
	}
	if in.TapQueue = ""; !strings.Contains(yourTaps(in)[0], "Needs me") {
		t.Fatalf("with no link configured the line must still name the screen: %v", yourTaps(in))
	}
	if in.Cards = nil; yourTaps(in) != nil {
		t.Fatalf("nothing open prints nothing: %v", yourTaps(in))
	}
}

func TestABoardThatDoesNotReadIsNamedAndTheRestStillRenders(t *testing.T) {
	in, err := Collect(context.Background(), src(nil, nil, nil, errors.New("cc-present outcomes exited 1: no board")), testDrive, now)
	if err != nil {
		t.Fatal(err)
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
	if last := status.InFlight[1].Last; last == nil || last.Kind != "report" || !last.At.Equal(ago(200)) {
		t.Fatalf("last = %+v", last)
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
	if status.Asks == nil || len(status.Asks) != 0 || status.Board == nil || len(status.Board) != 0 {
		t.Fatalf("a section left unread is not an empty list: %+v", status)
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

type boardRead struct{ stdout string }

func (b boardRead) Run(context.Context, shell.Command) (shell.Result, error) {
	return shell.Result{Stdout: b.stdout}, nil
}

func TestTheUpdateCountsHisReadyRowsWhenTheBoardCarriesNoPRRow(t *testing.T) {
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: 1, Head: "abc1234", ReadyForTap: true}, {Repo: "o/r", Number: 2, Head: "abc1234", ReadyForTap: true}, {Repo: "o/r", Number: 3, Head: "abc1234", WaitsOn: "held, root"}}); err != nil {
		t.Fatal(err)
	}
	stdout := `{"doc":{"blocks":[{"id":"card-a","type":"card","title":"Which one?","children":[{"id":"open","type":"choice"}]}]},"interactions":{}}`
	entry := drive.Entry{Repo: "o/r", Sessions: []string{"root"}, StateDir: t.TempDir()}
	sources := src(nil, nil, nil, nil)
	sources.Board = Wire(&app.App{Runner: boardRead{stdout}}, entry, s).Board
	in, err := Collect(context.Background(), sources, testDrive, now)
	if err != nil {
		t.Fatal(err)
	}
	if lines := yourTaps(in); !reflect.DeepEqual(lines, []string{"- 3 open, in order on the dashboard's Needs me tab"}) {
		t.Fatalf("taps = %v", lines)
	}
	lines := statusBoard(context.Background(), sources, nil)
	if len(lines) != 2 || lines[0].PR != "1" || lines[1].PR != "2" {
		t.Fatalf("status board = %+v", lines)
	}
}
