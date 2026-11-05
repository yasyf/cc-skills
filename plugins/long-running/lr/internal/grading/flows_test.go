package grading

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func openedAt(t *testing.T, s *store.Store, number int, head string) {
	t.Helper()
	if _, err := s.OpenItem(store.NewItem{Kind: ItemKind, Repo: repo, Number: number, Head: head, Mover: machine.MoverPool, Deadline: machine.GradeClock}, t0); err != nil {
		t.Fatal(err)
	}
}

func askPosted(t *testing.T, draft client.Draft) int64 {
	t.Helper()
	draft.To = []string{grader.FinderLane}
	return post(t, draft)
}

func beginAsks(t *testing.T, f *poolFixture) {
	t.Helper()
	if _, err := f.pool.Whole(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
}

func recordsOf(t *testing.T, kind string) []client.Record {
	t.Helper()
	found, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{kind}})
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func TestWhatARecordAsksOfTheFinderDesk(t *testing.T) {
	to := []string{grader.FinderLane}
	for name, tt := range map[string]struct {
		record client.Record
		want   []string
	}{
		"an ask for a whole pass":          {client.Record{Kind: kindAsk, Lane: "lane-7", To: to, Text: "finder: whole on org/repo#45489 and org/repo#45490"}, []string{"45489", "45490"}},
		"another repo's PR":                {client.Record{Kind: kindAsk, Lane: "lane-7", To: to, Text: "finder: whole on other/repo#45489"}, []string{}},
		"a bare number":                    {client.Record{Kind: kindAsk, Lane: "lane-7", To: to, Text: "Finder: Whole #45489, not #12"}, []string{"45489"}},
		"an ask for anything else":         {client.Record{Kind: kindAsk, Lane: "lane-7", To: to, Text: "how is #45489 doing"}, nil},
		"an ask to another lane":           {client.Record{Kind: kindAsk, Lane: "lane-7", To: []string{"root"}, Text: "finder: whole #45489"}, nil},
		"a root regrade order":             {client.Record{Kind: kindDecide, Lane: rootLane, To: to, Text: "finder-desk: regrade #45489; leave #45490"}, []string{"45489"}},
		"a lane's regrade order":           {client.Record{Kind: kindDecide, Lane: "lane-7", To: to, Text: "finder-desk: regrade #45489"}, nil},
		"a coordinator decide with no ask": {client.Record{Kind: kindDecide, Lane: rootLane, To: to, Text: "#45489 is settled"}, []string{}},
	} {
		got := requested(tt.record, repo)
		if len(got) != len(tt.want) || (len(got) > 0 && !reflect.DeepEqual(got, tt.want)) {
			t.Errorf("%s: requested = %v, want %v", name, got, tt.want)
		}
	}
}

func TestAWholeAskStartsOneFullPassAtTheLiveHeadAndEndsWithItsJob(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo, "checkout": "/srv/main"}, "finder": map[string]any{"sprite": "dar-1"}})
	beginAsks(t, f)
	ask := askPosted(t, client.Draft{Lane: "lane-7", Kind: kindAsk, Text: "finder: whole on #45489 please", PRs: []string{pull}})

	started, err := f.pool.Whole(context.Background(), map[int]machine.Facts{number: facts(head)})
	if err != nil {
		t.Fatal(err)
	}

	base := "45489" + wholeMark + strconv.FormatInt(ask, 10)
	if want := []string{"dar-1/" + grader.FinderLane + "/" + base}; !reflect.DeepEqual(started, want) {
		t.Fatalf("started = %v, want %v", started, want)
	}
	spec := readJob[grader.FinderJob](t, f.finderDir(base))
	if !spec.Full || spec.Head != "" || spec.Since != "" || spec.Prior != nil || spec.Lane != "lane-7" || spec.Repo != repo {
		t.Errorf("job = %+v", spec)
	}
	sameStrings(t, spec.To, []string{runnerLane, "lane-7"})

	again, err := f.pool.Whole(context.Background(), nil)
	if err != nil || len(again) != 0 {
		t.Fatalf("while it runs: started = %v, %v", again, err)
	}

	f.shell.exitAll()
	writeResult(t, f.finderDir(base), `{"verdict":"ok"}`, f.now)
	if _, err := f.pool.Whole(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	if exists(filepath.Join(f.finderDir(pendingDir), base)) {
		t.Error("the ask is still pending after its pass settled")
	}
}

