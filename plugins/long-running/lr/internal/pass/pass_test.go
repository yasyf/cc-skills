package pass_test

import (
	"encoding/json"
	"fmt"
	"reflect"
	"slices"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	head    = "e718a7434fa01ee9835f992a9a647a825350732c"
	newHead = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
	repo    = "org/repo"
)

var (
	t0  = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)
	all = []machine.Mover{machine.MoverSupervisor, machine.MoverPool, machine.MoverLane, machine.MoverOwner, machine.MoverRoot}
)

func facts(number int, change func(*machine.Facts)) machine.Facts {
	f := machine.Facts{
		Repo: repo, Number: number, Head: head, Base: "dev", Lane: "lane-7",
		CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true,
		Finder: machine.FinderClear, Fidelity: machine.FidelityPass, Tap: machine.TapTapped,
	}
	if change != nil {
		change(&f)
	}
	return f
}

func newStore(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func beat(t *testing.T, s *store.Store, name string, at time.Time, item int64) {
	t.Helper()
	if err := s.Heartbeat(store.Worker{Name: name, Generation: 1, Heartbeat: at, Item: item}); err != nil {
		t.Fatal(err)
	}
}

func finderAsks(f *machine.Facts) { f.Finder = machine.FinderAsk }
func noTap(f *machine.Facts)      { f.Tap = "" }

type posted struct {
	to   []string
	body bus.Body
}

type recorder struct{ posts []posted }

func (r *recorder) post(to []string, body bus.Body) error {
	r.posts = append(r.posts, posted{to, body})
	return nil
}

func (r *recorder) of(kind string) []posted {
	kept := make([]posted, 0, len(r.posts))
	for _, p := range r.posts {
		if p.body.Kind() == kind {
			kept = append(kept, p)
		}
	}
	return kept
}

func run(t *testing.T, s *store.Store, r *recorder, now time.Time, live []machine.Mover, snapshot ...machine.Facts) pass.Outcome {
	t.Helper()
	out, err := pass.Run(s, snapshot, now, r.post, live, func(desk string) string { return desk + "-3" }, running(s))
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func openOne(t *testing.T, s *store.Store, number int) store.Item {
	t.Helper()
	open, err := s.OpenItems(repo, number)
	if err != nil || len(open) != 1 {
		t.Fatalf("open items of #%d = %v, %v; want one", number, open, err)
	}
	return open[0]
}

func TestALandablePRGetsOneEnqueueItemTheSupervisorActsOnItself(t *testing.T) {
	s, r := newStore(t), &recorder{}

	out := run(t, s, r, t0, all, facts(45583, nil))

	item := openOne(t, s, 45583)
	if item.Kind != "enqueue" || item.Mover != "supervisor" || !item.Deadline.Equal(t0.Add(10*time.Minute)) {
		t.Fatalf("item = %+v", item)
	}
	if len(out.Acts) != 1 || out.Acts[0].Facts.Number != 45583 || out.Acts[0].Verdict.Action != machine.ActionEnqueue {
		t.Fatalf("acts = %+v", out.Acts)
	}
	if len(r.posts) != 0 {
		t.Fatalf("posts = %+v", r.posts)
	}
}

func TestAFindingWithoutAVerdictIsPostedToTheOwningLaneWithItsDeadline(t *testing.T) {
	s, r := newStore(t), &recorder{}

	run(t, s, r, t0, all, facts(45470, finderAsks))

	item := openOne(t, s, 45470)
	want := []posted{{[]string{"lane-7"}, bus.Item{
		Item: item.ID, Action: "verdict", PR: 45470, Head: head, Mover: "lane", Deadline: "2026-10-30T21:20:00Z",
	}}}
	if !reflect.DeepEqual(r.posts, want) {
		t.Fatalf("posts = %+v", r.posts)
	}
}

func TestAPRTheStoreSawOpenIsLandedOnceWhenItReadsMerged(t *testing.T) {
	s := newStore(t)
	r := &recorder{}
	merged := func(f *machine.Facts) { f.Merged = true }
	run(t, s, r, t0, nil, facts(7, nil))

	first := run(t, s, r, t0.Add(time.Minute), nil, facts(7, merged), facts(8, merged))
	if len(first.Landed) != 1 || first.Landed[0].Facts.Number != 7 || !first.Landed[0].LastOpen.Equal(t0) {
		t.Fatalf("landed = %+v, want #7 last open at %s; #8 was never seen open", first.Landed, t0)
	}

	if again := run(t, s, r, t0.Add(2*time.Minute), nil, facts(7, merged)); len(again.Landed) != 0 {
		t.Fatalf("landed again = %+v", again.Landed)
	}
}

func TestACarriedTapIsReportedOnlyOnceProvenAndWhileThePRIsOpen(t *testing.T) {
	s := newStore(t)
	carried := func(f *machine.Facts) { f.TapFrom, f.CarriedBy = "a28ab62", "same-diff" }

	out := run(t, s, &recorder{}, t0, nil, facts(7, carried), facts(8, func(f *machine.Facts) {
		carried(f)
		f.Merged = true
	}), facts(9, func(f *machine.Facts) { f.Tap, f.TapFrom = "", "a28ab62" }))

	if len(out.Carried) != 1 || out.Carried[0].Number != 7 {
		t.Fatalf("carried = %+v, want #7 alone", out.Carried)
	}
}

func TestAMissingTapIsTheOwnersItem(t *testing.T) {
	s, r := newStore(t), &recorder{}

	run(t, s, r, t0, all, facts(45583, noTap))

	item, isItem := r.posts[0].body.(bus.Item)
	if len(r.posts) != 1 || !isItem || r.posts[0].to[0] != "owner" || item.Action != "tap" {
		t.Fatalf("posts = %+v", r.posts)
	}
}

func TestTheSameFactsTwiceOpenNothingAndPostNothing(t *testing.T) {
	s, r := newStore(t), &recorder{}

	run(t, s, r, t0, all, facts(45470, finderAsks))
	second := run(t, s, r, t0.Add(5*time.Minute), all, facts(45470, finderAsks))

	if len(second.Opened) != 0 || len(r.posts) != 1 {
		t.Fatalf("opened = %+v, posts = %+v", second.Opened, r.posts)
	}
	openOne(t, s, 45470)
}

func TestAStateChangeClosesTheOldItemAndOpensTheNewOne(t *testing.T) {
	s, r := newStore(t), &recorder{}

	run(t, s, r, t0, all, facts(45583, func(f *machine.Facts) { f.Fidelity = "" }))
	run(t, s, r, t0.Add(4*time.Minute), all, facts(45583, nil))

	if item := openOne(t, s, 45583); item.Kind != "enqueue" {
		t.Fatalf("item = %+v", item)
	}
}

func TestAPRNobodyDrivesLosesItsItemsAndOpensNoneUntilALanePosts(t *testing.T) {
	s, r := newStore(t), &recorder{}
	ungraded := func(lane string) func(*machine.Facts) {
		return func(f *machine.Facts) { f.Lane, f.Finder, f.Fidelity, f.Tap = lane, "", "", "" }
	}
	opens := func(at time.Time, lane string, want int) {
		t.Helper()
		run(t, s, r, at, all, facts(45583, ungraded(lane)))
		if open, err := s.OpenItems(repo, 45583); err != nil || len(open) != want {
			t.Fatalf("with lane %q open = %+v, %v, want %d", lane, open, err, want)
		}
	}

	opens(t0, "lane-7", 1)
	opens(t0.Add(time.Minute), "", 0)
	opens(t0.Add(2*time.Minute), "", 0)
	opens(t0.Add(3*time.Minute), "lane-7", 1)
}

func TestLandedClosesEveryOpenItem(t *testing.T) {
	s, r := newStore(t), &recorder{}

	run(t, s, r, t0, all, facts(45583, noTap))
	run(t, s, r, t0.Add(time.Hour), all, facts(45583, func(f *machine.Facts) { f.Merged, f.Closed = true, true }))

	if open, _ := s.OpenItems(repo, 45583); len(open) != 0 {
		t.Fatalf("open = %+v", open)
	}
	prs, err := s.PRs(repo)
	if err != nil || len(prs) != 1 || prs[0].State != machine.StateLanded {
		t.Fatalf("prs = %+v, %v", prs, err)
	}
}

func TestAFailedPostNeverStopsThePassAndIsReported(t *testing.T) {
	s := newStore(t)
	given := []machine.Facts{facts(45470, finderAsks), facts(45471, finderAsks)}
	post := func([]string, bus.Body) error { return &bus.Refused{Message: "never reaches this lane"} }
	route := func(desk string) string { return desk + "-3" }
	if _, err := pass.Run(s, given, t0, post, all, route, running(s)); err != nil {
		t.Fatal(err)
	}

	out, err := pass.Run(s, given, t0.Add(20*time.Minute), post, all, route, running(s))
	if err != nil {
		t.Fatal(err)
	}

	if len(out.Escalated) != 2 || len(out.Unposted) != 2 {
		t.Fatalf("escalated = %+v, unposted = %+v", out.Escalated, out.Unposted)
	}
}

func TestAnOverdueVerdictGoesOnceToTheGraderDeskNeverRoot(t *testing.T) {
	s, r := newStore(t), &recorder{}
	given := facts(45470, finderAsks)

	run(t, s, r, t0, all, given)
	late := run(t, s, r, t0.Add(20*time.Minute), all, given)
	later := run(t, s, r, t0.Add(40*time.Minute), all, given)

	want := []pass.Escalated{{Subject: "org/repo#45470", Cause: "verdict overdue", To: "grader-appeals-3"}}
	if !reflect.DeepEqual(late.Escalated, want) || len(later.Escalated) != 0 {
		t.Fatalf("late = %+v, later = %+v", late.Escalated, later.Escalated)
	}
	body := bus.Note{Text: "org/repo#45470 verdict overdue"}
	if !reflect.DeepEqual(r.of("note"), []posted{{[]string{"grader-appeals-3"}, body}}) {
		t.Fatalf("posts = %+v", r.of("note"))
	}
}

func TestAnOverdueGradeGoesToTheGraderDeskAndAnOverdueTapToTheBoardDesk(t *testing.T) {
	s, r := newStore(t), &recorder{}
	given := []machine.Facts{facts(1, func(f *machine.Facts) { f.Fidelity, f.CI = "", machine.CIPending }), facts(2, noTap)}

	run(t, s, r, t0, all, given...)
	out := run(t, s, r, t0.Add(12*time.Hour), all, given...)

	got := out.Escalated
	sort.Slice(got, func(a, b int) bool { return got[a].Subject < got[b].Subject })
	want := []pass.Escalated{
		{Subject: "org/repo#1", Cause: "grade overdue", To: "grader-appeals-3"},
		{Subject: "org/repo#2", Cause: "tap overdue", To: "board-refresher-3"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("escalated = %+v", got)
	}
}

func TestAWorkerSilentFifteenMinutesLosesItsItemsAndTheGraderDeskHearsOnce(t *testing.T) {
	s, r := newStore(t), &recorder{}
	given := facts(45651, func(f *machine.Facts) { f.CI = machine.CIFailure })

	run(t, s, r, t0, all, given)
	item := openOne(t, s, 45651)
	beat(t, s, "lane-7", t0.Add(time.Minute), item.ID)

	quiet := run(t, s, r, t0.Add(10*time.Minute), all, given)
	silent := run(t, s, r, t0.Add(16*time.Minute), all, given)
	again := run(t, s, r, t0.Add(18*time.Minute), all, given)

	if len(quiet.Released) != 0 {
		t.Fatalf("quiet = %+v", quiet.Released)
	}
	if held := silent.Released["lane-7"]; len(held) != 1 || held[0].ID != item.ID {
		t.Fatalf("silent = %+v", silent.Released)
	}
	wantEscalated := []pass.Escalated{{Subject: "worker:lane-7", Cause: "silent 15 minutes", To: "grader-appeals-3"}}
	if !reflect.DeepEqual(silent.Escalated, wantEscalated) {
		t.Fatalf("escalated = %+v", silent.Escalated)
	}
	if openOne(t, s, 45651).Worker != "" {
		t.Fatal("the item still names the silent worker")
	}
	if len(again.Released) != 0 {
		t.Fatalf("again = %+v", again.Released)
	}
	body := bus.Note{Text: "worker:lane-7 silent 15 minutes"}
	if !reflect.DeepEqual(r.of("note"), []posted{{[]string{"grader-appeals-3"}, body}}) {
		t.Fatalf("posts = %+v", r.of("note"))
	}
}

func TestAGradingJobWhoseWrapperDiedFreesItsItemForTheNextDispatch(t *testing.T) {
	s, r := newStore(t), &recorder{}
	given := facts(46052, func(f *machine.Facts) { f.Finder = "" })

	run(t, s, r, t0, all, given)
	grade := openOne(t, s, 46052)
	beat(t, s, "sprite-3/finder/46052", t0, grade.ID)

	out := run(t, s, r, t0.Add(15*time.Minute), all, given)

	if held := out.Released["sprite-3/finder/46052"]; len(held) != 1 || held[0].Kind != "grade" {
		t.Fatalf("released = %+v", out.Released)
	}
	workers, err := s.Workers()
	if err != nil || len(workers) != 0 || openOne(t, s, 46052).Worker != "" {
		t.Fatalf("workers = %+v, %v; the dead wrapper still holds the item", workers, err)
	}
}

func TestAFreshStoreActsOnEveryLandableHeadInItsFirstPass(t *testing.T) {
	s, r := newStore(t), &recorder{}
	snapshot := make([]machine.Facts, 0, 10)
	for number := 1; number <= 10; number++ {
		snapshot = append(snapshot, facts(number, nil))
	}

	out := run(t, s, r, t0, all, snapshot...)

	numbers := make([]int, 0, len(out.Acts))
	for _, act := range out.Acts {
		if act.Verdict.Action == machine.ActionEnqueue {
			numbers = append(numbers, act.Facts.Number)
		}
	}
	if !slices.Equal(numbers, []int{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}) {
		t.Fatalf("enqueued = %v", numbers)
	}
}

func TestAMoverNotYetCutOverKeepsItsItemsInTheStoreAndSilent(t *testing.T) {
	s, r := newStore(t), &recorder{}
	gradingOnly := []machine.Mover{machine.MoverPool}
	given := []machine.Facts{facts(45470, finderAsks), facts(45471, noTap)}

	run(t, s, r, t0, gradingOnly, given...)
	run(t, s, r, t0.Add(24*time.Hour), gradingOnly, given...)

	if openOne(t, s, 45470).Kind != "verdict" || openOne(t, s, 45471).Kind != "tap" {
		t.Fatal("the items were not kept")
	}
	escalations, err := s.Escalations("")
	if err != nil || len(r.posts) != 0 || len(escalations) != 0 {
		t.Fatalf("posts = %+v, escalations = %+v, %v", r.posts, escalations, err)
	}
}

func running(s *store.Store) pass.Owner {
	return func(lane string, pr int) (string, error) { return lanes.ChangeOwner(s, "d", lane, pr) }
}

func TestAnOverdueRedReachesOnlyARunningOwnerOrItsNamedSuccessorElseRootAsUnowned(t *testing.T) {
	for _, tt := range []struct {
		name, state, retiredBy string
		want                   posted
	}{
		{"the owner runs", string(store.LaneDesired), "", posted{[]string{"lane-7"}, bus.Ask{Text: "org/repo#45470 fix-red overdue"}}},
		{"the owner stood down for a named successor", string(store.LaneRetired), "lane-9", posted{[]string{"lane-9"}, bus.Ask{Text: "org/repo#45470 fix-red overdue"}}},
		{"the owner stopped while a later lane of its stem runs", string(store.LaneRetired), "", posted{[]string{"root"}, bus.Escalation{Subject: "org/repo#45470", Cause: "UNOWNED, lane-7 is not running: fix-red overdue"}}},
		{"the owner is held", string(store.LaneHeld), "", posted{[]string{"root"}, bus.Escalation{Subject: "org/repo#45470", Cause: "UNOWNED, lane-7 is not running: fix-red overdue"}}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s, r := newStore(t), &recorder{}
			for _, lane := range []string{"lane-7", "lane-8", "lane-9"} {
				if _, err := s.WantLane(store.Lane{Name: lane, State: store.LaneDesired}, t0); err != nil {
					t.Fatal(err)
				}
			}
			if err := s.SetLaneState("lane-7", store.LaneState(tt.state), t0, tt.retiredBy, ""); err != nil {
				t.Fatal(err)
			}
			given := facts(45470, func(f *machine.Facts) { f.CI, f.Lane = machine.CIFailure, "lane-7" })

			run(t, s, r, t0, all, given)
			r.posts = nil
			run(t, s, r, t0.Add(20*time.Minute), all, given)

			if !reflect.DeepEqual(r.posts, []posted{tt.want}) {
				t.Fatalf("posts = %+v", r.posts)
			}
		})
	}
}

func TestTheOct31StallAlertsReachTheLastPusherThenTheCoordinatorsNaming(t *testing.T) {
	const opener, later, carrier, chain, stoppedPusher, namedByRoot = "data-client-layer-3", "data-client-layer-4", "conflicts-5", "n-enumeration-11", "placement-build-3", "placement-build-4"
	ask := func(to string, pr int) posted {
		return posted{[]string{to}, bus.Ask{Text: fmt.Sprintf("org/repo#%d fix-red overdue", pr)}}
	}
	for _, tt := range []struct {
		name          string
		pr            int
		pusher, named string
		stopped       []string
		want          posted
	}{
		{"#46052, sent to a later lane of its opener's stem in cci 255356", 46052, carrier, opener, []string{opener}, ask(carrier, 46052)},
		{"#46071, sent to a lane root never named for it in cci 255357", 46071, chain, carrier, nil, ask(chain, 46071)},
		{"#45813, whose pusher stopped and whose new owner root named in cci 255401", 45813, stoppedPusher, namedByRoot, []string{stoppedPusher}, ask(namedByRoot, 45813)},
		{"a stopped pusher and a stopped naming never fall to a later lane of the stem", 46052, carrier, opener, []string{carrier, opener}, posted{[]string{"root"}, bus.Escalation{Subject: "org/repo#46052", Cause: "UNOWNED, conflicts-5 is not running: fix-red overdue"}}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s, r := newStore(t), &recorder{}
			for _, lane := range []string{opener, later, carrier, chain, stoppedPusher, namedByRoot} {
				if _, err := s.WantLane(store.Lane{Name: lane, State: store.LaneDesired}, t0); err != nil {
					t.Fatal(err)
				}
			}
			for _, lane := range tt.stopped {
				if err := s.SetLaneState(lane, store.LaneRetired, t0, "", ""); err != nil {
					t.Fatal(err)
				}
			}
			if err := s.Own(tt.named, []int{tt.pr}); err != nil {
				t.Fatal(err)
			}
			given := facts(tt.pr, func(f *machine.Facts) { f.CI, f.Lane = machine.CIFailure, tt.pusher })

			run(t, s, r, t0, all, given)
			r.posts = nil
			run(t, s, r, t0.Add(20*time.Minute), all, given)

			if !reflect.DeepEqual(r.posts, []posted{tt.want}) {
				t.Fatalf("posts = %+v", r.posts)
			}
		})
	}
}

func TestARedNoLaneOwnsWaitsForItsDeadlineToReachRoot(t *testing.T) {
	s, r := newStore(t), &recorder{}
	given := facts(45470, func(f *machine.Facts) { f.CI, f.Lane = machine.CIFailure, "" })

	run(t, s, r, t0, all, given)
	run(t, s, r, t0.Add(20*time.Minute), all, given)

	want := []posted{{[]string{"root"}, bus.Escalation{Subject: "org/repo#45470", Cause: "fix-red overdue"}}}
	if !reflect.DeepEqual(r.posts, want) {
		t.Fatalf("posts = %+v", r.posts)
	}
}

func TestAQueueThatKeepsChangingItsWordsRaisesOneStuckPROnce(t *testing.T) {
	s, r := newStore(t), &recorder{}
	said := func(words string) {
		t.Helper()
		if _, err := s.PutFact(repo, 48476, head, "enqueued", words, t0); err != nil {
			t.Fatal(err)
		}
	}

	run(t, s, r, t0, all, facts(48476, nil))
	said("exit 5: TLS handshake timeout")
	run(t, s, r, t0.Add(10*time.Minute), all, facts(48476, nil))
	said("exit 1: no approved green bottom prefix")
	run(t, s, r, t0.Add(20*time.Minute), all, facts(48476, nil))

	if len(r.posts) != 1 {
		t.Fatalf("posts = %+v, want the first escalation alone", r.posts)
	}
}

func TestAnOverdueEnqueueNamesWhatTheQueueSaid(t *testing.T) {
	s, r := newStore(t), &recorder{}

	run(t, s, r, t0, all, facts(45583, nil))
	if _, err := s.PutFact(repo, 45583, head, "enqueued", "exit 1: #45583 BLOCKED required check pending", t0); err != nil {
		t.Fatal(err)
	}
	run(t, s, r, t0.Add(10*time.Minute), all, facts(45583, nil))

	want := []posted{{[]string{"root"}, bus.Overdue("root", "org/repo#45583", "enqueue overdue; the queue said exit 1: #45583 BLOCKED required check pending")}}
	if !reflect.DeepEqual(r.posts, want) {
		t.Fatalf("posts = %+v", r.posts)
	}
}

func TestAPushClosesTheOldHeadsItemSoItsDeadlineNeverEscalatesTheNewHead(t *testing.T) {
	s, r := newStore(t), &recorder{}
	red := func(f *machine.Facts) { f.CI = machine.CIFailure }

	run(t, s, r, t0, all, facts(45651, red))
	pushed := facts(45651, red)
	pushed.Head = newHead
	run(t, s, r, t0.Add(19*time.Minute), all, pushed)
	out := run(t, s, r, t0.Add(20*time.Minute), all, pushed)

	if item := openOne(t, s, 45651); item.Head != newHead {
		t.Fatalf("item = %+v", item)
	}
	if len(out.Escalated) != 0 {
		t.Fatalf("escalated = %+v", out.Escalated)
	}
}

func TestAnOverdueRedOrRestackGoesToThePRsLaneNeverRoot(t *testing.T) {
	for name, given := range map[string]func(*machine.Facts){
		"fix-red": func(f *machine.Facts) { f.CI = machine.CIFailure },
		"restack": func(f *machine.Facts) { f.Mergeable = machine.MergeableConflict },
	} {
		t.Run(name, func(t *testing.T) {
			s, r := newStore(t), &recorder{}
			pr := facts(45470, given)
			if _, err := s.WantLane(store.Lane{Name: pr.Lane, State: store.LaneDesired}, t0); err != nil {
				t.Fatal(err)
			}

			run(t, s, r, t0, all, pr)
			run(t, s, r, t0.Add(20*time.Minute), all, pr)

			got := r.of("ask")
			if len(got) != 1 || !reflect.DeepEqual(got[0].to, []string{pr.Lane}) {
				t.Fatalf("escalations = %+v", got)
			}
		})
	}
}

func TestAReadyPRBehindAnUntappedOneHasNoClockUntilTheOneBelowClears(t *testing.T) {
	s, r := newStore(t), &recorder{}
	below := facts(45469, func(f *machine.Facts) { f.Branch, f.Tap = "below", "" })
	above := facts(45470, func(f *machine.Facts) { f.Branch, f.Base = "above", "below" })
	live := []machine.Mover{machine.MoverSupervisor}

	first := run(t, s, r, t0, live, below, above)
	late := run(t, s, r, t0.Add(machine.SupervisorClock+time.Minute), live, below, above)
	below.Tap = machine.TapTapped
	cleared := run(t, s, r, t0.Add(machine.SupervisorClock+2*time.Minute), live, below, above)

	if len(first.Acts) != 0 || len(late.Acts) != 0 || len(late.Escalated) != 0 {
		t.Fatalf("first = %+v, late = %+v, escalated = %+v", first.Acts, late.Acts, late.Escalated)
	}
	if len(cleared.Acts) != 2 || len(cleared.Escalated) != 0 {
		t.Fatalf("cleared = %+v, escalated = %+v", cleared.Acts, cleared.Escalated)
	}
}

func TestAHeldOrUntappedConflictIsNeitherActedOnNorEscalatedWhileLandingIsLive(t *testing.T) {
	for name, given := range map[string]func(*machine.Facts){
		"held":     func(f *machine.Facts) { f.Mergeable, f.Hold = machine.MergeableConflict, machine.MoverRoot },
		"untapped": func(f *machine.Facts) { f.Mergeable, f.Tap = machine.MergeableConflict, "" },
	} {
		t.Run(name, func(t *testing.T) {
			s, r := newStore(t), &recorder{}
			pr := facts(45470, given)
			live := []machine.Mover{machine.MoverSupervisor}

			first := run(t, s, r, t0, live, pr)
			run(t, s, r, t0.Add(machine.LaneClock), live, pr)

			if len(first.Acts) != 0 || len(r.of("note")) != 0 {
				t.Fatalf("acts = %+v, notes = %+v", first.Acts, r.of("note"))
			}
		})
	}
}

func TestAHoldOverdueReachesRootOnceLandingIsLive(t *testing.T) {
	s, r := newStore(t), &recorder{}
	held := facts(48401, func(f *machine.Facts) { f.Hold, f.Tap = machine.MoverRoot, "" })

	run(t, s, r, t0, []machine.Mover{machine.MoverSupervisor}, held)
	run(t, s, r, t0.Add(machine.OwnerClock), []machine.Mover{machine.MoverSupervisor}, held)

	overdue := bus.Overdue("root", "org/repo#48401", "lift overdue")
	want := []posted{{[]string{"root"}, overdue}}
	if !reflect.DeepEqual(r.of(overdue.Kind()), want) {
		t.Fatalf("posts = %+v", r.posts)
	}
}

func TestATappedHeadUnderAHoldAsksTheCoordinatorOnceAndAnUntappedOneWaitsOnTheOwner(t *testing.T) {
	s, r := newStore(t), &recorder{}
	supervisor := []machine.Mover{machine.MoverSupervisor}
	tapped := facts(48401, func(f *machine.Facts) { f.Hold, f.HoldSeq = machine.MoverRoot, 372915 })
	untapped := facts(48402, func(f *machine.Facts) { f.Hold, f.HoldSeq, f.Tap = machine.MoverRoot, 372916, "" })
	pushed := tapped
	pushed.Head = newHead

	run(t, s, r, t0, supervisor, tapped, untapped)
	run(t, s, r, t0.Add(3*time.Minute), supervisor, tapped, untapped)
	run(t, s, r, t0.Add(6*time.Minute), supervisor, pushed, untapped)

	decide := "DECIDE: the owner tapped this head while hold #372915 stands; lift the hold, or tell the owner why not"
	want := []posted{
		{[]string{"root"}, bus.Escalation{Subject: "org/repo#48401 at e718a7434f", Cause: decide}},
		{[]string{"root"}, bus.Escalation{Subject: "org/repo#48401 at aa11bb22cc", Cause: decide}},
	}
	if !reflect.DeepEqual(r.posts, want) {
		t.Fatalf("posts = %+v", r.posts)
	}
	if item := openOne(t, s, 48402); item.Mover != machine.MoverRoot || !item.Deadline.Equal(t0.Add(machine.OwnerClock)) {
		t.Fatalf("untapped item = %+v, want root's lift on the owner's clock", item)
	}
}

func TestAnItemOfAKindThePassDoesNotOwnNeverEscalates(t *testing.T) {
	s, r := newStore(t), &recorder{}
	if _, err := s.OpenItem(store.NewItem{Kind: "approve", Repo: "release", Number: 12192, Head: "efe25ce", Mover: machine.MoverLane, Deadline: time.Minute}, t0); err != nil {
		t.Fatal(err)
	}

	out := run(t, s, r, t0.Add(time.Hour), all)

	if len(out.Escalated) != 0 {
		t.Fatalf("escalated = %+v", out.Escalated)
	}
}

func TestLiveNamesTheMoverEachConfiguredMoverPutsOnTheBus(t *testing.T) {
	got := pass.Live([]string{"lanes", "shadow", "landing", "grading", "items"})

	if !slices.Equal(got, []machine.Mover{machine.MoverSupervisor, machine.MoverPool, machine.MoverLane}) {
		t.Fatalf("live = %v", got)
	}
}

func TestAQueuedPRAboveAHeldOneLeavesTheQueueWithIt(t *testing.T) {
	s, r := newStore(t), &recorder{}
	queued := func(number int, branch, base string, hold machine.Mover) machine.Facts {
		return facts(number, func(f *machine.Facts) {
			f.Branch, f.Base, f.Queue, f.QueuedHead, f.Hold = branch, base, machine.QueueQueued, f.Head, hold
		})
	}
	snapshot := []machine.Facts{
		queued(1, "bottom", "dev", machine.MoverRoot),
		queued(2, "middle", "bottom", ""),
		queued(3, "top", "middle", ""),
		queued(4, "other", "dev", ""),
	}

	out, err := pass.Run(s, snapshot, t0, r.post, all, func(desk string) string { return desk }, running(s))
	if err != nil {
		t.Fatal(err)
	}

	acted := make([]int, 0, len(out.Acts))
	for _, act := range out.Acts {
		if act.Verdict.Action == machine.ActionDequeue {
			acted = append(acted, act.Facts.Number)
		}
	}
	if !slices.Equal(acted, []int{1, 2, 3}) {
		t.Fatalf("dequeued = %v", acted)
	}
}

func escalated(out pass.Outcome, keep func(pass.Escalated) bool) []pass.Escalated {
	var kept []pass.Escalated
	for _, e := range out.Escalated {
		if keep(e) {
			kept = append(kept, e)
		}
	}
	return kept
}

func clustered(out pass.Outcome) []pass.Escalated {
	return escalated(out, func(e pass.Escalated) bool { return strings.HasPrefix(e.Subject, repo+": ") })
}

func toRoot(out pass.Outcome) []pass.Escalated {
	return escalated(out, func(e pass.Escalated) bool { return e.To == "root" })
}

func refuse(t *testing.T, s *store.Store, numbers ...int) {
	t.Helper()
	for _, number := range numbers {
		if _, err := s.PutFact(repo, number, head, "enqueued", fmt.Sprintf("exit 1: #%d is in a stack that branches", number), t0); err != nil {
			t.Fatal(err)
		}
	}
}

func TestThreePRsTheQueueRefusesAlikeForHalfAnHourReachRootOnceAsOneCause(t *testing.T) {
	s, r := newStore(t), &recorder{}
	ready := []machine.Facts{facts(48001, nil), facts(48002, nil), facts(48003, nil)}

	run(t, s, r, t0, all, ready...)
	refuse(t, s, 48001, 48002, 48003)
	early := run(t, s, r, t0.Add(29*time.Minute), all, ready...)
	late := run(t, s, r, t0.Add(30*time.Minute), all, ready...)
	later := run(t, s, r, t0.Add(33*time.Minute), all, ready...)

	want := []pass.Escalated{{
		Subject: `org/repo: the queue said "exit 1: #<pr> is in a stack that branches" for 30 minutes`,
		Cause:   "on 3 or more open PRs, first #48001: #48001, #48002, #48003",
		To:      "root",
	}}
	if len(clustered(early)) != 0 || !reflect.DeepEqual(clustered(late), want) || len(clustered(later)) != 0 {
		t.Fatalf("early = %+v, late = %+v, later = %+v", clustered(early), clustered(late), clustered(later))
	}
}

func TestThreePRsAGateRefusesAlikeForHalfAnHourReachRootOnceNamingTheGate(t *testing.T) {
	s, r := newStore(t), &recorder{}
	ready := []machine.Facts{facts(48001, nil), facts(48002, nil), facts(48003, nil)}

	run(t, s, r, t0, all, ready...)
	for _, f := range ready {
		if _, err := s.PutFact(repo, f.Number, head, gates.RefusedFact, "unsafe-on-dev", t0); err != nil {
			t.Fatal(err)
		}
	}
	early := run(t, s, r, t0.Add(29*time.Minute), all, ready...)
	late := run(t, s, r, t0.Add(30*time.Minute), all, ready...)
	later := run(t, s, r, t0.Add(33*time.Minute), all, ready...)

	want := []pass.Escalated{{
		Subject: "org/repo: the unsafe-on-dev gate refused for 30 minutes",
		Cause:   "on 3 or more open PRs, first #48001: #48001, #48002, #48003",
		To:      "root",
	}}
	if len(clustered(early)) != 0 || !reflect.DeepEqual(clustered(late), want) || len(clustered(later)) != 0 {
		t.Fatalf("early = %+v, late = %+v, later = %+v", clustered(early), clustered(late), clustered(later))
	}
}

func TestTwoPRsTheQueueRefusesAlikeRaiseNoCluster(t *testing.T) {
	s, r := newStore(t), &recorder{}
	ready := []machine.Facts{facts(48001, nil), facts(48002, nil)}

	run(t, s, r, t0, all, ready...)
	refuse(t, s, 48001, 48002)
	out := run(t, s, r, t0.Add(2*time.Hour), all, ready...)

	if got := clustered(out); len(got) != 0 {
		t.Fatalf("clustered = %+v", got)
	}
}

func TestAGreenHeadOfAStoppedLaneTellsTheGraderDeskAndRootInRecordsTheBusAccepts(t *testing.T) {
	s, r := newStore(t), &recorder{}
	given := facts(48967, func(f *machine.Facts) { f.Finder = "" })

	run(t, s, r, t0, all, given)
	run(t, s, r, t0.Add(time.Hour), all, given)

	got := ungradedPosts(r)
	want := []posted{
		{[]string{"grader-appeals-3"}, bus.Note{Text: "org/repo#48967 at e718a7434f green and ungraded for 60 minutes: no finder grade"}},
		{[]string{"root"}, bus.Escalation{Subject: "org/repo#48967 at e718a7434f", Cause: "UNOWNED, lane-7 is not running: green and ungraded for 60 minutes: no finder grade"}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("posts = %+v", got)
	}
	for _, p := range got {
		raw, err := json.Marshal(p.body)
		if err != nil {
			t.Fatal(err)
		}
		if err := bus.Check(p.body.Kind(), raw, p.to); err != nil {
			t.Fatalf("the bus refuses %+v: %v", p, err)
		}
	}
}

func TestThreePRsWaitingOnAnAutomatedActorOrAReviewerForAnHourReachRoot(t *testing.T) {
	for subject, given := range map[string]func(*machine.Facts){
		"org/repo: grade waited on the pool for 60 minutes":    func(f *machine.Facts) { f.Finder, f.Fidelity = "", "" },
		"org/repo: review waited on a reviewer for 60 minutes": func(f *machine.Facts) { f.Approved = false },
	} {
		t.Run(subject, func(t *testing.T) {
			s, r := newStore(t), &recorder{}
			waiting := []machine.Facts{facts(1, given), facts(2, given), facts(3, given)}

			run(t, s, r, t0, all, waiting...)
			early := run(t, s, r, t0.Add(59*time.Minute), all, waiting...)
			late := run(t, s, r, t0.Add(time.Hour), all, waiting...)

			want := []pass.Escalated{{Subject: subject, Cause: "on 3 or more open PRs, first #1: #1, #2, #3", To: "root"}}
			if len(clustered(early)) != 0 || !reflect.DeepEqual(clustered(late), want) {
				t.Fatalf("early = %+v, late = %+v", clustered(early), clustered(late))
			}
		})
	}
}

func TestAStackBasePastItsDeadlineTellsRootOnceWhatItHolds(t *testing.T) {
	s, r := newStore(t), &recorder{}
	below := facts(45469, func(f *machine.Facts) { f.Branch, f.CI = "below", machine.CIFailure })
	above := facts(45470, func(f *machine.Facts) { f.Branch, f.Base = "above", "below" })
	if _, err := s.WantLane(store.Lane{Name: below.Lane, State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}

	run(t, s, r, t0, all, below, above)
	early := run(t, s, r, t0.Add(machine.LaneClock-time.Minute), all, below, above)
	late := run(t, s, r, t0.Add(machine.LaneClock), all, below, above)
	later := run(t, s, r, t0.Add(machine.LaneClock+time.Minute), all, below, above)

	want := []pass.Escalated{{
		Subject: "org/repo#45469",
		Cause:   "stack base stuck; its fix-red waits on the lane past its deadline at e718a7434f and holds #45470 above it",
		To:      "root",
	}}
	if len(toRoot(early)) != 0 || !reflect.DeepEqual(toRoot(late), want) || len(toRoot(later)) != 0 {
		t.Fatalf("early = %+v, late = %+v, later = %+v", toRoot(early), toRoot(late), toRoot(later))
	}
}

func TestAStackBaseNobodyDrivesTellsRootWhatItHolds(t *testing.T) {
	s, r := newStore(t), &recorder{}
	below := facts(45469, func(f *machine.Facts) { f.Branch, f.Lane, f.Tap = "below", "", "" })
	above := facts(45470, func(f *machine.Facts) { f.Branch, f.Base = "above", "below" })

	out := run(t, s, r, t0, all, below, above)

	want := []pass.Escalated{{Subject: "org/repo#45469", Cause: "stack base stuck; nobody drives it at e718a7434f and holds #45470 above it", To: "root"}}
	if !reflect.DeepEqual(toRoot(out), want) {
		t.Fatalf("escalated = %+v", toRoot(out))
	}
}

func TestABaseWaitingOnlyOnHisTapAsksRootNothing(t *testing.T) {
	waitsOnTap := facts(45468, func(f *machine.Facts) { f.Branch, f.Tap = "lowest", "" })
	for name, base := range map[string]machine.Facts{
		"its own tap":                facts(45469, func(f *machine.Facts) { f.Branch, f.Tap = "below", "" }),
		"the tap of what it carries": facts(45469, func(f *machine.Facts) { f.Branch, f.Tap, f.Carries = "below", "", []int{45468} }),
	} {
		t.Run(name, func(t *testing.T) {
			s, r := newStore(t), &recorder{}
			above := facts(45470, func(f *machine.Facts) { f.Branch, f.Base = "above", "below" })
			if _, err := s.WantLane(store.Lane{Name: above.Lane, State: store.LaneDesired}, t0); err != nil {
				t.Fatal(err)
			}

			for _, at := range []time.Duration{0, machine.OwnerClock, machine.OwnerClock + time.Minute} {
				if got := toRoot(run(t, s, r, t0.Add(at), all, waitsOnTap, base, above)); len(got) != 0 {
					t.Fatalf("at %s: root was asked %+v", at, got)
				}
			}
		})
	}
}

func TestABaseCarryingAPRThatWaitsOnALaneStillTellsRoot(t *testing.T) {
	s, r := newStore(t), &recorder{}
	red := facts(45468, func(f *machine.Facts) { f.Branch, f.Tap, f.CI = "lowest", "", machine.CIFailure })
	base := facts(45469, func(f *machine.Facts) { f.Branch, f.Tap, f.Carries = "below", "", []int{45468} })
	above := facts(45470, func(f *machine.Facts) { f.Branch, f.Base = "above", "below" })
	if _, err := s.WantLane(store.Lane{Name: above.Lane, State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}

	run(t, s, r, t0, all, red, base, above)
	late := run(t, s, r, t0.Add(machine.LaneClock), all, red, base, above)

	want := []pass.Escalated{{Subject: "org/repo#45469", Cause: "stack base stuck; its restack waits on the lane past its deadline at e718a7434f and holds #45470 above it", To: "root"}}
	if got := toRoot(late); !reflect.DeepEqual(got, want) {
		t.Fatalf("escalated = %+v", got)
	}
}

func TestAStackBaseWhoseHeadMovesRaisesOnceForOneCause(t *testing.T) {
	s, r := newStore(t), &recorder{}
	at := func(sha string) machine.Facts {
		return facts(45469, func(f *machine.Facts) { f.Branch, f.Head, f.CI = "below", sha, machine.CIFailure })
	}
	above := facts(45470, func(f *machine.Facts) { f.Branch, f.Base = "above", "below" })
	if _, err := s.WantLane(store.Lane{Name: above.Lane, State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}

	heads := []string{"f1eab2fc27aa", "18ac09aad8bb", "eed99540abcc"}
	raised := make([]pass.Escalated, 0, len(heads))
	for round, sha := range heads {
		start := t0.Add(time.Duration(round) * 2 * machine.LaneClock)
		run(t, s, r, start, all, at(sha), above)
		raised = append(raised, toRoot(run(t, s, r, start.Add(machine.LaneClock), all, at(sha), above))...)
	}

	want := []pass.Escalated{{
		Subject: "org/repo#45469",
		Cause:   "stack base stuck; its fix-red waits on the lane past its deadline at f1eab2fc27 and holds #45470 above it",
		To:      "root",
	}}
	if !reflect.DeepEqual(raised, want) {
		t.Fatalf("raised = %+v", raised)
	}
}

func ungradedPosts(r *recorder) []posted {
	kept := make([]posted, 0, len(r.posts))
	for _, p := range r.posts {
		if strings.Contains(fmt.Sprint(p.body), "green and ungraded") {
			kept = append(kept, p)
		}
	}
	return kept
}

func TestAGreenHeadMissingAGradeTellsTheGraderAndLaneAtAnHourAndRootAtThreeOnce(t *testing.T) {
	s, r := newStore(t), &recorder{}
	if _, err := s.WantLane(store.Lane{Name: "lane-7", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	given := facts(48967, func(f *machine.Facts) { f.Finder, f.Approved = "", false })

	run(t, s, r, t0, all, given)
	run(t, s, r, t0.Add(59*time.Minute), all, given)
	if got := ungradedPosts(r); len(got) != 0 {
		t.Fatalf("before the hour: %+v", got)
	}

	run(t, s, r, t0.Add(time.Hour), all, given)
	run(t, s, r, t0.Add(2*time.Hour), all, given)
	wantHour := posted{[]string{"grader-appeals-3", "lane-7"}, bus.Note{Text: "org/repo#48967 at e718a7434f green and ungraded for 60 minutes: no finder grade"}}
	if got := ungradedPosts(r); !reflect.DeepEqual(got, []posted{wantHour}) {
		t.Fatalf("at the hour: %+v", got)
	}

	run(t, s, r, t0.Add(3*time.Hour), all, given)
	run(t, s, r, t0.Add(4*time.Hour), all, given)
	wantRoot := posted{[]string{"root"}, bus.Escalation{Subject: "org/repo#48967 at e718a7434f", Cause: "green and ungraded for 180 minutes: no finder grade"}}
	if got := ungradedPosts(r); !reflect.DeepEqual(got, []posted{wantHour, wantRoot}) {
		t.Fatalf("at three hours: %+v", got)
	}
}

func TestAnApprovedUngradedHeadLeavesRootToTheRowAlarm(t *testing.T) {
	s, r := newStore(t), &recorder{}
	if _, err := s.WantLane(store.Lane{Name: "lane-7", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	given := facts(48967, func(f *machine.Facts) { f.Finder = "" })

	run(t, s, r, t0, all, given)
	run(t, s, r, t0.Add(3*time.Hour), all, given)
	run(t, s, r, t0.Add(4*time.Hour), all, given)

	got := ungradedPosts(r)
	if len(got) != 1 || !slices.Equal(got[0].to, []string{"grader-appeals-3", "lane-7"}) {
		t.Fatalf("posts = %+v", got)
	}
}

func TestAGreenUngradedHeadNoLaneOwnsTellsNobody(t *testing.T) {
	s, r := newStore(t), &recorder{}
	given := facts(31976, func(f *machine.Facts) { f.Lane, f.Finder, f.Fidelity, f.Tap = "", "", "", "" })

	run(t, s, r, t0, all, given)
	run(t, s, r, t0.Add(3*time.Hour), all, given)

	if got := ungradedPosts(r); len(got) != 0 {
		t.Fatalf("posts = %+v", got)
	}
}

func TestAnotherPersonsPRsALaneCarriesOpenNothingAndTellNobodyForHours(t *testing.T) {
	s, r := newStore(t), &recorder{}
	if _, err := s.WantLane(store.Lane{Name: "lane-7", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	carried := func(number int, change func(*machine.Facts)) machine.Facts {
		return facts(number, func(f *machine.Facts) {
			f.ForeignAuthor, f.Tap = "teammate", ""
			change(f)
		})
	}
	snapshot := []machine.Facts{
		carried(47613, func(*machine.Facts) {}),
		carried(47615, func(f *machine.Facts) { f.Finder, f.Fidelity = "", "" }),
		carried(47644, func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, head }),
	}

	var out pass.Outcome
	for hour := range 4 {
		out = run(t, s, r, t0.Add(time.Duration(hour)*time.Hour), all, snapshot...)
	}

	if len(r.posts) != 0 || len(out.Acts) != 0 || len(out.Escalated) != 0 {
		t.Fatalf("posts = %+v, outcome = %+v", r.posts, out)
	}
	if open, err := s.OpenItems(repo, 0); err != nil || len(open) != 0 {
		t.Fatalf("open = %+v, %v", open, err)
	}
}

func TestANewHeadRestartsTheUngradedClock(t *testing.T) {
	s, r := newStore(t), &recorder{}
	given := facts(48967, func(f *machine.Facts) { f.Finder = "" })
	moved := given
	moved.Head = newHead

	run(t, s, r, t0, all, given)
	run(t, s, r, t0.Add(30*time.Minute), all, moved)
	run(t, s, r, t0.Add(time.Hour), all, moved)

	if got := ungradedPosts(r); len(got) != 0 {
		t.Fatalf("posts = %+v", got)
	}
}

func TestOnlyAGreenOpenHeadThatCanBeGradedStartsTheUngradedClock(t *testing.T) {
	for name, change := range map[string]func(*machine.Facts){
		"graded":   nil,
		"pending":  func(f *machine.Facts) { f.Finder, f.CI = "", machine.CIPending },
		"draft":    func(f *machine.Facts) { f.Finder, f.Draft = "", true },
		"conflict": func(f *machine.Facts) { f.Finder, f.Mergeable = "", machine.MergeableConflict },
		"merged":   func(f *machine.Facts) { f.Finder, f.Merged = "", true },
	} {
		t.Run(name, func(t *testing.T) {
			s, r := newStore(t), &recorder{}
			given := facts(48967, change)

			run(t, s, r, t0, all, given)
			run(t, s, r, t0.Add(4*time.Hour), all, given)

			if got := ungradedPosts(r); len(got) != 0 {
				t.Fatalf("posts = %+v", got)
			}
		})
	}
}

func TestATappedPRWhoseRowIsNotReadyIsNotQueuedAndItsLaneOwnsTheWait(t *testing.T) {
	s, r := newStore(t), &recorder{}

	out := run(t, s, r, t0, all, facts(1, func(f *machine.Facts) { f.RowWait = "not graded" }))

	item := openOne(t, s, 1)
	if out.Verdicts[1].State != machine.StateNotReady || len(out.Acts) != 0 || len(out.Escalated) != 0 {
		t.Fatalf("out = %+v", out)
	}
	if item.Kind != string(machine.ActionRowWait) || item.Mover != machine.MoverLane {
		t.Fatalf("item = %+v", item)
	}
	told := r.of("item")
	if len(told) != 1 || !slices.Equal(told[0].to, []string{"lane-7"}) {
		t.Fatalf("told = %+v", told)
	}
}

func TestRowsWaitingOnTheirLaneAskItAtTheDeadlineAndRootOnceAfterAnHour(t *testing.T) {
	s, r := newStore(t), &recorder{}
	if _, err := s.WantLane(store.Lane{Name: "lane-7", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	waiting := func(f *machine.Facts) { f.RowWait = "its PR body has no Summary" }
	snapshot := []machine.Facts{facts(1, waiting), facts(2, waiting), facts(3, waiting)}

	run(t, s, r, t0, all, snapshot...)
	run(t, s, r, t0.Add(machine.LaneClock+time.Minute), all, snapshot...)
	asked := r.of("ask")
	if len(asked) != 3 || len(r.of("escalation")) != 0 || !slices.ContainsFunc(asked, func(p posted) bool { return p.body == bus.Ask{Text: "org/repo#2 row-wait overdue"} }) {
		t.Fatalf("posts before the hour = %+v", r.posts)
	}
	for _, p := range asked {
		if !slices.Equal(p.to, []string{"lane-7"}) {
			t.Fatalf("asked %+v", p)
		}
	}

	late := run(t, s, r, t0.Add(time.Hour+time.Minute), all, snapshot...)
	run(t, s, r, t0.Add(2*time.Hour), all, snapshot...)

	told := r.of("escalation")
	if len(told) != 1 || !slices.Equal(told[0].to, []string{"root"}) || len(late.Escalated) != 1 || !strings.Contains(late.Escalated[0].Cause, "#1, #2, #3") {
		t.Fatalf("told = %+v, escalated = %+v", told, late.Escalated)
	}
}

func TestARowWaitingOnAStuckChangeBelowItAsksItsLaneNothingAndTheBaseNamesIt(t *testing.T) {
	s, r := newStore(t), &recorder{}
	if _, err := s.WantLane(store.Lane{Name: "lane-7", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	below := facts(45469, func(f *machine.Facts) { f.Branch, f.CI = "below", machine.CIFailure })
	stacked := func(number int, branch, base string) machine.Facts {
		return facts(number, func(f *machine.Facts) {
			f.Branch, f.Base, f.RowWait = branch, base, fmt.Sprintf("the change below it, %s, has not landed", base)
		})
	}
	snapshot := []machine.Facts{below, stacked(45470, "above", "below"), stacked(45471, "top", "above"), stacked(45472, "side", "below")}

	run(t, s, r, t0, all, snapshot...)
	late := run(t, s, r, t0.Add(machine.LaneClock), all, snapshot...)
	hour := run(t, s, r, t0.Add(time.Hour+time.Minute), all, snapshot...)

	for _, number := range []int{45470, 45471, 45472} {
		if open, err := s.OpenItems(repo, number); err != nil || len(open) != 0 {
			t.Fatalf("#%d opened %+v, %v", number, open, err)
		}
	}
	if asked := r.of("ask"); len(asked) != 1 || asked[0].body != (bus.Ask{Text: "org/repo#45469 fix-red overdue"}) {
		t.Fatalf("asked = %+v", asked)
	}
	want := []pass.Escalated{{Subject: "org/repo#45469", Cause: "stack base stuck; its fix-red waits on the lane past its deadline at e718a7434f and holds #45470, #45471, #45472 above it", To: "root"}}
	if !reflect.DeepEqual(toRoot(late), want) || len(toRoot(hour)) != 0 {
		t.Fatalf("late = %+v, after the hour = %+v", toRoot(late), toRoot(hour))
	}
}

func TestATappedPRWithNoRowLandsNothingAndMetaDeskHearsOnce(t *testing.T) {
	s, r := newStore(t), &recorder{}
	unread := func(f *machine.Facts) { f.RowWait = gates.NoRow }

	first := run(t, s, r, t0, all, facts(1, unread), facts(2, unread))
	run(t, s, r, t0.Add(time.Minute), all, facts(1, unread), facts(2, unread))

	told := r.of("escalation")
	if len(first.Acts) != 0 || len(told) != 1 || !slices.Equal(told[0].to, []string{pass.RowsDesk}) {
		t.Fatalf("acts = %+v, told = %+v", first.Acts, told)
	}
	if said := told[0].body.(bus.Escalation); !strings.Contains(said.Subject, "#1, #2") {
		t.Fatalf("said = %+v", said)
	}
}

func TestAnExemptPRWithNoRowLandsUnderItsRuleAndTellsNobody(t *testing.T) {
	s, r := newStore(t), &recorder{}

	out := run(t, s, r, t0, all, facts(1, func(f *machine.Facts) { f.Tap, f.Exempt, f.RowWait = "", true, gates.NoRow }))

	if out.Verdicts[1].Action != machine.ActionEnqueue || len(r.of("escalation")) != 0 {
		t.Fatalf("out = %+v, posts = %+v", out, r.posts)
	}
}

func TestATappedHeadOverdueOnAMoverThisHostDoesNotRunSaysWhy(t *testing.T) {
	landing := []machine.Mover{machine.MoverSupervisor}
	ungraded := func(f *machine.Facts) { f.Fidelity, f.Tap, f.Exempt = "", "", true }
	late := t0.Add(machine.GradeClock + time.Minute)
	for name, test := range map[string]struct {
		change func(*machine.Facts)
		live   []machine.Mover
		at     time.Time
		stuck  bool
	}{
		"a go's head past the deadline":      {ungraded, landing, late, true},
		"before the deadline":                {ungraded, landing, t0.Add(time.Minute), false},
		"a head nobody tapped":               {func(f *machine.Facts) { f.Fidelity, f.Tap = "", "" }, landing, late, false},
		"on a host that runs the mover":      {ungraded, all, late, false},
		"a head the landing pass moves here": {nil, landing, late, false},
		"a conflicted head under a hold":     {func(f *machine.Facts) { f.Mergeable, f.Hold = machine.MergeableConflict, machine.MoverRoot }, landing, t0.Add(machine.LaneClock + time.Minute), false},
	} {
		t.Run(name, func(t *testing.T) {
			s, r := newStore(t), &recorder{}
			f := facts(1, test.change)
			run(t, s, r, t0, test.live, f)

			out := run(t, s, r, test.at, test.live, f)

			why := out.Stuck[1]
			if test.stuck != (why != "") {
				t.Fatalf("stuck = %q, want stuck %v", why, test.stuck)
			}
			if test.stuck && (!strings.Contains(why, "grading") || !strings.Contains(why, "no fidelity grade")) {
				t.Fatalf("why = %q names neither the state nor the missing grade", why)
			}
		})
	}
}
