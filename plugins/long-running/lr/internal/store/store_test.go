package store

import (
	"database/sql"
	"errors"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	head = "e718a7434fa01ee9835f992a9a647a825350732c"
	repo = "org/repo"
)

var t0 = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)

func memory(t *testing.T) *Store {
	t.Helper()
	s, err := Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func must[T any](t *testing.T) func(T, error) T {
	t.Helper()
	return func(value T, err error) T {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
}

func ok(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func item(kind string, number int, mover machine.Mover, deadline time.Duration) NewItem {
	return NewItem{Kind: kind, Repo: repo, Number: number, Head: head, Mover: mover, Deadline: deadline}
}

func ids(items []Item) []int64 {
	out := make([]int64, len(items))
	for i, held := range items {
		out[i] = held.ID
	}
	return out
}

func TestAnItemWithoutAMoverOrADeadlineCannotBeWritten(t *testing.T) {
	s := memory(t)
	cases := map[string]string{
		"mover":    "INSERT INTO items (kind, repo, number, head, mover, created, deadline) VALUES ('enqueue', 'r', 1, 'h', NULL, '2026-10-30T21:00:00Z', '2026-10-30T21:10:00Z')",
		"deadline": "INSERT INTO items (kind, repo, number, head, mover, created, deadline) VALUES ('enqueue', 'r', 1, 'h', 'supervisor', '2026-10-30T21:00:00Z', NULL)",
	}
	for column, insert := range cases {
		_, err := s.db.Exec(insert)
		if err == nil || !strings.Contains(err.Error(), "NOT NULL") || !strings.Contains(err.Error(), column) {
			t.Errorf("%s: got %v, want a NOT NULL refusal", column, err)
		}
	}
}

func TestOpeningTheSameWaitTwiceKeepsOneItem(t *testing.T) {
	s := memory(t)
	first := must[Item](t)(s.OpenItem(item("enqueue", 45583, machine.MoverSupervisor, 10*time.Minute), t0))
	again := must[Item](t)(s.OpenItem(item("enqueue", 45583, machine.MoverSupervisor, 10*time.Minute), t0.Add(3*time.Minute)))
	if again != first {
		t.Fatalf("got %+v, want %+v", again, first)
	}
	if !again.Deadline.Equal(t0.Add(10 * time.Minute)) {
		t.Fatalf("the deadline moved to %s", again.Deadline)
	}
	if got := ids(must[[]Item](t)(s.OpenItems(repo, 45583))); !slices.Equal(got, []int64{first.ID}) {
		t.Fatalf("open items %v", got)
	}
}

func TestAClosedWaitCanReopenAsANewItem(t *testing.T) {
	s := memory(t)
	first := must[Item](t)(s.OpenItem(item("restack", 45410, machine.MoverSupervisor, 10*time.Minute), t0))
	ok(t, s.CloseItem(first.ID, t0.Add(4*time.Minute)))
	second := must[Item](t)(s.OpenItem(item("restack", 45410, machine.MoverSupervisor, 10*time.Minute), t0.Add(time.Hour)))
	if second.ID == first.ID {
		t.Fatal("the closed item came back")
	}
	if got := must[[]Item](t)(s.OpenItems(repo, 45410)); !reflect.DeepEqual(got, []Item{second}) {
		t.Fatalf("open items %+v", got)
	}
}

func TestOverdueListsOnlyItemsPastTheirDeadline(t *testing.T) {
	s := memory(t)
	late := must[Item](t)(s.OpenItem(item("verdict", 45470, machine.MoverLane, 20*time.Minute), t0))
	must[Item](t)(s.OpenItem(item("grade", 45489, machine.MoverPool, 10*time.Minute), t0.Add(15*time.Minute)))
	if got := ids(must[[]Item](t)(s.Overdue(t0.Add(21 * time.Minute)))); !slices.Equal(got, []int64{late.ID}) {
		t.Fatalf("overdue %v", got)
	}
	if got := must[[]Item](t)(s.Overdue(t0.Add(19 * time.Minute))); len(got) != 0 {
		t.Fatalf("overdue %+v", got)
	}
}

func TestAStateChangeClosesEveryOtherWaitOnThePR(t *testing.T) {
	s := memory(t)
	must[Item](t)(s.OpenItem(item("tap", 45583, machine.MoverOwner, 12*time.Hour), t0))
	must[Item](t)(s.OpenItem(item("grade", 45583, machine.MoverPool, 10*time.Minute), t0))
	kept := must[Item](t)(s.OpenItem(item("enqueue", 45583, machine.MoverSupervisor, 10*time.Minute), t0))
	if closed := must[int64](t)(s.CloseItems(repo, 45583, t0.Add(time.Minute), "enqueue")); closed != 2 {
		t.Fatalf("closed %d", closed)
	}
	if got := must[[]Item](t)(s.OpenItems(repo, 45583)); !reflect.DeepEqual(got, []Item{kept}) {
		t.Fatalf("open items %+v", got)
	}
	if closed := must[int64](t)(s.CloseItems(repo, 45583, t0.Add(2*time.Minute), "")); closed != 1 {
		t.Fatalf("closing with nothing kept closed %d", closed)
	}
}

func TestOpenItemsFiltersOnlyByWhatIsNamed(t *testing.T) {
	s := memory(t)
	must[Item](t)(s.OpenItem(item("tap", 1, machine.MoverOwner, time.Hour), t0))
	must[Item](t)(s.OpenItem(NewItem{Kind: "tap", Repo: "other/repo", Number: 2, Head: head, Mover: machine.MoverOwner, Deadline: time.Hour}, t0))
	if got := must[[]Item](t)(s.OpenItems("", 0)); len(got) != 2 {
		t.Fatalf("every repo: %+v", got)
	}
	if got := must[[]Item](t)(s.OpenItems(repo, 0)); len(got) != 1 || got[0].Number != 1 {
		t.Fatalf("one repo: %+v", got)
	}
}

func TestAGradePostedTwiceChangesNothing(t *testing.T) {
	s := memory(t)
	if !must[bool](t)(s.PutFact(repo, 46389, head, "finder", "clear", t0)) {
		t.Fatal("the first post changed nothing")
	}
	if must[bool](t)(s.PutFact(repo, 46389, head, "finder", "clear", t0.Add(5*time.Minute))) {
		t.Fatal("the second post changed something")
	}
	if got := must[map[string]string](t)(s.Facts(repo, 46389, head)); !reflect.DeepEqual(got, map[string]string{"finder": "clear"}) {
		t.Fatalf("facts %v", got)
	}
}

func TestAWithdrawalSupersedesAnEarlierTap(t *testing.T) {
	s := memory(t)
	must[bool](t)(s.PutFact(repo, 45911, head, "tap", "tapped", t0))
	if !must[bool](t)(s.PutFact(repo, 45911, head, "tap", "withdrawn", t0.Add(time.Minute))) {
		t.Fatal("the withdrawal changed nothing")
	}
	if got := must[map[string]string](t)(s.Facts(repo, 45911, head))["tap"]; got != "withdrawn" {
		t.Fatalf("tap %q", got)
	}
}

func TestAnOverrideStopsClearingOnceAGradeFailsAfterItAndARepeatClearsAgain(t *testing.T) {
	s := memory(t)
	full, short := strings.Repeat("f", 40), strings.Repeat("f", 7)
	at := time.Date(2026, 11, 4, 14, 43, 0, 0, time.UTC)
	put := func(head, kind, value string, offset time.Duration) {
		t.Helper()
		if _, err := s.PutFact("r", 1, head, kind, value, at.Add(offset)); err != nil {
			t.Fatal(err)
		}
	}
	overridden := func(grader string) bool {
		t.Helper()
		facts, err := s.Facts("r", 1, full)
		if err != nil {
			t.Fatal(err)
		}
		return facts[machine.OverrideFact(grader)] != ""
	}

	put(short, machine.OverrideFact("finder"), "434757", 0)
	put(short, machine.OverrideFact("fidelity"), "434758", 0)
	put(full, "finder", "clear", time.Minute)
	if !overridden("finder") {
		t.Fatal("a passing grade after the override dropped it")
	}
	put(full, "finder", "ask", time.Hour)
	if overridden("finder") {
		t.Fatal("an override older than its own grader's failing grade still stands")
	}
	if !overridden("fidelity") {
		t.Fatal("a failing finder grade dropped the fidelity override")
	}
	put(short, machine.OverrideFact("finder"), "434757", 2*time.Hour)
	if !overridden("finder") {
		t.Fatal("a repeated override after the grade does not stand")
	}
}

func TestFactsArePerHead(t *testing.T) {
	s := memory(t)
	must[bool](t)(s.PutFact(repo, 45744, "aa11bb22cc33dd44ee55ff6677889900aabbccdd", "tap", "tapped", t0))
	if got := must[map[string]string](t)(s.Facts(repo, 45744, head)); len(got) != 0 {
		t.Fatalf("facts %v", got)
	}
}

func TestAShortShaReadsTheFactsItsFullHeadCarries(t *testing.T) {
	s := memory(t)
	must[bool](t)(s.PutFact(repo, 46000, head, "diff", "h1", t0))
	must[bool](t)(s.PutFact(repo, 46000, "aa11bb22cc33dd44ee55ff6677889900aabbccdd", "diff", "h2", t0))
	if got := must[map[string]string](t)(s.Facts(repo, 46000, head[:12])); !reflect.DeepEqual(got, map[string]string{"diff": "h1"}) {
		t.Fatalf("got %v", got)
	}
}

func TestAFactKeyedByAShortShaCountsForTheFullHeadAndTheNewestValueWins(t *testing.T) {
	s := memory(t)
	must[bool](t)(s.PutFact(repo, 46000, head[:7], "finder", "ask", t0))
	must[bool](t)(s.PutFact(repo, 46000, head, "finder", "clear", t0.Add(time.Minute)))
	must[bool](t)(s.PutFact(repo, 46000, head[:7], "fidelity", "pass", t0))
	must[bool](t)(s.PutFact(repo, 46000, head[:6], "tap", "tapped", t0))
	want := map[string]string{"finder": "clear", "fidelity": "pass"}
	if got := must[map[string]string](t)(s.Facts(repo, 46000, head)); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestANewerRecordOwnsTheHeardNodeAndAnOlderOneLeavesIt(t *testing.T) {
	s := memory(t)
	_, err := s.WantLane(Lane{Name: "l", Brief: "b", Model: "m", Effort: "e", Role: "r", State: LaneDesired}, t0)
	ok(t, err)
	ok(t, s.LaneHeard("l", t0.Add(time.Minute), "state", 1))
	ok(t, s.LaneDelivered("l", t0.Add(time.Minute), "110f10f5abcdef01", 55*time.Minute))

	ok(t, s.LaneHeard("l", t0, "note", 2))
	ok(t, s.LaneDelivered("l", t0, "ffffffffffffffff", time.Hour))
	lanes, err := s.AllLanes()
	ok(t, err)
	if lanes[0].Node != "110f10f5abcdef01" || lanes[0].Late != 55*time.Minute {
		t.Fatalf("older record moved the node: %+v", lanes[0])
	}

	ok(t, s.LaneHeard("l", t0.Add(2*time.Minute), "landed", 0))
	lanes, err = s.AllLanes()
	ok(t, err)
	if lanes[0].Node != "" || lanes[0].Late != 0 {
		t.Fatalf("a newer local record kept the node: %+v", lanes[0])
	}
	newest, err := s.NewestHeard()
	ok(t, err)
	if !newest.Equal(t0.Add(2 * time.Minute)) {
		t.Fatalf("newest = %v", newest)
	}
}

func TestOneEscalationPerSubjectAndCause(t *testing.T) {
	s := memory(t)
	if !must[bool](t)(s.Escalate("org/repo#45470", "verdict overdue", "root", t0)) {
		t.Fatal("the first escalation was dropped")
	}
	if must[bool](t)(s.Escalate("org/repo#45470", "verdict overdue", "root", t0.Add(20*time.Minute))) {
		t.Fatal("the repeat was raised")
	}
	if !must[bool](t)(s.Escalate("org/repo#45470", "restack overdue", "root", t0.Add(20*time.Minute))) {
		t.Fatal("the second cause was dropped")
	}
	raised := must[[]Escalation](t)(s.Escalations("org/repo#45470"))
	if len(raised) != 2 || raised[0].Cause != "verdict overdue" || raised[1].Cause != "restack overdue" {
		t.Fatalf("escalations %+v", raised)
	}
}

func TestAWorkerSilentFifteenMinutesIsStaleAndItsItemsComeBack(t *testing.T) {
	s := memory(t)
	held := must[Item](t)(s.OpenItem(item("fix-red", 45651, machine.MoverLane, 20*time.Minute), t0))
	ok(t, s.Heartbeat(Worker{Name: "lane-7", Generation: 1, Heartbeat: t0, Item: held.ID}))
	ok(t, s.Heartbeat(Worker{Name: "lane-8", Generation: 1, Heartbeat: t0.Add(14 * time.Minute)}))
	ok(t, s.Heartbeat(Worker{Name: "lane-holding-nothing", Generation: 1, Heartbeat: t0}))
	if got := must[[]string](t)(s.StaleWorkers(t0.Add(14*time.Minute + 59*time.Second))); len(got) != 0 {
		t.Fatalf("stale %v", got)
	}
	if got := must[[]string](t)(s.StaleWorkers(t0.Add(15 * time.Minute))); !slices.Equal(got, []string{"lane-7"}) {
		t.Fatalf("stale %v", got)
	}
	released := must[[]Item](t)(s.ReleaseWorker("lane-7"))
	if got := ids(released); !slices.Equal(got, []int64{held.ID}) {
		t.Fatalf("released %v", got)
	}
	if got := must[[]Item](t)(s.OpenItems(repo, 45651))[0].Worker; got != "" {
		t.Fatalf("the item is still with %q", got)
	}
	workers := must[[]Worker](t)(s.Workers())
	if len(workers) != 2 || workers[0].Name != "lane-8" || workers[1].Name != "lane-holding-nothing" {
		t.Fatalf("workers %+v", workers)
	}
}

func TestAHeartbeatClaimsTheItemForTheWorker(t *testing.T) {
	s := memory(t)
	held := must[Item](t)(s.OpenItem(item("restack", 45580, machine.MoverLane, 20*time.Minute), t0))
	left := int64(120_000)
	ok(t, s.Heartbeat(Worker{Name: "lane-3", Generation: 2, Heartbeat: t0.Add(time.Minute), Item: held.ID, ContextLeft: &left}))
	if got := must[[]Item](t)(s.OpenItems(repo, 45580))[0].Worker; got != "lane-3" {
		t.Fatalf("worker %q", got)
	}
	worker := must[[]Worker](t)(s.Workers())[0]
	if worker.ContextLeft == nil || *worker.ContextLeft != left || worker.Item != held.ID {
		t.Fatalf("worker %+v", worker)
	}
}

func TestASecondSupervisorTakesTheLeaseAndTheFirstExitsOnRenewal(t *testing.T) {
	s := memory(t)
	first := must[Lease](t)(s.AcquireLease("drive", "0.8.0", 101, t0))
	second := must[Lease](t)(s.AcquireLease("drive", "0.8.1", 202, t0.Add(time.Minute)))
	if first.Generation != 1 || second.Generation != 2 {
		t.Fatalf("generations %d, %d", first.Generation, second.Generation)
	}
	ok(t, s.RenewLease("drive", second.Generation, t0.Add(2*time.Minute)))
	err := s.RenewLease("drive", first.Generation, t0.Add(2*time.Minute))
	if !errors.Is(err, ErrStaleGeneration) || !strings.Contains(err.Error(), "generation 1 is behind 2") {
		t.Fatalf("got %v", err)
	}
	if got := must[Lease](t)(s.Lease("drive")).PID; got != 202 {
		t.Fatalf("pid %d", got)
	}
}

func TestARuleRowCarriesAWholeCheckOrNone(t *testing.T) {
	s := memory(t)
	ok(t, s.PutRule("3f34533", "every PR needs a tap", "2026-10-22T09:49:00Z", "predicate", "gates.tapped"))
	ok(t, s.PutRule("9f51e67", "ports need no tap unless they conflict with the plan", "2026-10-29T19:46:00Z", "", ""))
	if got := must[[]string](t)(s.UncheckedRules()); !slices.Equal(got, []string{"9f51e67"}) {
		t.Fatalf("unchecked %v", got)
	}
	for _, kind := range []struct{ check, ref string }{{"predicate", ""}, {"vibe", "x"}} {
		err := s.PutRule("broken", "words", "2026-10-30T00:00:00Z", kind.check, kind.ref)
		if err == nil || !strings.Contains(err.Error(), "CHECK") {
			t.Errorf("%+v: got %v, want a CHECK refusal", kind, err)
		}
	}
}

func TestACursorNeverMovesBackwards(t *testing.T) {
	s := memory(t)
	if got := must[int64](t)(s.Cursor("cci")); got != 0 {
		t.Fatalf("cursor %d", got)
	}
	ok(t, s.Advance("cci", 239378))
	ok(t, s.Advance("cci", 239000))
	if got := must[int64](t)(s.Cursor("cci")); got != 239378 {
		t.Fatalf("cursor %d", got)
	}
}

func TestAPRRowIsUpsertedByRepoAndNumber(t *testing.T) {
	s := memory(t)
	ok(t, s.PutPR(PR{Repo: repo, Number: 45757, Head: "aa11", Base: "dev", Lane: "unregistered-lane", State: machine.StateGrading, ReadAt: t0}))
	ok(t, s.PutPR(PR{Repo: repo, Number: 45757, Head: head, Base: "dev", Lane: "unregistered-lane", State: machine.StateReady, ReadAt: t0.Add(11 * time.Hour)}))
	rows := must[[]PR](t)(s.PRs(repo))
	if len(rows) != 1 || rows[0].Head != head || rows[0].State != machine.StateReady {
		t.Fatalf("rows %+v", rows)
	}
}

func TestTheStoreOpensAFileInWALModeAndKeepsItsRows(t *testing.T) {
	path := filepath.Join(t.TempDir(), "drive.sqlite")
	s := must[*Store](t)(Open(path))
	var mode string
	ok(t, s.db.QueryRow("PRAGMA journal_mode").Scan(&mode))
	if mode != "wal" {
		t.Fatalf("journal mode %q", mode)
	}
	must[Item](t)(s.OpenItem(item("enqueue", 1, machine.MoverSupervisor, 10*time.Minute), t0))
	ok(t, s.Close())
	again := must[*Store](t)(Open(path))
	defer again.Close()
	if got := must[[]Item](t)(again.OpenItems(repo, 1)); len(got) != 1 || got[0].Kind != "enqueue" {
		t.Fatalf("items %+v", got)
	}
}

const oldLanes = `CREATE TABLE lanes (
  name TEXT PRIMARY KEY, brief TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL, role TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('launching', 'desired', 'held', 'retired')),
  since TEXT NOT NULL, heard TEXT, handoff TEXT, prs TEXT NOT NULL DEFAULT '[]',
  predecessor TEXT, reason TEXT, strikes INTEGER NOT NULL DEFAULT 0, asked TEXT, flushed TEXT, retired_by TEXT
)`

func TestAStoreFileFromAnOlderSchemaGainsTheColumnsItLacks(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lr.sqlite")
	old := must[*sql.DB](t)(sql.Open("sqlite", path))
	must[sql.Result](t)(old.Exec(oldLanes))
	must[sql.Result](t)(old.Exec("INSERT INTO lanes (name, brief, model, effort, role, state, since) VALUES ('l-1', '/b.md', 'opus', 'xhigh', 'lane', 'desired', ?)", Stamp(t0)))
	ok(t, old.Close())

	s := must[*Store](t)(Open(path))
	defer s.Close()
	lane, found, err := s.Lane("l-1")
	ok(t, err)
	if !found || lane.Waiting != 0 || !lane.Answered.IsZero() || lane.StoppedOn != 0 {
		t.Fatalf("lane %+v, found %v", lane, found)
	}
}

func TestANoticeAnOlderStoreMarkedAsAnEscalationStaysSentAndLeavesTheEscalations(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lr.sqlite")
	s := must[*Store](t)(Open(path))
	must[bool](t)(s.Escalate("alert:C1:1.5", "launch", "root", t0))
	must[bool](t)(s.Escalate("org/repo#1", "enqueue overdue", "root", t0))
	ok(t, s.Close())

	again := must[*Store](t)(Open(path))
	defer again.Close()
	if must[bool](t)(again.Notice("alert:C1:1.5", t0)) {
		t.Fatal("the alert's launch notice was sent again")
	}
	if got := must[[]Escalation](t)(again.Escalations("")); len(got) != 1 || got[0].Subject != "org/repo#1" {
		t.Fatalf("escalations %+v", got)
	}
}

func TestOpeningACurrentStoreAgainChangesNothing(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lr.sqlite")
	s := must[*Store](t)(Open(path))
	must[bool](t)(s.WantLane(Lane{Name: "l-1", Brief: "/b.md", Model: "opus", Effort: "xhigh", Role: "lane", State: LaneDesired}, t0))
	ok(t, s.Close())
	again := must[*Store](t)(Open(path))
	defer again.Close()
	if _, found, err := again.Lane("l-1"); err != nil || !found {
		t.Fatalf("found %v, err %v", found, err)
	}
}

func TestALaneRoundTripsEveryColumn(t *testing.T) {
	s := memory(t)
	want := Lane{
		Name: "l-2", Brief: "/b.md", Model: "opus", Effort: "xhigh", Role: "lane", State: LaneLaunching, Since: t0,
		Heard: t0.Add(time.Minute), Handoff: "ccn1", PRs: []int{7, 9}, Predecessor: "l-1", Reason: "rotated",
		Asked: t0.Add(2 * time.Minute), Flushed: t0.Add(3 * time.Minute), RetiredBy: "root", Waiting: 11, Answered: t0.Add(4 * time.Minute),
		Page: map[string]any{"topic": "owner:1"}, Awaits: "card-1", Last: "state", Placement: "orca", StoppedOn: 12, Launcher: "meta-desk-2",
	}
	if !must[bool](t)(s.WantLane(want, t0)) {
		t.Fatal("the lane was not added")
	}
	ok(t, s.LaneHeard("l-2", t0.Add(time.Minute), "state", 5))
	got, found, err := s.Lane("l-2")
	ok(t, err)
	if !found || !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
	if must[bool](t)(s.WantLane(Lane{Name: "l-2", Brief: "/other.md", Model: "m", Effort: "e", Role: "r", State: LaneHeld}, t0)) {
		t.Fatal("wanting a known lane added it again")
	}
	if got, _, _ := s.Lane("l-2"); got.Brief != "/b.md" {
		t.Fatalf("the brief moved to %q", got.Brief)
	}
}

func TestLanesFilterByState(t *testing.T) {
	s := memory(t)
	for name, state := range map[string]LaneState{"a": LaneDesired, "b": LaneRetired, "c": LaneHeld} {
		must[bool](t)(s.WantLane(Lane{Name: name, Brief: "b", Model: "m", Effort: "e", Role: "r", State: state}, t0))
	}
	names := func(lanes []Lane) []string {
		out := make([]string, len(lanes))
		for i, lane := range lanes {
			out[i] = lane.Name
		}
		return out
	}
	if got := names(must[[]Lane](t)(s.Lanes(LaneDesired, LaneHeld))); !slices.Equal(got, []string{"a", "c"}) {
		t.Fatalf("lanes %v", got)
	}
	if got := names(must[[]Lane](t)(s.AllLanes())); !slices.Equal(got, []string{"a", "b", "c"}) {
		t.Fatalf("all lanes %v", got)
	}
	if got := must[[]Lane](t)(s.Lanes()); len(got) != 0 {
		t.Fatalf("no states named: %+v", got)
	}
	if got := must[[]string](t)(s.Names()); !slices.Equal(got, []string{"a", "b", "c"}) {
		t.Fatalf("names %v", got)
	}
}

func TestLaneHeardKeepsTheNewestTimeKindAndSeq(t *testing.T) {
	s := memory(t)
	must[bool](t)(s.WantLane(Lane{Name: "l", Brief: "b", Model: "m", Effort: "e", Role: "r", State: LaneDesired}, t0))
	ok(t, s.LaneHeard("l", t0.Add(time.Minute), "state", 10))
	ok(t, s.LaneHeard("l", t0, "note", 5))
	lane, _, err := s.Lane("l")
	ok(t, err)
	if !lane.Heard.Equal(t0.Add(time.Minute)) || lane.Last != "state" {
		t.Fatalf("lane %+v", lane)
	}
	if got := must[int64](t)(s.NewestSeq("l")); got != 10 {
		t.Fatalf("seq %d", got)
	}
	ok(t, s.LaneHeard("l", t0.Add(2*time.Minute), "landed", 0))
	lane, _, err = s.Lane("l")
	ok(t, err)
	if lane.Last != "landed" {
		t.Fatalf("kind %q", lane.Last)
	}
	if got := must[int64](t)(s.NewestSeq("l")); got != 10 {
		t.Fatalf("a record with no seq moved the seq to %d", got)
	}
	if got := must[map[string]time.Time](t)(s.Heard())["l"]; !got.Equal(t0.Add(2 * time.Minute)) {
		t.Fatalf("heard %s", got)
	}
	if got := must[int64](t)(s.NewestSeq("nobody")); got != 0 {
		t.Fatalf("an unheard lane has seq %d", got)
	}
}

func TestOwnersFollowTheNewestLane(t *testing.T) {
	s := memory(t)
	ok(t, s.Own("a", []int{1, 2}))
	ok(t, s.Own("b", []int{2, 3}))
	want := map[string][]int{"a": {1}, "b": {2, 3}}
	if got := must[map[string][]int](t)(s.Owners()); !reflect.DeepEqual(got, want) {
		t.Fatalf("owners %v", got)
	}
	if got := must[map[int]string](t)(s.OwnerOf([]int{1, 3, 4})); !reflect.DeepEqual(got, map[int]string{1: "a", 3: "b", 4: ""}) {
		t.Fatalf("owner of %v", got)
	}
	ok(t, s.Disown([]int{3}))
	ok(t, s.SetLanePRs("a", []int{5}))
	want = map[string][]int{"a": {5}, "b": {2}}
	if got := must[map[string][]int](t)(s.Owners()); !reflect.DeepEqual(got, want) {
		t.Fatalf("owners %v", got)
	}
}

func TestALaneWaitingOnAnAnswerIsFoundOnceAndStopsWaiting(t *testing.T) {
	s := memory(t)
	must[bool](t)(s.WantLane(Lane{Name: "plain", Brief: "b", Model: "m", Effort: "e", Role: "r", State: LaneDesired}, t0))
	must[bool](t)(s.WantLane(Lane{Name: "carded", Brief: "b", Model: "m", Effort: "e", Role: "r", State: LaneDesired}, t0))
	ok(t, s.SetLaneWaiting("plain", 100, time.Time{}, ""))
	ok(t, s.SetLaneWaiting("carded", 100, time.Time{}, "card-1"))
	if got := must[[]string](t)(s.Answered([]int64{100, 101}, LaneDesired)); !slices.Equal(got, []string{"plain"}) {
		t.Fatalf("answered %v", got)
	}
	ok(t, s.SetLaneWaiting("plain", 0, t0, ""))
	lane, _, err := s.Lane("plain")
	ok(t, err)
	if lane.Waiting != 0 || !lane.Answered.Equal(t0) {
		t.Fatalf("lane %+v", lane)
	}
}

func TestLaneStateKeepsWhoRetiredItUnlessRestated(t *testing.T) {
	s := memory(t)
	must[bool](t)(s.WantLane(Lane{Name: "l", Brief: "b", Model: "m", Effort: "e", Role: "r", State: LaneDesired}, t0))
	ok(t, s.SetLaneState("l", LaneRetired, t0.Add(time.Minute), "root", "finished"))
	ok(t, s.SetLaneState("l", LaneHeld, t0.Add(2*time.Minute), "", ""))
	lane, _, err := s.Lane("l")
	ok(t, err)
	if lane.State != LaneHeld || !lane.Since.Equal(t0.Add(2*time.Minute)) || lane.RetiredBy != "root" || lane.Reason != "finished" {
		t.Fatalf("lane %+v", lane)
	}
}

func TestLaneFieldsSetAndClear(t *testing.T) {
	s := memory(t)
	must[bool](t)(s.WantLane(Lane{Name: "l", Brief: "b", Model: "m", Effort: "e", Role: "r", State: LaneDesired}, t0))
	ok(t, s.SetLaneHandoff("l", "doc-1", t0))
	ok(t, s.SetLaneHandoff("l", "doc-2", time.Time{}))
	ok(t, s.SetLaneAsked("l", t0))
	ok(t, s.SetLaneStoppedOn("l", 44))
	ok(t, s.SetLanePage("l", map[string]any{"topic": "t"}))
	lane, _, err := s.Lane("l")
	ok(t, err)
	if lane.Handoff != "doc-2" || !lane.Flushed.Equal(t0) || !lane.Asked.Equal(t0) || lane.StoppedOn != 44 || lane.Page["topic"] != "t" {
		t.Fatalf("lane %+v", lane)
	}
	if got := must[map[string]map[string]any](t)(s.Pages())["l"]["topic"]; got != "t" {
		t.Fatalf("pages %v", got)
	}
	ok(t, s.SetLaneAsked("l", time.Time{}))
	ok(t, s.SetLaneStoppedOn("l", 0))
	ok(t, s.SetLanePage("l", nil))
	lane, _, err = s.Lane("l")
	ok(t, err)
	if !lane.Asked.IsZero() || lane.StoppedOn != 0 || lane.Page != nil {
		t.Fatalf("lane %+v", lane)
	}
	if got := must[map[string]map[string]any](t)(s.Pages()); len(got) != 0 {
		t.Fatalf("pages %v", got)
	}
}

func TestAProbeIsAskedAgainAndCleared(t *testing.T) {
	s := memory(t)
	ok(t, s.Probe("l", t0))
	ok(t, s.Probe("l", t0.Add(2*time.Minute)))
	if got := must[map[string]Probe](t)(s.Probes())["l"]; !got.Asked.Equal(t0.Add(2 * time.Minute)) {
		t.Fatalf("probe %+v", got)
	}
	ok(t, s.Unprobe("l"))
	if got := must[map[string]Probe](t)(s.Probes()); len(got) != 0 {
		t.Fatalf("probes %v", got)
	}
}

func TestOrdersAreRememberedOnce(t *testing.T) {
	s := memory(t)
	ok(t, s.Order(7))
	ok(t, s.Order(7))
	if got := must[[]int64](t)(s.Orders([]int64{7, 8})); !slices.Equal(got, []int64{7}) {
		t.Fatalf("orders %v", got)
	}
}

func TestStampsAreUTCSecondsAndParseOffsets(t *testing.T) {
	pacific := time.FixedZone("PST", -8*3600)
	if got := Stamp(time.Date(2026, 10, 30, 13, 0, 5, 999, pacific)); got != "2026-10-30T21:00:05Z" {
		t.Fatalf("stamp %s", got)
	}
	parsed := must[time.Time](t)(Parse("2026-10-30T13:00:05-08:00"))
	if !parsed.Equal(time.Date(2026, 10, 30, 21, 0, 5, 0, time.UTC)) {
		t.Fatalf("parsed %s", parsed)
	}
}

func TestAScopedLaneReadCarriesOnlyItsOwnHeardAndPRs(t *testing.T) {
	s := memory(t)
	for _, name := range []string{"l-1", "l-2"} {
		must[bool](t)(s.WantLane(Lane{Name: name, Brief: "/b.md", Model: "opus", Effort: "xhigh", Role: "lane", State: LaneDesired}, t0))
	}
	must[bool](t)(s.WantLane(Lane{Name: "l-3", Brief: "/b.md", Model: "opus", Effort: "xhigh", Role: "lane", State: LaneRetired}, t0))
	ok(t, s.LaneHeard("l-1", t0.Add(time.Minute), "state", 5))
	ok(t, s.LaneHeard("l-3", t0.Add(2*time.Minute), "done", 6))
	ok(t, s.Own("l-1", []int{7, 9}))
	ok(t, s.Own("l-3", []int{11}))

	one, found, err := s.Lane("l-1")
	if err != nil || !found {
		t.Fatalf("found %v, err %v", found, err)
	}
	if !one.Heard.Equal(t0.Add(time.Minute)) || one.Last != "state" || !slices.Equal(one.PRs, []int{7, 9}) {
		t.Fatalf("heard %v, last %q, PRs %v", one.Heard, one.Last, one.PRs)
	}

	silent, found, err := s.Lane("l-2")
	if err != nil || !found {
		t.Fatalf("found %v, err %v", found, err)
	}
	if !silent.Heard.IsZero() || len(silent.PRs) != 0 {
		t.Fatalf("heard %v, PRs %v", silent.Heard, silent.PRs)
	}

	retired := must[[]Lane](t)(s.Lanes(LaneRetired))
	if len(retired) != 1 || retired[0].Name != "l-3" || retired[0].Last != "done" || !slices.Equal(retired[0].PRs, []int{11}) {
		t.Fatalf("retired %+v", retired)
	}

	missing, found, err := s.Lane("l-9")
	if err != nil || found {
		t.Fatalf("lane %+v, found %v, err %v", missing, found, err)
	}
}