func TestTheFirstReadOfAsksStartsAtTheNewestRecord(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo}, "finder": map[string]any{}})
	askPosted(t, client.Draft{Lane: "lane-7", Kind: kindAsk, Text: "finder: whole #45489", PRs: []string{pull}})

	started, err := f.pool.Whole(context.Background(), nil)

	if err != nil || len(started) != 0 {
		t.Fatalf("started = %v, %v", started, err)
	}
}

func TestAnAskArrivingAfterAFirstReadThatFoundNoneIsNotLost(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo}, "finder": map[string]any{}})
	beginAsks(t, f)
	post(t, client.Draft{Lane: "lane-7", Kind: kindNote, Text: "a first record"})
	askPosted(t, client.Draft{Lane: "lane-7", Kind: kindAsk, Text: "finder: whole #45489", PRs: []string{pull}})

	started, err := f.pool.Whole(context.Background(), nil)

	if err != nil || len(started) != 1 {
		t.Fatalf("started = %v, %v", started, err)
	}
}

func TestAWholeAskCarriesTheBugItemThePRBodyNames(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo, "checkout": "/srv/main"}, "finder": map[string]any{"sprite": "dar-1"}})
	f.shell.view = `{"title":"a change","body":"Bug item 52.","headRefName":"lane-7"}`
	post(t, client.Draft{Lane: "slack-bugs-2", Kind: kindIncident, Attrs: map[string]string{"item": "52"}, Text: "Josh said the session is offline"})
	beginAsks(t, f)
	ask := askPosted(t, client.Draft{Lane: "lane-7", Kind: kindAsk, Text: "finder: whole on #45489 please", PRs: []string{pull}})

	if _, err := f.pool.Whole(context.Background(), map[int]machine.Facts{number: facts(head)}); err != nil {
		t.Fatal(err)
	}

	spec := readJob[grader.FinderJob](t, f.finderDir("45489"+wholeMark+strconv.FormatInt(ask, 10)))
	if len(spec.Items) != 1 || spec.Items[0].Text != "Josh said the session is offline" {
		t.Errorf("whole items = %+v", spec.Items)
	}
}

func TestAWholeRecordPostedSinceThePRsNewestHeadAnswersTheAsk(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo}, "finder": map[string]any{}})
	beginAsks(t, f)
	post(t, client.Draft{Lane: "lane-7", Kind: kindHead, Text: "#45489 " + head, PRs: []string{pull}})
	post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: "finder: ok " + head[:7] + " sol finder over whole abc1234^.." + head[:7], PRs: []string{pull}})
	askPosted(t, client.Draft{Lane: "lane-7", Kind: kindAsk, Text: "finder: whole #45489", PRs: []string{pull}})

	started, err := f.pool.Whole(context.Background(), nil)

	if err != nil || len(started) != 0 || len(f.shell.spawns) != 0 {
		t.Fatalf("started = %v, %v, spawns %d", started, err, len(f.shell.spawns))
	}
}

func TestABodyEditHeadRecordRechecksTheMajorAtThatHeadAndNothingGradedOk(t *testing.T) {
	for grade, wantStarted := range map[string]int{"major " + head[:7] + " 1 open over whole abc1234^.." + head[:7] + ": follow.go:40 second fix": 1, "ok " + head[:7] + " sol finder over whole abc1234^.." + head[:7] + ": no majors": 0} {
		t.Run(grade, func(t *testing.T) {
			f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo, "checkout": "/srv/main"}, "finder": map[string]any{"sprite": "dar-1"}})
			beginAsks(t, f)
			post(t, client.Draft{Lane: "lane-7", Kind: kindHead, Text: "#45489 " + head, PRs: []string{pull}})
			graded := post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: "finder: " + grade, PRs: []string{pull}})
			edit := post(t, client.Draft{Lane: "lane-7", Kind: kindHead, To: []string{grader.FinderLane}, Text: head[:7] + " body edit", Attrs: map[string]string{"head:45489": head}, PRs: []string{pull}})

			started, err := f.pool.Whole(context.Background(), map[int]machine.Facts{number: facts(head)})
			if err != nil || len(started) != wantStarted {
				t.Fatalf("started = %v, %v", started, err)
			}
			if wantStarted == 0 {
				return
			}
			spec := readJob[grader.FinderJob](t, f.finderDir("45489"+wholeMark+strconv.FormatInt(edit, 10)))
			if spec.Full || spec.Head != head || spec.Prior == nil || spec.Prior.Seq != graded || spec.Lane != "lane-7" {
				t.Errorf("job = %+v", spec)
			}
		})
	}
}

func TestARecheckCarriesTheBugItemThePRBodyNames(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo, "checkout": "/srv/main"}, "finder": map[string]any{"sprite": "dar-1"}})
	f.shell.view = `{"title":"a change","body":"Bug item 52.","headRefName":"lane-7"}`
	post(t, client.Draft{Lane: "slack-bugs-2", Kind: kindIncident, Attrs: map[string]string{"item": "52"}, Text: "Josh said the session is offline"})
	beginAsks(t, f)
	post(t, client.Draft{Lane: "lane-7", Kind: kindHead, Text: "#45489 " + head, PRs: []string{pull}})
	post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: "finder: major " + head[:7] + " 1 open over whole abc1234^.." + head[:7] + ": follow.go:40 second fix", PRs: []string{pull}})
	edit := post(t, client.Draft{Lane: "lane-7", Kind: kindHead, To: []string{grader.FinderLane}, Text: head[:7] + " body edit", Attrs: map[string]string{"head:45489": head}, PRs: []string{pull}})

	if _, err := f.pool.Whole(context.Background(), map[int]machine.Facts{number: facts(head)}); err != nil {
		t.Fatal(err)
	}

	spec := readJob[grader.FinderJob](t, f.finderDir("45489"+wholeMark+strconv.FormatInt(edit, 10)))
	if len(spec.Items) != 1 || spec.Items[0].Text != "Josh said the session is offline" {
		t.Errorf("recheck items = %+v", spec.Items)
	}
}

func TestAPassThatFoundAHeadNoRecordGradedQueuesOneWholePass(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo}, "finder": map[string]any{}})
	beginAsks(t, f)
	name := f.started(t, GraderFinder)
	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir), `{"skipped":"replays a head no finder record graded","ungraded":true}`, f.now)

	if _, err := f.pool.Settle(context.Background(), []string{name}); err != nil {
		t.Fatal(err)
	}
	started, err := f.pool.Whole(context.Background(), map[int]machine.Facts{number: facts(head)})
	if err != nil {
		t.Fatal(err)
	}

	base := "45489" + wholeMark + head[:headChars]
	if len(started) != 1 || refOf(started[0]).base != base {
		t.Fatalf("started = %v, want %s", started, base)
	}
	if !readJob[grader.FinderJob](t, f.finderDir(base)).Full {
		t.Error("the queued pass is not a full pass")
	}
}

func TestANewerHeadNeverCancelsAWholePass(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	whole := localBox + "/" + grader.FinderLane + "/45489" + wholeMark + "300001"
	f.hold(t, whole)
	if err := os.MkdirAll(f.finderDir("45489"+wholeMark+"300001"), 0o750); err != nil {
		t.Fatal(err)
	}

	f.started(t, GraderFinder)

	if exists(filepath.Join(f.finderDir("45489"+wholeMark+"300001"), grader.CancelledFile)) {
		t.Error("cancelled a whole pass")
	}
}

func TestARootsFirstOrderPutsItsPRAheadOfEveryPriority(t *testing.T) {
	for text, want := range map[string][]string{
		"#48141 at a04386d is first in every grading queue": {"48141"},
		"#48141 is second. Grade #48142 first":              {"48142"},
		"Grade these first: #49730, #49912. #2 goes last":   {"49730", "49912"},
		"#48141 goes last": nil,
		"#48141\nfirst":    nil,
		"#48141 " + strings.Repeat("x", 161) + " first": nil,
	} {
		if got := namedFirst(text); !slices.Equal(got, want) {
			t.Errorf("namedFirst(%q) = %v, want %v", text, got, want)
		}
	}
}

func TestItemsRankRootFirstThenIncidentThenPriorityThenTappedThenOldest(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"ledger": "L1"}, "finder": map[string]any{}})
	if err := f.store.PutLedgerRows("L1", map[string]map[string]string{"6": {"priority": "5"}, "5": {"priority": "50"}}, t0); err != nil {
		t.Fatal(err)
	}
	post(t, client.Draft{Lane: rootLane, Kind: kindDecide, Text: "#1 is first in every grading queue"})
	snapshot := map[int]machine.Facts{
		1: {Number: 1},
		2: {Number: 2},
		3: {Number: 3, Tap: machine.TapTapped},
		4: {Number: 4, Lane: "incident-7-fix"},
		5: {Number: 5},
		6: {Number: 6},
	}
	open := make([]store.Item, 0, len(snapshot))
	for _, n := range []int{2, 3, 5, 6, 4, 1} {
		open = append(open, store.Item{ID: int64(n), Kind: ItemKind, Number: n})
	}
	f.now = time.Now()

	ranked, err := f.pool.Plan(context.Background(), open, snapshot, 0, f.now)
	if err != nil {
		t.Fatal(err)
	}

	got := make([]int, 0, len(ranked))
	for _, item := range ranked {
		got = append(got, item.Number)
	}
	if want := []int{1, 4, 6, 5, 3, 2}; !slices.Equal(got, want) {
		t.Fatalf("order = %v, want %v", got, want)
	}
}

func TestARootAskPutsItsPRsFirstAndAGreenApprovedRowGoesAheadOfTheRest(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"ledger": "L1"}, "finder": map[string]any{}})
	if err := f.store.PutLedgerRows("L1", map[string]map[string]string{"4": {"priority": "5"}}, t0); err != nil {
		t.Fatal(err)
	}
	post(t, client.Draft{Lane: rootLane, Kind: kindAsk, Text: "Grade these first: #1, #2. His pick makes them the critical path."})
	snapshot := map[int]machine.Facts{
		1: {Number: 1},
		2: {Number: 2},
		3: {Number: 3, CI: machine.CISuccess, Approved: true},
		4: {Number: 4, CI: machine.CISuccess},
		5: {Number: 5, Approved: true},
	}
	open := make([]store.Item, 0, len(snapshot))
	for _, n := range []int{5, 4, 3, 2, 1} {
		open = append(open, store.Item{ID: int64(n), Kind: ItemKind, Number: n})
	}
	f.now = time.Now()

	ranked, err := f.pool.Plan(context.Background(), open, snapshot, 0, f.now)
	if err != nil {
		t.Fatal(err)
	}

	got := make([]int, 0, len(ranked))
	for _, item := range ranked {
		got = append(got, item.Number)
	}
	if want := []int{2, 1, 3, 4, 5}; !slices.Equal(got, want) {
		t.Fatalf("order = %v, want %v", got, want)
	}
}

func TestReservedSlotsTakeOnlyAPRTheRootPutsFirst(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"concurrent": 2, "reserved": 1}})
	busy := []store.Worker{{Name: localBox + "/" + grader.FinderLane + "/1-aaaaaaaaaaaa"}}

	if _, err := f.pool.place(f.pool.desks[GraderFinder], busy, false); err == nil {
		t.Error("an ordinary PR took a reserved slot")
	}
	if _, err := f.pool.place(f.pool.desks[GraderFinder], busy, true); err != nil {
		t.Errorf("a first PR found no slot: %v", err)
	}
}

func TestEachSpritePostsOneBeatEveryTenMinutesNamingItsPasses(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": []string{"dar-1", "dar-2"}}, "fidelity": map[string]any{"sprite": "dar-1"}})
	f.now = time.Now()
	f.hold(t, "dar-1/"+grader.FinderLane+"/1-aaaaaaaaaaaa", "dar-1/"+grader.FidelityLane+"/1-aaaaaaaaaaaa")
	for _, lane := range []string{grader.FinderLane, grader.FidelityLane} {
		directory := filepath.Join(f.pool.root, lane, "1-aaaaaaaaaaaa")
		if err := os.MkdirAll(directory, 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(directory, pidFile), []byte("9"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(filepath.Join(directory, pidFile), f.now.Add(-5*time.Minute), f.now.Add(-5*time.Minute)); err != nil {
			t.Fatal(err)
		}
	}

	beginAsks(t, f)
	beginAsks(t, f)

	states := recordsOf(t, kindState)
	if len(states) != 2 {
		t.Fatalf("states = %+v, want one per Sprite", states)
	}
	if want := "sprite dar-1: 1 finder and 1 fidelity passes, oldest 5 min"; states[0].Text != want {
		t.Errorf("text = %q, want %q", states[0].Text, want)
	}
	if want := "sprite dar-2: 0 finder and 0 fidelity passes, none running"; states[1].Text != want {
		t.Errorf("text = %q, want %q", states[1].Text, want)
	}

	f.now = f.now.Add(spriteBeatEvery)
	beginAsks(t, f)
	later := recordsOf(t, kindState)
	if !slices.ContainsFunc(later, func(r client.Record) bool {
		return r.Text == "sprite dar-1: 1 finder and 1 fidelity passes, oldest 15 min"
	}) {
		t.Errorf("states after ten minutes = %+v, want dar-1's next beat", later)
	}
}

func TestASpriteWhosePassesAreOldWithNoneSettledPostsADefectInsteadOfItsBeat(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": "dar-1"}})
	f.now = time.Now()
	f.hold(t, "dar-1/"+grader.FinderLane+"/1-aaaaaaaaaaaa")
	directory := f.finderDir("1-aaaaaaaaaaaa")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	pid := filepath.Join(directory, pidFile)
	if err := os.WriteFile(pid, []byte("9"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := f.now.Add(-spriteStalledFor)
	if err := os.Chtimes(pid, old, old); err != nil {
		t.Fatal(err)
	}

	beginAsks(t, f)

	defects := recordsOf(t, kindDefect)
	if len(defects) != 1 || !strings.HasPrefix(defects[0].Text, "SPRITE-STALLED dar-1: 1 passes running, oldest 30 min") || !slices.Contains(defects[0].To, rootLane) {
		t.Fatalf("defects = %+v", defects)
	}
	if got := len(recordsOf(t, kindState)); got != 0 {
		t.Errorf("states = %d, want the defect instead", got)
	}
}

func TestASpriteThatSettledAPassLatelyIsNotStalled(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": "dar-1"}})
	f.now = time.Now()
	name := f.started(t, GraderFinder)
	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir), `{"verdict":"ok"}`, f.now)
	if _, err := f.pool.Settle(context.Background(), []string{name}); err != nil {
		t.Fatal(err)
	}
	f.hold(t, "dar-1/"+grader.FinderLane+"/1-aaaaaaaaaaaa")
	directory := f.finderDir("1-aaaaaaaaaaaa")
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	pid := filepath.Join(directory, pidFile)
	if err := os.WriteFile(pid, []byte("9"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := f.now.Add(-time.Hour)
	if err := os.Chtimes(pid, old, old); err != nil {
		t.Fatal(err)
	}

	beginAsks(t, f)

	if got := len(recordsOf(t, kindDefect)); got != 0 {
		t.Errorf("defects = %d", got)
	}
}

func TestALastFailedFinderAttemptTellsRootOnce(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	for range maxAttempts {
		f.started(t, GraderFinder)
		f.shell.exitAll()
	}

	for range 2 {
		if _, err := f.dispatch(t, GraderFinder); err == nil {
			t.Fatal("started a fourth attempt")
		}
	}

	defects := recordsOf(t, kindDefect)
	if len(defects) != 1 || !strings.HasPrefix(defects[0].Text, "FINDER-FAILED finder:45489:"+head+" lane-7: #45489 "+head[:headChars]) || !slices.Contains(defects[0].To, rootLane) {
		t.Fatalf("defects = %+v", defects)
	}
}

func TestAStoppedHeadTellsTheDesksAndItsLaneOnce(t *testing.T) {
	f := newPool(t, map[string]any{"tooling_lane": "meta-1", "finder": map[string]any{}})

	for range 2 {
		if err := f.pool.TellStopped(context.Background(), facts(head), "fidelity: no reader took it"); err != nil {
			t.Fatal(err)
		}
	}

	reports := recordsOf(t, kindReport)
	if len(reports) != 1 || !strings.Contains(reports[0].Text, "#45489 "+head[:headChars]) || !strings.Contains(reports[0].Text, "no reader took it") {
		t.Fatalf("reports = %+v", reports)
	}
	sameStrings(t, reports[0].To, []string{appealsDesk, "meta-1", "lane-7"})
}

func TestALastFailedFidelityAttemptTellsItsHumanDesks(t *testing.T) {
	f := newPool(t, map[string]any{"fidelity": map[string]any{}})
	for range maxAttempts {
		f.started(t, GraderFidelity)
		f.shell.exitAll()
	}

	if _, err := f.dispatch(t, GraderFidelity); err == nil {
		t.Fatal("started a fourth attempt")
	}

	defects := recordsOf(t, kindDefect)
	if len(defects) != 1 || !strings.HasPrefix(defects[0].Text, "FIDELITY-FAILED ") || !slices.Contains(defects[0].To, "fidelity-desk") || !slices.Contains(defects[0].To, rootLane) || slices.Contains(defects[0].To, grader.FidelityLane) {
		t.Fatalf("defects = %+v", defects)
	}
}

func TestAToolingFailureTellsTheToolingLaneOncePerCauseAndHour(t *testing.T) {
	f := newPool(t, map[string]any{"tooling_lane": "tooling-1", "finder": map[string]any{}})
	f.now = time.Now().Truncate(time.Hour)
	name := f.started(t, GraderFinder)
	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir), `{"error":"git failed in /srv/checkout at e718a74"}`, f.now)

	for range 2 {
		if _, err := f.pool.Settle(context.Background(), []string{name}); err != nil {
			t.Fatal(err)
		}
	}

	reports := recordsOf(t, kindReport)
	if len(reports) != 1 || !strings.HasPrefix(reports[0].Text, "GRADER-TOOLING grader-tooling:") || !strings.Contains(reports[0].Text, "git failed in /srv/checkout") || !slices.Contains(reports[0].To, "tooling-1") {
		t.Fatalf("reports = %+v", reports)
	}
}

func TestALostPassTellsItsPRsLaneOnceThatItRunsAgain(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	f.now = time.Now()
	name := f.started(t, GraderFinder)
	f.shell.exitAll()

	for range 2 {
		if _, err := f.pool.Settle(context.Background(), []string{name}); err != nil {
			t.Fatal(err)
		}
	}

	var lost []string
	for _, note := range recordsOf(t, kindNote) {
		if strings.HasPrefix(note.Text, "GRADER-LOST ") {
			lost = append(lost, note.Text)
		}
	}
	if len(lost) != 1 || !strings.Contains(lost[0], "the pool runs it again, up to 3 attempts") {
		t.Fatalf("lost = %q", lost)
	}
}

func TestAFailedPassTellsItsPRsLaneOnceAtItsFirstFailure(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	f.now = time.Now()
	name := f.started(t, GraderFinder)
	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir), `{"error":"TimeoutExpired: git --no-lazy-fetch range-diff --no-patch timed out after 15m0s"}`, f.now)

	for range 2 {
		if _, err := f.pool.Settle(context.Background(), []string{name}); err != nil {
			t.Fatal(err)
		}
	}

	notes := recordsOf(t, kindNote)
	if len(notes) != 1 || !strings.HasPrefix(notes[0].Text, "GRADER-FAILED #45489 "+head[:headChars]+": its finder pass died: TimeoutExpired: git --no-lazy-fetch range-diff") || !slices.Contains(notes[0].To, "lane-7") {
		t.Fatalf("notes = %+v", notes)
	}
}

func TestACancelledJobWhoseCodexWasKilledTellsNobody(t *testing.T) {
	f := newPool(t, map[string]any{"tooling_lane": "tooling-1", "finder": map[string]any{}})
	f.now = time.Now().Truncate(time.Hour)
	name := f.started(t, GraderFinder)
	f.shell.exitAll()
	directory := f.finderDir(jobDir)
	if err := os.WriteFile(filepath.Join(directory, grader.CancelledFile), []byte("a newer head\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	writeResult(t, directory, `{"error":"codex-ask -m luna --schema exited 143"}`, f.now)

	if _, err := f.pool.Settle(context.Background(), []string{name}); err != nil {
		t.Fatal(err)
	}

	if got := recordsOf(t, kindReport); len(got) != 0 {
		t.Errorf("reports = %+v", got)
	}
}

func TestNoToolingLaneMeansNoToolingNotice(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	name := f.started(t, GraderFinder)
	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir), `{"error":"git failed"}`, f.now)

	if _, err := f.pool.Settle(context.Background(), []string{name}); err != nil {
		t.Fatal(err)
	}

	if got := len(recordsOf(t, kindReport)); got != 0 {
		t.Errorf("reports = %d", got)
	}
}

func TestActStartsWholePassesAndRanksThroughTheJobPool(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo}, "finder": map[string]any{}})
	openedAt(t, f.store, number, head)
	graded := map[int]machine.Facts{number: facts(head, func(mutated *machine.Facts) { mutated.Fidelity = machine.FidelityPass })}
	beginAsks(t, f)
	post(t, client.Draft{Lane: "lane-7", Kind: kindNote, Text: "a first record"})
	askPosted(t, client.Draft{Lane: "lane-7", Kind: kindAsk, Text: "finder: whole #45490", PRs: []string{repo + "#45490"}})

	dispatched, err := Act(context.Background(), f.store, f.pool, items(t, f.store), graded, t0, 1)
	if err != nil {
		t.Fatal(err)
	}

	if len(dispatched) != 1 || dispatched[0].Grader != GraderFinder {
		t.Fatalf("dispatched = %v", dispatched)
	}
	workers := workerNames(t, f.store)
	if !slices.ContainsFunc(workers, isWhole) || len(workers) != 2 {
		t.Fatalf("workers = %v, want the head's pass and one whole pass", workers)
	}
}

func TestAPassCarriesTheOwnersCheckedPicksToItsGrader(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	if err := f.store.PutRule("rule/abc", "polling is fine here", "2026-11-02", "predicate", "answers:polls every"); err != nil {
		t.Fatal(err)
	}
	if _, err := f.pool.Plan(context.Background(), nil, nil, 0, t0); err != nil {
		t.Fatal(err)
	}

	f.started(t, GraderFinder)

	picks := readJob[grader.FinderJob](t, f.finderDir(jobDir)).Picks
	if len(picks) != 1 || picks[0].ID != "rule/abc" || picks[0].CheckRef != "answers:polls every" {
		t.Fatalf("picks = %+v", picks)
	}
}
