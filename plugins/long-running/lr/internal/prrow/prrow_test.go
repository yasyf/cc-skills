package prrow

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	testRepo = "o/r"
	headA    = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	headB    = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	fullBody = "## Summary\n\nA user sees it.\n\n## Risk profile\n\nLow.\n\n## Design\n\nThis carries plan step 1.\n"
)

func readyRow() store.ReviewRow {
	return store.ReviewRow{
		Repo: testRepo, Number: 1, Head: headA, State: StateOpen, CI: CIGreen, Approvers: "a", Grade: GradePass, GradeFindings: None,
		Mergeable: string(machine.MergeableClean), Queue: string(machine.QueueNone), Hold: None, SupersededBy: None, Parent: None, ParentState: None,
		ForUsers: "a user sees it", PlanRow: "plan step 1", Risk: "low",
	}
}

func TestEachUnmetConditionKeepsARowFromReadyAndSaysWhy(t *testing.T) {
	if row := Decide(readyRow()); !row.ReadyForTap || row.WaitsOn != onlyHim {
		t.Fatalf("a full row = %+v, want ready and waiting only on him", row)
	}
	for _, tt := range []struct {
		name   string
		change func(*store.ReviewRow)
		says   string
	}{
		{"landed", func(r *store.ReviewRow) { r.State = StateLanded }, "landed"},
		{"closed", func(r *store.ReviewRow) { r.State = StateClosed }, "closed"},
		{"superseded", func(r *store.ReviewRow) { r.SupersededBy = "replaced by #9" }, "replaced by #9"},
		{"draft", func(r *store.ReviewRow) { r.State = StateDraft }, "draft"},
		{"held", func(r *store.ReviewRow) { r.Hold = "cutover:the pick" }, "cutover:the pick"},
		{"queued", func(r *store.ReviewRow) { r.State = StateQueued }, "merge queue"},
		{"parent open", func(r *store.ReviewRow) { r.Parent, r.ParentState = "#7", ParentOpen }, "#7, has not landed"},
		{"parent closed", func(r *store.ReviewRow) { r.Parent, r.ParentState = "#7", ParentClosed }, "closed without landing"},
		{"parent unknown", func(r *store.ReviewRow) { r.Parent, r.ParentState = "below", ParentUnknown }, "branch below, has no row"},
		{"no head", func(r *store.ReviewRow) { r.Head = "" }, "names its head"},
		{"red", func(r *store.ReviewRow) { r.CI = CIRed }, "CI is red"},
		{"pending", func(r *store.ReviewRow) { r.CI = CIPending }, "CI is running"},
		{"no review", func(r *store.ReviewRow) { r.Approvers = None }, "no approving review"},
		{"ungraded", func(r *store.ReviewRow) { r.Grade = GradePending }, "not graded"},
		{"failed grade", func(r *store.ReviewRow) { r.Grade, r.GradeFindings = GradeFail, "finder: two majors" }, "finder: two majors"},
		{"unknown mergeable", func(r *store.ReviewRow) { r.Mergeable = string(machine.MergeableUnknown) }, "mergeability is unknown"},
		{"conflict", func(r *store.ReviewRow) { r.Mergeable = string(machine.MergeableConflict) }, "mergeability is conflict"},
		{"unsafe body", func(r *store.ReviewRow) { r.UnsafeWait = "this waits on the cutover pick" }, "waits to go live"},
		{"no for users", func(r *store.ReviewRow) { r.ForUsers = "" }, "no Summary"},
		{"no plan", func(r *store.ReviewRow) { r.PlanRow = "" }, "names no plan: open a line with"},
		{"no risk", func(r *store.ReviewRow) { r.Risk = "" }, "no Risk profile"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			row := readyRow()
			tt.change(&row)
			row = Decide(row)
			if row.ReadyForTap || !strings.Contains(row.WaitsOn, tt.says) {
				t.Fatalf("row = %+v, want not ready and waits_on naming %q", row, tt.says)
			}
		})
	}
}

func TestHisApprovalStandsBehindAPendingCheckAndOnlyTheRowsStandingRefusesIt(t *testing.T) {
	for _, tt := range []struct {
		name    string
		change  func(*store.ReviewRow)
		refuses string
	}{
		{"ready", func(*store.ReviewRow) {}, ""},
		{"CI running", func(r *store.ReviewRow) { r.CI = CIPending }, ""},
		{"CI red", func(r *store.ReviewRow) { r.CI = CIRed }, ""},
		{"no review", func(r *store.ReviewRow) { r.Approvers = None }, ""},
		{"fidelity missing", func(r *store.ReviewRow) { r.Grade, r.GradeFindings = GradeFail, "fidelity: missing" }, ""},
		{"ungraded", func(r *store.ReviewRow) { r.Grade = GradePending }, ""},
		{"conflict", func(r *store.ReviewRow) { r.Mergeable = string(machine.MergeableConflict) }, ""},
		{"the change below open", func(r *store.ReviewRow) { r.Parent, r.ParentState = "#7", ParentOpen }, ""},
		{"queued untapped", func(r *store.ReviewRow) { r.State = StateQueued }, ""},
		{"held", func(r *store.ReviewRow) { r.Hold = "root:the cutover" }, ""},
		{"held with CI running", func(r *store.ReviewRow) { r.Hold, r.CI = "root:the cutover", CIPending }, ""},
		{"superseded", func(r *store.ReviewRow) { r.SupersededBy = "replaced by #9" }, "Nothing: superseded, replaced by #9"},
		{"draft", func(r *store.ReviewRow) { r.State = StateDraft }, "it is a draft"},
		{"closed", func(r *store.ReviewRow) { r.State = StateClosed }, "Nothing: it was closed without landing."},
		{"no Summary", func(r *store.ReviewRow) { r.ForUsers = "" }, ""},
		{"no plan", func(r *store.ReviewRow) { r.PlanRow = "" }, ""},
		{"no Risk", func(r *store.ReviewRow) { r.Risk = "" }, ""},
	} {
		t.Run(tt.name, func(t *testing.T) {
			row := readyRow()
			tt.change(&row)
			s := memoryWith(t, Decide(row))
			head, refusal, err := TapAt(s, testRepo, bus.Tap{PR: 1, Head: headA[:7]})
			if err != nil || head != headA || refusal != tt.refuses {
				t.Fatalf("TapAt = %q, %q, %v, want %q refused %q", head, refusal, err, headA, tt.refuses)
			}
		})
	}
}

func TestAnApprovalThroughAMissingBodyLineWaitsForItAndNeedsNoNewTap(t *testing.T) {
	row := readyRow()
	row.PlanRow, row.Tapped = "", true
	row = Decide(row)
	if row.ReadyForTap || !strings.Contains(row.WaitsOn, "names no plan") || LandsOnce(row) != "its PR body names its plan" {
		t.Fatalf("row = %+v, lands once %q, want it waiting on the plan line", row, LandsOnce(row))
	}
	row.PlanRow = "plan step 3"
	if row = Decide(row); !row.ReadyForTap || row.WaitsOn != tappedWaits {
		t.Fatalf("row = %+v, want it ready and tapped once the body names a plan", row)
	}
}

func TestHisApprovalAtAHeadTheRowMovedPastStandsThereForTheCarryRule(t *testing.T) {
	row := readyRow()
	row.Head = headB
	s := memoryWith(t, Decide(row))
	head, refusal, err := TapAt(s, testRepo, bus.Tap{PR: 1, Head: headA})
	if err != nil || head != headA || refusal != "" {
		t.Fatalf("TapAt = %q, %q, %v, want it standing at %s", head, refusal, err, headA)
	}
}

func TestAnApprovalOnAPRWithNoRowWaitsForTheFirstFoldOrIsRefusedOnceRowsExist(t *testing.T) {
	for _, tt := range []struct {
		name    string
		rows    []store.ReviewRow
		refusal string
		err     error
	}{
		{"no rows at all", nil, "", ErrRowsNotFolded},
		{"another PR's row", []store.ReviewRow{{Repo: testRepo, Number: 2, Head: headA}}, gates.NoRow, nil},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s, err := store.Memory()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = s.Close() })
			if err := s.PutReviewRows(tt.rows); err != nil {
				t.Fatal(err)
			}
			if _, refusal, err := TapAt(s, testRepo, bus.Tap{PR: 1, Head: headA}); !errors.Is(err, tt.err) || refusal != tt.refusal {
				t.Fatalf("TapAt = %q, %v, want %q, %v", refusal, err, tt.refusal, tt.err)
			}
		})
	}
}

func TestATappedRowStaysReadySoTheGateCanLandItAndIsNotWaitingOnHim(t *testing.T) {
	row := readyRow()
	row.State, row.Tapped = StateTapped, true
	if row = Decide(row); !row.ReadyForTap || row.WaitsOn != tappedWaits {
		t.Fatalf("row = %+v", row)
	}
}

func TestARowWhoseHoldsAllAwaitHisTapStaysReadyAndSaysWhatItWaitsOn(t *testing.T) {
	row := readyRow()
	row.AwaitsTap = "his approval was at an older head (record #7)"
	if row = Decide(row); !row.ReadyForTap || row.WaitsOn != tapEndsHold+"his approval was at an older head (record #7)" {
		t.Fatalf("row = %+v", row)
	}
	row.Hold = "root:the cutover (record #8)"
	if row = Decide(row); row.ReadyForTap || !strings.Contains(row.WaitsOn, "root:the cutover (record #8)") {
		t.Fatalf("an ordinary hold beside it: row = %+v", row)
	}
}

func TestATapFromBeforeAnUntilTapHoldDoesNotCountOnTheRow(t *testing.T) {
	facts := map[string]string{"tap": "tapped"}
	if tapped := Materialize(store.ReviewRow{Number: 1, Head: headA}, facts, Held{}, nil, "dev"); !tapped.Tapped {
		t.Fatalf("no hold: row = %+v", tapped)
	}
	held := Materialize(store.ReviewRow{Number: 1, Head: headA}, facts, Held{AwaitsTap: "asks his tap"}, nil, "dev")
	if held.Tapped || held.Hold != None || held.AwaitsTap != "asks his tap" {
		t.Fatalf("row = %+v", held)
	}
}

func TestAStandingWordLeavesAnUntilTapRowForHisTap(t *testing.T) {
	facts := map[string]string{gates.FlakeFixAttr: "yes", "read-ci": CIGreen, "read-approvers": "a", "read-mergeable": string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityPass)}
	base := func() store.ReviewRow {
		row := readyRow()
		row.Number, row.Head = 1, headA
		return row
	}
	if standing := Materialize(base(), facts, Held{}, nil, "dev"); standing.ReadyForTap {
		t.Fatalf("no hold: row = %+v, want the standing word to take it off his tab", standing)
	}
	if held := Materialize(base(), facts, Held{AwaitsTap: "asks his tap"}, nil, "dev"); !held.ReadyForTap || !strings.Contains(held.WaitsOn, "asks his tap") {
		t.Fatalf("until=tap: row = %+v, want it ready for his tap", held)
	}
}

func TestACarriedTapDoesNotCountOnAnUntilTapRowOrSpeakOnIt(t *testing.T) {
	facts := map[string]string{factCarriedBy: "same-diff", factTapFrom: headB}
	if carried := Materialize(store.ReviewRow{Number: 1, Head: headA}, facts, Held{}, nil, "dev"); !carried.Tapped || !strings.Contains(carried.WaitsOn, "carried to this head") {
		t.Fatalf("no hold: row = %+v", carried)
	}
	held := Materialize(store.ReviewRow{Number: 1, Head: headA}, facts, Held{AwaitsTap: "asks his tap"}, nil, "dev")
	if held.Tapped || strings.Contains(held.WaitsOn, "carried") {
		t.Fatalf("row = %+v", held)
	}
}

func foldFor(t *testing.T) (Fold, *store.Store) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return Fold{Drive: "d", Repo: testRepo, Trunk: "dev", Store: s, Now: time.Now}, s
}

func postOpened(t *testing.T, number int, head, base, body string) {
	t.Helper()
	encoded, err := json.Marshal(map[string]string{"body": body})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "lane-7", Kind: "opened", Text: "opened", PRs: []string{fmt.Sprintf("%s#%d", testRepo, number)},
		Attrs: map[string]string{"repo": testRepo, "branch": fmt.Sprintf("branch-%d", number), "base": base, "head": head}, Body: encoded,
	}); err != nil {
		t.Fatal(err)
	}
}

func postHead(t *testing.T, number int, head string) {
	t.Helper()
	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "lane-7", Kind: "head", Text: fmt.Sprintf("HEAD #%d %s", number, head), PRs: []string{fmt.Sprintf("%s#%d", testRepo, number)},
		ID: fmt.Sprintf("head/%d/%s", number, head),
	}); err != nil {
		t.Fatal(err)
	}
}

func cleanRead(number int, head string) machine.Facts {
	return machine.Facts{Number: number, Head: head, Title: "the title", Branch: fmt.Sprintf("branch-%d", number), Base: "dev", CI: machine.CISuccess, Approved: true, Approvers: []string{"a", "b"}, Mergeable: machine.MergeableClean, Queue: machine.QueueNone, ReadAt: time.Now()}
}

func folded(t *testing.T, f Fold, number int) store.ReviewRow {
	t.Helper()
	rows, err := f.Pass(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range rows {
		if row.Number == number {
			return row
		}
	}
	t.Fatalf("no row for #%d in %+v", number, rows)
	return store.ReviewRow{}
}

func gradeClear(t *testing.T, s *store.Store, number int, head string) {
	t.Helper()
	for kind, value := range map[string]string{"finder": "clear", "fidelity": "pass"} {
		if _, err := s.PutFact(testRepo, number, head, kind, value, time.Now()); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAnOpenedRecordReopensAClosedRowAndThePRAboveSeesItsParentOpen(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	postOpened(t, 2, headB, "branch-1", fullBody)
	folded(t, f, 2)
	closed := cleanRead(1, headA)
	closed.Closed = true
	if err := f.PostReads(context.Background(), []machine.Facts{closed}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 2); row.ParentState != ParentClosed {
		t.Fatalf("after the close: #2 = %+v, want its parent closed", row)
	}

	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "lane-7", Kind: "opened", Text: "reopened #1", PRs: []string{testRepo + "#1"},
		Attrs: map[string]string{"repo": testRepo, "head": headA},
	}); err != nil {
		t.Fatal(err)
	}

	rows, err := f.Pass(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	byNumber := map[int]store.ReviewRow{}
	for _, row := range rows {
		byNumber[row.Number] = row
	}
	if got := byNumber[1].State; got != StateOpen {
		t.Fatalf("#1 state = %q after its opened record, want open", got)
	}
	if got := byNumber[2]; got.Parent != "#1" || got.ParentState != ParentOpen {
		t.Fatalf("#2 = %+v, want its parent #1 open", got)
	}
}

func TestAKnownMergeabilitySurvivesGitHubsRecomputeThenGoesUnknown(t *testing.T) {
	f, s := foldFor(t)
	now := time.Now()
	f.Now = func() time.Time { return now }
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	gradeClear(t, s, 1, headA)
	read := func(mergeable machine.Mergeable) store.ReviewRow {
		t.Helper()
		now = now.Add(time.Second)
		pr := cleanRead(1, headA)
		pr.Mergeable = mergeable
		if err := f.PostReads(context.Background(), []machine.Facts{pr}); err != nil {
			t.Fatal(err)
		}
		return folded(t, f, 1)
	}

	read(machine.MergeableClean)
	if row := read(machine.MergeableUnknown); !row.ReadyForTap || row.Mergeable != "clean" {
		t.Fatalf("during the recompute: row = %+v, want still ready", row)
	}
	for range 5 {
		now = now.Add(5 * time.Minute)
		if row := read(machine.MergeableUnknown); !row.ReadyForTap || row.Mergeable != "clean" {
			t.Fatalf("dev still moving: row = %+v, want its clean kept until GitHub answers", row)
		}
	}
	if row := read(machine.MergeableClean); !row.ReadyForTap {
		t.Fatalf("after the answer: row = %+v, want ready", row)
	}
	if row := read(machine.MergeableUnknown); !row.ReadyForTap || row.Mergeable != "clean" {
		t.Fatalf("a later recompute: row = %+v, want clean kept", row)
	}
}

func TestADevMoveHoldsReadyRowsFromATapUntilAHostReadsAgainAndItsConflictsStick(t *testing.T) {
	f, s := foldFor(t)
	dev := strings.Repeat("d", 40)
	for number, head := range map[int]string{1: headA, 2: headB} {
		postOpened(t, number, head, "dev", fullBody)
		folded(t, f, number)
		gradeClear(t, s, number, head)
		if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(number, head)}); err != nil {
			t.Fatal(err)
		}
		if row := folded(t, f, number); !row.ReadyForTap {
			t.Fatalf("before dev moves: #%d = %+v, want ready", number, row)
		}
	}
	for _, branch := range []string{"lane/fix", "dev"} {
		var push client.Record
		raw := fmt.Sprintf(`{"seq":1,"drive":"github-pr","lane":"github","kind":"github","at":%q,"topic":"push","fields":{"outcome":"pushed","commit":%q,"attrs":{"repo":%q,"branch":%q}}}`,
			time.Now().Format(time.RFC3339Nano), dev, testRepo, branch)
		if err := json.Unmarshal([]byte(raw), &push); err != nil {
			t.Fatal(err)
		}
		if err := s.Update(func(tx *store.Tx) error { return f.follow(tx, map[int]store.ReviewRow{}, push) }); err != nil {
			t.Fatal(err)
		}
	}

	row := folded(t, f, 1)
	if row.ReadyForTap || row.Mergeable != "clean" || !strings.Contains(row.WaitsOn, "dev moved to "+shortHead(dev)) {
		t.Fatalf("after dev moves: row = %+v, want its clean kept and no tap until a read", row)
	}
	unread, err := f.UnreadTrunk()
	if err != nil || unread != dev {
		t.Fatalf("unread trunk = %q, %v, want %s", unread, err, dev)
	}
	if err := f.PostTrunkRead(context.Background(), unread, []string{"2@" + headB}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); !row.ReadyForTap {
		t.Fatalf("after a host reads: #1 = %+v, want ready", row)
	}
	if row := folded(t, f, 2); row.ReadyForTap || row.Mergeable != string(machine.MergeableConflict) {
		t.Fatalf("after a host reads: #2 = %+v, want the conflict it named", row)
	}
	if unread, err := f.UnreadTrunk(); err != nil || unread != "" {
		t.Fatalf("unread trunk after the read = %q, %v, want none", unread, err)
	}
}

func TestAConflictAnswerReplacesAKeptClean(t *testing.T) {
	f, s := foldFor(t)
	now := time.Now()
	f.Now = func() time.Time { return now }
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	gradeClear(t, s, 1, headA)
	read := func(mergeable machine.Mergeable) store.ReviewRow {
		t.Helper()
		now = now.Add(time.Second)
		pr := cleanRead(1, headA)
		pr.Mergeable = mergeable
		if err := f.PostReads(context.Background(), []machine.Facts{pr}); err != nil {
			t.Fatal(err)
		}
		return folded(t, f, 1)
	}

	read(machine.MergeableClean)
	if row := read(machine.MergeableUnknown); !row.ReadyForTap || row.Mergeable != "clean" {
		t.Fatalf("during the recompute: row = %+v, want clean kept", row)
	}
	if row := read(machine.MergeableConflict); row.ReadyForTap || row.Mergeable != "conflict" {
		t.Fatalf("after the conflict answer: row = %+v, want conflict and not ready", row)
	}
	if row := read(machine.MergeableUnknown); row.ReadyForTap || row.Mergeable != "conflict" {
		t.Fatalf("recomputing after the conflict: row = %+v, want the conflict kept", row)
	}
}

func TestANewHeadKeepsNoAnswerFromTheHeadBefore(t *testing.T) {
	f, s := foldFor(t)
	now := time.Now()
	f.Now = func() time.Time { return now }
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	gradeClear(t, s, 1, headA)
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
		t.Fatal(err)
	}
	folded(t, f, 1)
	now = now.Add(time.Second)
	moved := cleanRead(1, headB)
	moved.Mergeable = machine.MergeableUnknown
	if err := f.PostReads(context.Background(), []machine.Facts{moved}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.Mergeable != "unknown" || row.ReadyForTap {
		t.Fatalf("row = %+v, want a new head unknown until GitHub answers for it", row)
	}
}

func TestUnknownSinceDatesAHeadGitHubNeverAnsweredAndAnAnswerEndsTheEpisode(t *testing.T) {
	f, s := foldFor(t)
	began := time.Now()
	now := began
	f.Now = func() time.Time { return now }
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	read := func(mergeable machine.Mergeable) map[string]string {
		t.Helper()
		pr := cleanRead(1, headA)
		pr.Mergeable = mergeable
		if err := f.PostReads(context.Background(), []machine.Facts{pr}); err != nil {
			t.Fatal(err)
		}
		folded(t, f, 1)
		facts, err := s.Facts(testRepo, 1, headA)
		if err != nil {
			t.Fatal(err)
		}
		return facts
	}

	want := began.Format(time.RFC3339)
	if got := read(machine.MergeableUnknown)[FactUnknownSince]; got != want {
		t.Fatalf("unknown-since = %q, want %q on a head with no known value", got, want)
	}
	now = began.Add(time.Hour)
	if got := read(machine.MergeableUnknown)[FactUnknownSince]; got != want {
		t.Fatalf("unknown-since = %q, want the first unknown read's %q kept", got, want)
	}
	if _, err := s.PutFact(testRepo, 1, headA, FactBumps, "3", now); err != nil {
		t.Fatal(err)
	}
	if facts := read(machine.MergeableClean); facts[FactUnknownSince] != "" || facts[FactBumps] != "" {
		t.Fatalf("facts = %v, want an answer to clear the date and the count", facts)
	}
}

func TestARowFromTheRecordsCarriesEveryFieldAndComesReady(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	gradeClear(t, s, 1, headA)
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
		t.Fatal(err)
	}

	row := folded(t, f, 1)

	if !row.ReadyForTap || row.WaitsOn != onlyHim {
		t.Fatalf("row = %+v, want ready", row)
	}
	want := store.ReviewRow{
		Repo: testRepo, Number: 1, Title: "the title", Lane: "lane", Branch: "branch-1", Base: "dev", Parent: None, ParentState: None, Head: headA,
		ForUsers: "A user sees it.", PlanLine: "This carries plan step 1.", PlanRow: "This carries plan step 1. " + planChecked, Risk: "Low.",
		CI: CIGreen, Approvers: "a, b", Review: "approved by a, b", Grade: GradePass, GradeFindings: None, Mergeable: "clean", Queue: "none",
		Hold: None, SupersededBy: None, WaitsOn: onlyHim, ReadyForTap: true, State: StateOpen, TapRefused: None, TappedAt: NotYet, TappedBy: NotYet, LandedAt: NotYet, Port: None,
	}
	got := row
	got.HeadAt, got.ReadHead, got.Description, got.LastRecord, got.WrittenAt, got.MovedAt, got.WordsAt = "", "", "", 0, "", "", ""
	if got != want {
		t.Fatalf("row =\n%+v\nwant\n%+v", got, want)
	}
	if row.Description == "" || row.WrittenAt == "" || row.MovedAt != row.WrittenAt || row.LastRecord == 0 || row.ReadHead != headA || row.WordsAt == "" {
		t.Fatalf("row = %+v, want description, written_at, moved_at at written_at, last_record, read_head and words_at", row)
	}
}

func TestAReadFillsTheCardFromGitHubsBodyAndCarriesItOnlyWhenItChanges(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", "")
	folded(t, f, 1)
	gradeClear(t, s, 1, headA)
	pass := func(pr machine.Facts) store.ReviewRow {
		t.Helper()
		if err := f.PostReads(context.Background(), []machine.Facts{pr}); err != nil {
			t.Fatal(err)
		}
		return folded(t, f, 1)
	}
	counts := func() (reads, bodies int) {
		t.Helper()
		found, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{readKind}})
		if err != nil {
			t.Fatal(err)
		}
		for _, record := range found {
			if len(record.Body) > 0 {
				bodies++
			}
		}
		return len(found), bodies
	}

	read := cleanRead(1, headA)
	read.Body = fullBody
	row := pass(read)
	if !row.ReadyForTap || row.ForUsers != "A user sees it." {
		t.Fatalf("row = %+v, want its card from the read and ready", row)
	}
	pass(read)
	if reads, bodies := counts(); reads != 1 || bodies != 1 {
		t.Fatalf("reads = %d, bodies = %d after a repeat, want 1 and 1", reads, bodies)
	}

	red := read
	red.CI, red.Failing = machine.CIFailure, []string{"lint"}
	pass(red)
	if reads, bodies := counts(); reads != 2 || bodies != 1 {
		t.Fatalf("reads = %d, bodies = %d after a status change, want 2 and 1", reads, bodies)
	}

	edited := read
	edited.Body = strings.Replace(fullBody, "A user sees it.", "A user sees more.", 1)
	row = pass(edited)
	if reads, bodies := counts(); reads != 3 || bodies != 2 || row.ForUsers != "A user sees more." {
		t.Fatalf("reads = %d, bodies = %d, for users = %q after an edit", reads, bodies, row.ForUsers)
	}
}

func TestARowWhoseLaneIsParkedOnAnotherPRReadsThatItWaitsOnThatPR(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	postOpened(t, 2, headB, "dev", fullBody)
	folded(t, f, 1)
	red := cleanRead(1, headA)
	red.CI = machine.CIFailure
	if err := f.PostReads(context.Background(), []machine.Facts{red, cleanRead(2, headB)}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.WaitsOn != RedAtHead {
		t.Fatalf("before the park: waits on %q, want %q", row.WaitsOn, RedAtHead)
	}
	const lane = "signal-storage-retro-3"
	if _, err := s.WantLane(store.Lane{Name: lane, Brief: "/briefs/x.md", State: store.LaneDesired, PRs: []int{1}}, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := s.SetLaneWaiting(lane, 9, time.Time{}, "pr:1 pr:2"); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.WaitsOn != "waits on #2" || row.ReadyForTap {
		t.Fatalf("parked on #2: row = %+v", row)
	}
	if row := folded(t, f, 2); strings.HasPrefix(row.WaitsOn, "waits on") {
		t.Fatalf("the PR it waits on reads %q", row.WaitsOn)
	}
	if err := s.SetLaneWaiting(lane, 0, time.Now(), ""); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.WaitsOn != RedAtHead {
		t.Fatalf("after the wait ends: waits on %q", row.WaitsOn)
	}
}

func TestANewHeadClearsCIReviewGradeAndMergeableAndKeepsTheBody(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	gradeClear(t, s, 1, headA)
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); !row.ReadyForTap {
		t.Fatalf("row before the push = %+v", row)
	}

	postHead(t, 1, headB)
	row := folded(t, f, 1)

	if row.Head != headB || row.ReadyForTap {
		t.Fatalf("row = %+v, want not ready at the new head", row)
	}
	if row.CI != CIPending || row.Approvers != None || row.Grade != GradePending || row.Mergeable != string(machine.MergeableUnknown) {
		t.Fatalf("row = %+v, want ci, review, grade and mergeable cleared", row)
	}
	if row.ForUsers != "A user sees it." {
		t.Fatalf("for_users = %q, want the body kept when a head record carries none", row.ForUsers)
	}
}

func TestAStaleGitHubReadNeverUndoesAPushAndAnUnseenHeadMovesTheRow(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	postHead(t, 1, headB)
	folded(t, f, 1)

	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
		t.Fatal(err)
	}
	row := folded(t, f, 1)
	if row.Head != headB || row.ReadHead != headA || row.CI != CIPending {
		t.Fatalf("row = %+v, want the push's head kept, with the stale read's values not on it", row)
	}

	unseen := strings.Repeat("c", 40)
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, unseen)}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.Head != unseen || row.CI != CIGreen {
		t.Fatalf("row = %+v, want a head no record named to move the row", row)
	}
}

func TestAReadCorrectsARowAStaleHeadRecordMovedBackOnceTheGraceEnds(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	pass := func() store.ReviewRow {
		t.Helper()
		if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headB)}); err != nil {
			t.Fatal(err)
		}
		return folded(t, f, 1)
	}
	if row := pass(); row.Head != headB {
		t.Fatalf("row = %+v, want the read's new head", row)
	}
	postHead(t, 1, headA)
	if row := folded(t, f, 1); row.Head != headA {
		t.Fatalf("row = %+v, want the head record to move it back", row)
	}

	row := pass()
	if row.Head != headA {
		t.Fatalf("row = %+v, want a read inside the grace to leave a fresh push alone", row)
	}

	row.HeadAt = store.Stamp(time.Now().Add(-headGrace - time.Minute))
	if err := s.PutReviewRows([]store.ReviewRow{row}); err != nil {
		t.Fatal(err)
	}
	if row := pass(); row.Head != headB {
		t.Fatalf("row = %+v, want GitHub's head once the grace ended", row)
	}
	reads, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{readKind}})
	if err != nil {
		t.Fatal(err)
	}
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headB)}); err != nil {
		t.Fatal(err)
	}
	again, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{readKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(again) != len(reads) {
		t.Fatalf("reads went from %d to %d once the row matched GitHub", len(reads), len(again))
	}
}

func TestAShortHeadRecordAfterTheReadKeepsTheFullShaItNames(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headB)}); err != nil {
		t.Fatal(err)
	}
	folded(t, f, 1)

	postHead(t, 1, headB[:7])

	if row := folded(t, f, 1); row.Head != headB {
		t.Fatalf("head = %q, want the read's full %s", row.Head, headB)
	}
}

func TestAShortHeadTheFoldCannotExpandLeavesTheRowsHeadAloneAndAsksForARead(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)

	postHead(t, 1, headB[:12])

	if row := folded(t, f, 1); row.Head != headA {
		t.Fatalf("head = %q, want the row to stay at %s", row.Head, headA)
	}
	asked, err := s.ReadAsks(testRepo)
	if err != nil || !slices.Equal(asked, []int{1}) {
		t.Fatalf("asks = %v, %v, want one read asked for #1", asked, err)
	}

	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headB)}); err != nil {
		t.Fatal(err)
	}

	if row := folded(t, f, 1); row.Head != headB {
		t.Fatalf("head = %q, want the read's full %s", row.Head, headB)
	}
}

func TestAnOpenedRecordWithAShortHeadOpensARowWithNoHead(t *testing.T) {
	f, s := foldFor(t)

	postOpened(t, 1, headA[:7], "dev", fullBody)

	if row := folded(t, f, 1); row.Head != "" {
		t.Fatalf("head = %q, want none until a read names the full sha", row.Head)
	}
	asked, err := s.ReadAsks(testRepo)
	if err != nil || !slices.Equal(asked, []int{1}) {
		t.Fatalf("asks = %v, %v, want one read asked for #1", asked, err)
	}
}

func TestARowHoldingAShortHeadHealsFromTheNextRead(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
		t.Fatal(err)
	}
	row := folded(t, f, 1)
	row.Head = headA[:7]
	if err := s.PutReviewRows([]store.ReviewRow{row}); err != nil {
		t.Fatal(err)
	}

	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
		t.Fatal(err)
	}

	if row := folded(t, f, 1); row.Head != headA || row.CI != CIGreen {
		t.Fatalf("row = %+v, want the full head read back with its values unchanged", row)
	}
}

func TestARowNamesTheLaneThatLastPushedItByAlias(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	if row := folded(t, f, 1); row.Lane != "lane" {
		t.Fatalf("lane = %q, want the opener's alias", row.Lane)
	}
	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "run-search-shape-2", Kind: "head", Text: "HEAD #1 " + headB, PRs: []string{fmt.Sprintf("%s#%d", testRepo, 1)},
		ID: "head/1/successor",
	}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.Lane != "run-search-shape" {
		t.Fatalf("lane = %q, want the lane that pushed last", row.Lane)
	}
}

func TestASameHeadRecordFromAnotherLaneLeavesTheOwnerAndANewShaTakesIt(t *testing.T) {
	f, _ := foldFor(t)
	post := func(t *testing.T, draft client.Draft) {
		t.Helper()
		draft.Drive, draft.PRs = "d", []string{testRepo + "#1"}
		if _, err := client.Post(context.Background(), draft); err != nil {
			t.Fatal(err)
		}
	}
	post(t, client.Draft{Lane: "next-one-kind-of-chat-1", Kind: "opened", Text: "opened", Attrs: map[string]string{"repo": testRepo, "branch": "b", "base": "dev", "head": headA}})
	for _, step := range []struct {
		lane, head, owner, why string
	}{
		{"progress-desk-4", headA, "next-one-kind-of-chat", "a head record at the same sha, naming a pick"},
		{"next-one-kind-of-chat-2", headA, "next-one-kind-of-chat", "its relaunched successor at the same sha"},
		{"takeover-1", headB, "takeover", "another lane's push to a new sha"},
	} {
		post(t, client.Draft{Lane: step.lane, Kind: "head", Text: "HEAD #1 " + step.head})
		if row := folded(t, f, 1); row.Lane != step.owner || row.Head != step.head {
			t.Fatalf("after %s: lane %q at %s, want %q at %s", step.why, row.Lane, row.Head, step.owner, step.head)
		}
	}
}

func TestARowNamesTheLaneTheRegistryOwnsItByAfterAnotherLanePushedIt(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	if err := s.Own("dd-1-fix", []int{1}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: bus.SweepLane, Kind: "head", Text: "HEAD #1 " + headB + ": restacked onto dev by remote-restack", PRs: []string{fmt.Sprintf("%s#%d", testRepo, 1)},
		ID: "head/1/restack",
	}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.Lane != "dd-1-fix" || row.Head != headB {
		t.Fatalf("row = %+v, want the new head under the lane the registry owns it by", row)
	}
}

func TestAReadPostsOnlyWhenAValueChanged(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	reads := func() int {
		t.Helper()
		found, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{readKind}})
		if err != nil {
			t.Fatal(err)
		}
		return len(found)
	}
	pass := func(pr machine.Facts) {
		t.Helper()
		if err := f.PostReads(context.Background(), []machine.Facts{pr}); err != nil {
			t.Fatal(err)
		}
		folded(t, f, 1)
	}

	pass(cleanRead(1, headA))
	pass(cleanRead(1, headA))
	if got := reads(); got != 1 {
		t.Fatalf("%d reads for one value, want 1", got)
	}
	found, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{readKind}})
	if err != nil {
		t.Fatal(err)
	}
	if got := found[0].To; len(got) != 1 || got[0] != readReader {
		t.Fatalf("a read's address = %v, want only %s, because an unaddressed record reaches every lane's reader", got, readReader)
	}
	red := cleanRead(1, headA)
	red.CI, red.Failing = machine.CIFailure, []string{"lint"}
	pass(red)
	pass(cleanRead(1, headA))
	if got := reads(); got != 3 {
		t.Fatalf("%d reads after green, red, green; want 3, so a value that returns is posted again", got)
	}
}

func TestAPRWithNoOpenedRecordHasNoRowAndNoReadPosts(t *testing.T) {
	f, _ := foldFor(t)
	postHead(t, 1, headA)
	if rows, err := f.Pass(context.Background(), nil); err != nil || len(rows) != 0 {
		t.Fatalf("rows = %+v, %v, want a head record to make no row", rows, err)
	}
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
		t.Fatal(err)
	}
	found, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{readKind}})
	if err != nil || len(found) != 0 {
		t.Fatalf("reads = %+v, %v", found, err)
	}
}

func TestAChildsParentIsTheOpenPROnItsBaseBranchNotAClosedOneThatUsedTheBranchBefore(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	closed := cleanRead(1, headA)
	closed.Closed = true
	if err := f.PostReads(context.Background(), []machine.Facts{closed}); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "lane-8", Kind: "opened", Text: "opened", PRs: []string{testRepo + "#3"},
		Attrs: map[string]string{"repo": testRepo, "branch": "branch-1", "base": "dev", "head": strings.Repeat("c", 40)},
	}); err != nil {
		t.Fatal(err)
	}
	postOpened(t, 2, headB, "branch-1", fullBody)
	for range 16 {
		if row := folded(t, f, 2); row.Parent != "#3" || row.ParentState != ParentOpen {
			t.Fatalf("child = %+v, want it waiting on the open #3, which reuses closed #1's branch", row)
		}
	}
}

func TestAChildsParentFollowsARestackThatMovesItsGitHubBase(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	postOpened(t, 3, strings.Repeat("c", 40), "dev", fullBody)
	postOpened(t, 2, headB, "branch-1", fullBody)
	if row := folded(t, f, 2); row.Parent != "#1" {
		t.Fatalf("child before the restack = %+v, want it on #1", row)
	}
	restacked := cleanRead(2, headB)
	restacked.Base = "branch-3"
	if err := f.PostReads(context.Background(), []machine.Facts{restacked}); err != nil {
		t.Fatal(err)
	}

	if row := folded(t, f, 2); row.Parent != "#3" || row.ParentState != ParentOpen {
		t.Fatalf("child after the restack = %+v, want it waiting on #3, its new GitHub base", row)
	}
}

func TestAChildIsNotReadyBeforeItsParentLands(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	postOpened(t, 2, headB, "branch-1", fullBody)
	folded(t, f, 2)
	gradeClear(t, s, 2, headB)
	child := cleanRead(2, headB)
	child.Base = "branch-1"
	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA), child}); err != nil {
		t.Fatal(err)
	}

	if row := folded(t, f, 2); row.ReadyForTap || row.ParentState != ParentOpen || row.Parent != "#1" {
		t.Fatalf("child = %+v, want it waiting on #1", row)
	}

	landed := cleanRead(1, headA)
	landed.Merged, landed.Closed = true, true
	if err := f.PostReads(context.Background(), []machine.Facts{landed}); err != nil {
		t.Fatal(err)
	}
	row := folded(t, f, 2)
	if !row.ReadyForTap || row.ParentState != ParentLanded {
		t.Fatalf("child = %+v, want ready once #1 landed", row)
	}
	parent := folded(t, f, 1)
	if parent.State != StateLanded || parent.LandedAt == NotYet {
		t.Fatalf("parent = %+v, want landed with its time", parent)
	}
}

func TestALandedRecordLandsAQueuedRowAndAStaleReadLeavesItLanded(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	queued := cleanRead(1, headA)
	queued.Queue, queued.QueuedHead = machine.QueueQueued, headA
	if err := f.PostReads(context.Background(), []machine.Facts{queued}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.State != StateQueued {
		t.Fatalf("row = %+v, want queued", row)
	}

	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: landingDesk, Kind: "landed", Text: "merged at " + headB, PRs: []string{testRepo + "#1"}}); err != nil {
		t.Fatal(err)
	}
	row := folded(t, f, 1)
	if row.State != StateLanded || row.LandedAt == NotYet || row.Queue != string(machine.QueueNone) {
		t.Fatalf("row = %+v, want landed with its time and out of the queue", row)
	}

	queued.CI = machine.CIPending
	if err := f.PostReads(context.Background(), []machine.Facts{queued}); err != nil {
		t.Fatal(err)
	}
	if stale := folded(t, f, 1); stale.State != StateLanded || stale.LandedAt != row.LandedAt || stale.Queue != string(machine.QueueNone) {
		t.Fatalf("row = %+v, want a read taken before the merge to leave it landed at %s", stale, row.LandedAt)
	}
}

func TestALandedRecordLandsARowOnlyFromTheLandingDesk(t *testing.T) {
	for lane, want := range map[string]string{"lane-7": StateOpen, landingDesk: StateLanded, "host-tap-fold": StateOpen} {
		t.Run(lane, func(t *testing.T) {
			f, _ := foldFor(t)
			postOpened(t, 1, headA, "dev", fullBody)
			folded(t, f, 1)

			if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: "landed", Text: "yasyf/cc-skills#3409 merged", PRs: []string{"yasyf/cc-skills#3409", "1"}}); err != nil {
				t.Fatal(err)
			}

			if row := folded(t, f, 1); row.State != want {
				t.Fatalf("row = %+v, want %s after %s's landed record", row, want, lane)
			}
		})
	}
}

func TestTheOwningLanesLandedRecordCitingItsOpenPRLeavesTheRowOpen(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)

	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "lane-7", Kind: "landed",
		Text: "monorepo#1 + Codex hook cwd: cc-remote v0.27.25 released. Monorepo pin PR #1 is stacked on #51750 and lands on his tap.",
		PRs:  []string{testRepo + "#1"},
	}); err != nil {
		t.Fatal(err)
	}

	if row := folded(t, f, 1); row.State != StateOpen || row.LandedAt != NotYet || row.Lane != "lane" {
		t.Fatalf("row = %+v, want it open and still lane-7's", row)
	}
}

func TestAnOpenReadReopensARowALaneRecordLandedAndAReadAtTheLandingLeavesIt(t *testing.T) {
	for name, landedAgo := range map[string]time.Duration{"an hour after the landing": time.Hour, "at the landing": 0} {
		t.Run(name, func(t *testing.T) {
			f, s := foldFor(t)
			postOpened(t, 1, headA, "dev", fullBody)
			folded(t, f, 1)
			if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
				t.Fatal(err)
			}
			row := folded(t, f, 1)
			row.State, row.LandedAt = StateLanded, store.Stamp(time.Now().Add(-landedAgo))
			if err := s.PutReviewRows([]store.ReviewRow{row}); err != nil {
				t.Fatal(err)
			}

			if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
				t.Fatal(err)
			}

			want := StateOpen
			if landedAgo < headGrace {
				want = StateLanded
			}
			if got := folded(t, f, 1); got.State != want {
				t.Fatalf("row = %+v, want %s after an open read %s", got, want, name)
			}
		})
	}
}

func TestARowALandedRecordFromAnyLaneButTheLandingDeskClosedOutReopensOnTheFirstFold(t *testing.T) {
	f, s := foldFor(t)
	cited, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "host-tap-fold", Kind: "landed", Text: "yasyf/cc-skills#3409 merged", PRs: []string{"yasyf/cc-skills#3409", "1"}})
	if err != nil {
		t.Fatal(err)
	}
	owned, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "landed", Text: "merged", PRs: []string{testRepo + "#2"}})
	if err != nil {
		t.Fatal(err)
	}
	desk, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: landingDesk, Kind: "landed", Text: "LANDED #3", PRs: []string{testRepo + "#3"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := s.PutReviewRows([]store.ReviewRow{
		{Repo: testRepo, Number: 1, Head: headA, Lane: "lane", Base: "dev", State: StateLanded, LandedAt: store.Stamp(cited.At)},
		{Repo: testRepo, Number: 2, Head: headB, Lane: "lane", Base: "dev", State: StateLanded, LandedAt: store.Stamp(owned.At)},
		{Repo: testRepo, Number: 3, Head: headA, Lane: "lane", Base: "dev", State: StateLanded, LandedAt: store.Stamp(desk.At)},
	}); err != nil {
		t.Fatal(err)
	}

	for _, number := range []int{1, 2} {
		if row := folded(t, f, number); row.State != StateOpen || row.LandedAt != NotYet {
			t.Fatalf("#%d = %+v, want it open again", number, row)
		}
	}
	if row := folded(t, f, 3); row.State != StateLanded || row.LandedAt != store.Stamp(desk.At) {
		t.Fatalf("#3 = %+v, want the landing desk's landing to stand", row)
	}
}

func TestASupersededPRLeavesAndANewHeadBringsItBack(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	folded(t, f, 1)
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "duplicate", Text: "replaced", PRs: []string{testRepo + "#1"}, Attrs: map[string]string{"superseded_by": "9"}}); err != nil {
		t.Fatal(err)
	}
	if row := folded(t, f, 1); row.State != StateSuperseded || row.SupersededBy != "replaced by #9" || row.ReadyForTap {
		t.Fatalf("row = %+v", row)
	}
	postHead(t, 1, headB)
	if row := folded(t, f, 1); row.State != StateOpen || row.SupersededBy != None {
		t.Fatalf("row = %+v, want a new head to bring it back", row)
	}
}

func TestAClaimMakesARowThatTheReadFillsAndThatStaysNotReadyWithNoBody(t *testing.T) {
	f, s := foldFor(t)
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "opened", Text: "opened #o/r#1", PRs: []string{testRepo + "#1"}}); err != nil {
		t.Fatal(err)
	}
	row := folded(t, f, 1)
	if row.Head != "" || row.ReadyForTap || row.Lane != "lane" || row.WaitsOn != "no record names its head yet" {
		t.Fatalf("row = %+v, want a row at once, under its lane, saying no head is known", row)
	}

	if err := f.PostReads(context.Background(), []machine.Facts{cleanRead(1, headA)}); err != nil {
		t.Fatal(err)
	}
	gradeClear(t, s, 1, headA)
	row = folded(t, f, 1)

	if row.Head != headA || row.Base != "dev" || row.State != StateOpen || row.CI != CIGreen {
		t.Fatalf("row = %+v, want head, base and state from the read", row)
	}
	if row.ReadyForTap || row.WaitsOn != "its PR body has no Summary" {
		t.Fatalf("row = %+v, want it not ready, saying its body is missing", row)
	}
}

func TestForUsersRiskAndDescriptionAreStoredWhole(t *testing.T) {
	long := strings.Repeat("word ", 1000) + "end"
	body := "## Summary\n\n" + long + "\n\n## Risk profile\n\n" + long + "\n"
	var row store.ReviewRow
	ApplyBody(&row, body)
	if row.ForUsers != long || row.Risk != long || row.Description != strings.TrimSpace(body) {
		t.Fatalf("for_users %d, risk %d and description %d characters, want %d, %d and %d", len(row.ForUsers), len(row.Risk), len(row.Description), len(long), len(long), len(strings.TrimSpace(body)))
	}
}

func TestAPlanLineIsNeverReadFromTheRiskProfile(t *testing.T) {
	var row store.ReviewRow
	ApplyBody(&row, "## Summary\n\nFor a user.\n\n## Risk profile\n\n- **Threat model:** plan 12 changes nothing.\n\n## Design\n\nNo reference here.\n")
	if row.PlanLine != "" || !strings.Contains(row.Risk, "Threat model") {
		t.Fatalf("row = %+v, want the risk kept and no plan line taken from it", row)
	}
}

func TestAPlanLineIsAnyFormALaneWritesItIn(t *testing.T) {
	const incident = "Plan: replace timeout-only ticket mint options with one key per logical invocation and the existing timeout, verify recovery at ticket populations of 1 and 1,000, then release the merged fix and observe AIG smoke. The ingress client recovers the same invocation after losing its response. Plan and acceptance record: cc-notes task `ed1b77de02898e932acb9d8cb5a8f7dc1656d321`, implementing the smoke-flake-fix-1 incident brief (owner:m835)."
	for name, tc := range map[string]struct{ body, want string }{
		"an incident brief": {"## Summary\n\n" + incident + "\n\n## Risk profile\n\nLow.\n", incident},
		"a bulleted plan":   {"## Summary\n\nFor a user.\n\n- **Plan:** carries the sweep.\n", "- **Plan:** carries the sweep."},
		"an owner order":    {"## Summary\n\nFor a user.\n\nCarries owner:m31, no timestamps in records.\n", "Carries owner:m31, no timestamps in records."},
		"a task id":         {"## Summary\n\nFor a user.\n\nRecorded in cc-notes task ed1b77de0289.\n", "Recorded in cc-notes task ed1b77de0289."},
		"a plan step":       {"## Summary\n\nFor a user.\n\nThis carries plan step 1.\n", "This carries plan step 1."},
		"the planner":       {"## Summary\n\nFor a user.\n\n## Design\n\nThe planner's choice is not guaranteed, so the old plan keeps serving.\n", ""},
		"a mid-line Plan:":  {"## Summary\n\nFor a user.\n\nThe Plan: is elsewhere.\n", ""},
		"an owner: no row":  {"## Summary\n\nFor a user.\n\nThe owner: asked nothing.\n", ""},
		"no line":           {"## Summary\n\nFor a user.\n", ""},
	} {
		t.Run(name, func(t *testing.T) {
			var row store.ReviewRow
			ApplyBody(&row, tc.body)
			if row.PlanLine != clip(tc.want, fieldChars) {
				t.Fatalf("plan line = %q, want %q", row.PlanLine, clip(tc.want, fieldChars))
			}
		})
	}
}

func TestAPlanLineNamesAPlanStepAnOwnerOrderOrATask(t *testing.T) {
	for line, refused := range map[string]bool{
		"Plan: step 3.":                  false,
		"This carries plan step 1.":      false,
		"Plan: owner:m1793.":             false,
		"Plan: cc-notes task 5221024.":   false,
		"Plan: the gate reads the body.": true,
		"- **Plan:** carries the sweep.": true,
		"Plan: rule/d7b507a.":            true,
		"":                               true,
	} {
		if flaw := PlanFlaw(line); (flaw != "") != refused || refused && !strings.Contains(flaw, PlanForms) {
			t.Errorf("PlanFlaw(%q) = %q, want refused %v naming the accepted forms", line, flaw, refused)
		}
	}
}

func TestTheBodyParserReadsEachField(t *testing.T) {
	var row store.ReviewRow
	ApplyBody(&row, "## Context\n\nWhy.\n\n## Summary\n\nFor a user.\n\n## Risk profile\n\nNone.\n\n## Design\n\nRow 12 of plan step 3 applies.\n\n## Changes\n\nThis goes live only after #1 is live.\n")
	if row.ForUsers != "For a user." || row.Risk != "None." || row.PlanLine != "Row 12 of plan step 3 applies." || !strings.HasPrefix(row.Description, "## Context") {
		t.Fatalf("row = %+v", row)
	}
	if row.UnsafeWait == "" {
		t.Fatalf("row = %+v, want the body's unsafe wait", row)
	}
}

func TestAPlanRowIsTheBodysLineOrTheFidelityPass(t *testing.T) {
	if got := planRow("", machine.FidelityPass, ""); got != planChecked {
		t.Fatalf("no line with a pass = %q", got)
	}
	if got := planRow("", machine.FidelityFail, ""); got != "" {
		t.Fatalf("no line with a fail = %q, want empty so the row is not ready", got)
	}
	if got := planRow("plan step 1", "", ""); got != "plan step 1" {
		t.Fatalf("line ungraded = %q", got)
	}
	if got := planRow("plan step 1", machine.FidelityFail, "443549"); got != "plan step 1 "+planOverridden {
		t.Fatalf("line with an override = %q", got)
	}
}

func TestOnlyAFidelityOverrideFillsAnEmptyPlanRow(t *testing.T) {
	row := store.ReviewRow{Number: 1, Head: headA, Hold: None, SupersededBy: None, State: StateOpen, ForUsers: "a user sees it", Risk: "low"}
	failed := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityFail)}

	for name, test := range map[string]struct {
		extra map[string]string
		ready bool
		grade string
		waits string
	}{
		"a fidelity override": {extra: map[string]string{machine.OverrideFact("fidelity"): "443549"}, ready: true, grade: GradePass, waits: onlyHim},
		"a finder override":   {extra: map[string]string{machine.OverrideFact("finder"): "284378"}, grade: GradeFail, waits: "a grade needs a verdict"},
		"no override":         {grade: GradeFail, waits: "a grade needs a verdict"},
	} {
		t.Run(name, func(t *testing.T) {
			facts := maps.Clone(failed)
			maps.Copy(facts, test.extra)
			got := Materialize(row, facts, Held{}, nil, "dev")
			if got.Grade != test.grade || got.ReadyForTap != test.ready || !strings.HasPrefix(got.WaitsOn, test.waits) {
				t.Fatalf("grade %q, ready %v, waits %q, want %q, %v, %q", got.Grade, got.ReadyForTap, got.WaitsOn, test.grade, test.ready, test.waits)
			}
			if test.ready && got.PlanRow != planOverridden {
				t.Fatalf("plan row %q, want %q", got.PlanRow, planOverridden)
			}
		})
	}
}

func TestARefusedTapShowsOnItsRowUntilALaterTapStands(t *testing.T) {
	const reason = "held, root:waits on the cutover"
	refused := Materialize(store.ReviewRow{Number: 1, Head: headA}, map[string]string{bus.TapRefusedFact: reason}, Held{}, nil, "dev")
	if refused.TapRefused != reason || refused.Tapped {
		t.Fatalf("row = %+v", refused)
	}
	tapped := Materialize(store.ReviewRow{Number: 1, Head: headA}, map[string]string{bus.TapRefusedFact: reason, "tap": "tapped"}, Held{}, nil, "dev")
	if tapped.TapRefused != None || !tapped.Tapped {
		t.Fatalf("row = %+v", tapped)
	}
	if never := Materialize(store.ReviewRow{Number: 1, Head: headA}, nil, Held{}, nil, "dev"); never.TapRefused != None {
		t.Fatalf("row = %+v", never)
	}
}

func TestATappedRowIsStillReadySoLandingCanRequireBoth(t *testing.T) {
	row := readyRow()
	row.State, row.Tapped = StateTapped, true
	if row = Decide(row); !row.ReadyForTap {
		t.Fatalf("row = %+v", row)
	}
}

func memoryWith(t *testing.T, row store.ReviewRow) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	if err := s.PutReviewRows([]store.ReviewRow{row}); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestATapWritesItsByAndAtFactsSoTheNextFoldKeepsThem(t *testing.T) {
	at := time.Date(2026, 11, 4, 19, 0, 0, 0, time.UTC)
	s := memoryWith(t, Decide(readyRow()))
	if err := Tap(s, testRepo, 1, headA, "owner", at); err != nil {
		t.Fatal(err)
	}
	facts, err := s.Facts(testRepo, 1, headA)
	if err != nil {
		t.Fatal(err)
	}
	if facts["tap"] != string(machine.TapTapped) || facts[FactTapBy] != "owner" || facts[FactTapAt] != store.Stamp(at) {
		t.Fatalf("facts = %v, want the tap with who and when for Materialize to read", facts)
	}
	row, _, err := s.ReviewRow(testRepo, 1)
	if err != nil || !row.Tapped || row.ReadyForTap && !row.Tapped || row.TappedBy != "owner" {
		t.Fatalf("row = %+v, %v, want it tapped by the owner at once", row, err)
	}
}

func TestAnotherPersonsPRLeavesTheRowsUnlessHeTappedItOrItIsExempt(t *testing.T) {
	f, s := foldFor(t)
	const theirs, tapped, ours, exempted = 1, 2, 3, 4
	for _, number := range []int{theirs, tapped, ours, exempted} {
		postOpened(t, number, headA, "dev", "the body")
	}
	if _, err := f.Pass(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	if err := Tap(s, testRepo, tapped, headA, "owner", time.Now()); err != nil {
		t.Fatal(err)
	}
	if _, err := s.PutFact(testRepo, exempted, headA, "exempt", "test-only", time.Now()); err != nil {
		t.Fatal(err)
	}
	foreign := func(number int) machine.Facts {
		pr := cleanRead(number, headA)
		pr.ForeignAuthor = "a-teammate"
		return pr
	}
	if err := f.PostReads(context.Background(), []machine.Facts{foreign(theirs), foreign(tapped), cleanRead(ours, headA), foreign(exempted)}); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Pass(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	postOpened(t, theirs, headA, "dev", "a later backfill of the same PR")

	rows, err := f.Pass(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}

	kept := make([]int, 0, len(rows))
	for _, row := range rows {
		kept = append(kept, row.Number)
	}
	if !slices.Equal(kept, []int{tapped, ours, exempted}) {
		t.Fatalf("the pass returned rows %v, want the tapped PR, our own and the exempt one", kept)
	}
	if _, found, err := s.ReviewRow(testRepo, theirs); err != nil || found {
		t.Fatalf("the store still holds another person's row: found=%v err=%v", found, err)
	}
}

func TestWithdrawingAStaleCardLeavesALandedRowLanded(t *testing.T) {
	landed := readyRow()
	landed.State = StateLanded
	s := memoryWith(t, landed)
	if err := Withdraw(s, testRepo, 1, headA, "redone", time.Now()); err != nil {
		t.Fatal(err)
	}
	if row, _, err := s.ReviewRow(testRepo, 1); err != nil || row.State != StateLanded || row.SupersededBy == "redone" {
		t.Fatalf("row = %+v, %v, want it still landed", row, err)
	}
}

func postClassed(t *testing.T, number int, was, head, class, note string) {
	t.Helper()
	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "lane-7", Kind: "head", Text: fmt.Sprintf("HEAD #%d %s", number, head), PRs: []string{fmt.Sprintf("%s#%d", testRepo, number)},
		ID: fmt.Sprintf("head/%d/%s/%s", number, head, class), Attrs: map[string]string{fmt.Sprintf("head:%d", number): was + ".." + head + " " + note, "delta": class},
	}); err != nil {
		t.Fatal(err)
	}
}

func TestAHeadRecordsClassAndWhatItReplacedReachTheStore(t *testing.T) {
	f, s := foldFor(t)
	tapped, unnamed, classed := headA, headB, strings.Repeat("c", 40)
	postOpened(t, 1, tapped, "dev", "body")
	folded(t, f, 1)
	postHead(t, 1, unnamed)
	folded(t, f, 1)
	postClassed(t, 1, unnamed, classed, gates.DeltaFundamental, "replaces the design")

	folded(t, f, 1)

	got, err := s.HeadFacts(testRepo, 1, gates.DeltaFacts)
	if err != nil {
		t.Fatal(err)
	}
	said := map[string]string{}
	for _, fact := range got {
		said[fact.Head+" "+fact.Kind] = fact.Value
	}
	want := map[string]string{
		unnamed + " " + gates.DeltaFromFact: tapped,
		classed + " " + gates.DeltaFromFact: unnamed,
		classed + " " + gates.DeltaFact:     gates.DeltaFundamental,
		classed + " " + gates.DeltaWhyFact:  "replaces the design",
	}
	if !maps.Equal(said, want) {
		t.Fatalf("facts = %v, want %v", said, want)
	}
}

func TestAHeadRecordPointingBackAlongItsOwnLineageKeepsTheLineage(t *testing.T) {
	const restored, restacked, earlier = "b56c08a28754e8a8148dadccbd1f45c1c3f5c35e", "ce8b1f16f23ad1bdf269c4440ab8241ffa422eb2", "552425ab0000000000000000000000000000000a"
	const tapped, fixed, rebased = "ab3e76908947c6e70cf51fca64fc72438075ae32", "f34888bcb4cceadc281fc20b5c942da8ca3becb1", "7fbaaa3110223382e44230e4598acb85d6e7fc91"
	for name, tc := range map[string]struct {
		heads func()
		want  map[string]string
	}{
		"48611: a head restored after a restack": {
			heads: func() {
				postClassed(t, 1, earlier, restored, "rebase", "onto dev")
				postClassed(t, 1, restored, restacked, "rebase", "patch-identical")
				postClassed(t, 1, restacked, restored, "rebase", "restored pre-restack head")
			},
			want: map[string]string{restored: earlier, restacked: restored},
		},
		"51253: a class record whose text names the head it moved from first": {
			heads: func() {
				postClassed(t, 1, tapped, fixed, "review-fix", "replay")
				postClassed(t, 1, fixed, rebased, "", "restack, range-diff patch-identical")
				if _, err := client.Post(context.Background(), client.Draft{
					Drive: "d", Lane: "lane-7", Kind: "head", Text: fmt.Sprintf("#1 moved from %s to %s, a rebase onto dev", fixed, rebased), PRs: []string{testRepo + "#1"},
					ID: "head/1/moved", Attrs: map[string]string{"delta": "rebase"},
				}); err != nil {
					t.Fatal(err)
				}
			},
			want: map[string]string{fixed: tapped, rebased: fixed},
		},
	} {
		t.Run(name, func(t *testing.T) {
			f, s := foldFor(t)
			postOpened(t, 1, headA, "dev", "body")
			folded(t, f, 1)
			tc.heads()

			folded(t, f, 1)

			got, err := s.HeadFacts(testRepo, 1, []string{gates.DeltaFromFact})
			if err != nil {
				t.Fatal(err)
			}
			said := map[string]string{}
			for _, fact := range got {
				said[fact.Head] = fact.Value
			}
			for head, from := range tc.want {
				if said[head] != from {
					t.Fatalf("%s comes from %q, want %q; every delta-from: %v", head[:7], said[head], from, said)
				}
			}
		})
	}
}

func TestARowSaysHisTapCarriedOrReturnedAndWhy(t *testing.T) {
	const from = "aaaaaaaaaaaa"
	ready := readyRow()
	for name, tc := range map[string]struct {
		facts  map[string]string
		tapped bool
		waits  string
	}{
		"counted once for the PR":       {map[string]string{factCarriedBy: gates.ByTap, factTapFrom: from}, true, "You approved it; the landing desk lands it next; your tap at aaaaaaa carried to this head: his tap counts once for the PR, and no push since changed it fundamentally"},
		"carried by the same diff":      {map[string]string{factCarriedBy: gates.BySameDiff, factTapFrom: from}, true, "You approved it; the landing desk lands it next; your tap at aaaaaaa carried to this head: the PR's own diff is the same as at the tapped head"},
		"ended by a fundamental change": {map[string]string{factReturned: "a fundamental change at bbbbbbb: replaces the design", factTapFrom: from}, false, "Only your approval; your tap at aaaaaaa does not carry to this head: a fundamental change at bbbbbbb: replaces the design"},
		"his own tap at this head":      {map[string]string{"tap": string(machine.TapTapped), factCarriedBy: gates.ByTap, factTapFrom: from}, true, tappedWaits},
	} {
		t.Run(name, func(t *testing.T) {
			facts := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityPass)}
			maps.Copy(facts, tc.facts)
			row := Materialize(ready, facts, Held{}, nil, "dev")
			if row.Tapped != tc.tapped || row.WaitsOn != tc.waits {
				t.Fatalf("tapped %v, waits %q; want %v, %q", row.Tapped, row.WaitsOn, tc.tapped, tc.waits)
			}
		})
	}
}

func TestACarryReachesTheRowThroughTheRead(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, "aaaaaaaaaaaa", "dev", "body")
	folded(t, f, 1)
	read := cleanRead(1, "aaaaaaaaaaaa")
	read.TapFrom, read.CarriedBy = "9999999", gates.ByTap

	if err := f.PostReads(context.Background(), []machine.Facts{read}); err != nil {
		t.Fatal(err)
	}
	row := folded(t, f, 1)

	if !row.Tapped || row.TappedBy != "carried from 9999999" || !strings.Contains(row.WaitsOn, "counts once for the PR") {
		t.Fatalf("tapped %v by %q, waits %q", row.Tapped, row.TappedBy, row.WaitsOn)
	}
}

func portRead(sourceMerged bool, files []string) map[string]string {
	facts := gates.PortEvidence(sourceMerged, files)
	facts[gates.PortOfAttr], facts[gates.PortInteractsAttr] = "49000", gates.PortCleanWord
	return facts
}

func TestAPortAsksHimOnlyWhenItSaysItInteractsOrTheGateRefusesIt(t *testing.T) {
	const reason = "port: infra/rows/x.ts is outside go/sand/next/, so it is no pure port and lands on his tap"
	const outsideWords = "Only your approval: it changes infra/rows/x.ts outside the old tree, so it is no pure port and lands on your approval."
	clean := map[string]string{gates.PortOfAttr: "49000", gates.PortInteractsAttr: gates.PortCleanWord}
	for name, tc := range map[string]struct {
		facts map[string]string
		ready bool
		waits string
	}{
		"a clean port waits on nothing of his":                        {clean, false, PortLands},
		"a port that interacts asks him":                              {map[string]string{gates.PortOfAttr: "49000", gates.PortInteractsAttr: "yes"}, true, onlyHim},
		"a port silent on interaction asks him":                       {map[string]string{gates.PortOfAttr: "49000"}, true, onlyHim},
		"a head with no port claim asks him":                          {nil, true, onlyHim},
		"a clean claim the gate refused asks him and says why":        {map[string]string{gates.PortOfAttr: "49000", gates.PortInteractsAttr: gates.PortCleanWord, gates.StandingRefused: reason}, true, outsideWords},
		"a clean claim whose source the go read as unmerged asks him": {portRead(false, nil), true, "Only your approval: its source #49000 has not merged yet, and your approval lands this port anyway."},
		"a clean claim the go read as outside the tree asks him":      {portRead(true, []string{"infra/rows/x.ts"}), true, outsideWords},
		"a clean claim the go read as clean waits on nothing of his":  {portRead(true, []string{"go/sand/next/a.go"}), false, PortLands},
		"a flake fix waits on nothing of his":                         {map[string]string{gates.FlakeFixAttr: "TestChatStream"}, false, FlakeLands},
		"a clean port with its go lands exempt":                       {map[string]string{gates.PortOfAttr: "49000", gates.PortInteractsAttr: gates.PortCleanWord, "exempt": gates.OldTreePortClass}, false, gates.ExemptWaits(gates.OldTreePortClass)},
	} {
		t.Run(name, func(t *testing.T) {
			facts := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityPass)}
			maps.Copy(facts, tc.facts)
			row := Materialize(readyRow(), facts, Held{}, nil, "dev")
			if row.ReadyForTap != tc.ready || row.WaitsOn != tc.waits {
				t.Fatalf("ready %v, waits %q; want %v, %q", row.ReadyForTap, row.WaitsOn, tc.ready, tc.waits)
			}
		})
	}
}

func TestAPortHeadRecordReachesTheRowsFactsAtItsHead(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "lane-7", Kind: "head", Text: "HEAD #1", PRs: []string{testRepo + "#1"},
		Attrs: map[string]string{"head:1": headA, gates.PortOfAttr: "49000", gates.PortInteractsAttr: gates.PortCleanWord},
	}); err != nil {
		t.Fatal(err)
	}
	folded(t, f, 1)
	facts, err := s.Facts(testRepo, 1, headA)
	if err != nil {
		t.Fatal(err)
	}
	if facts[gates.PortOfAttr] != "49000" || facts[gates.PortInteractsAttr] != gates.PortCleanWord {
		t.Fatalf("facts = %v, want the head record's port claim", facts)
	}
}

func TestANewerHeadRecordSilentOnInteractionUndoesAnOlderCleanClaim(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	for _, attrs := range []map[string]string{
		{"head:1": headA, gates.PortOfAttr: "49000", gates.PortInteractsAttr: gates.PortCleanWord},
		{"head:1": headA, gates.PortOfAttr: "49000"},
	} {
		if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "head", Text: "HEAD #1", PRs: []string{testRepo + "#1"}, Attrs: attrs}); err != nil {
			t.Fatal(err)
		}
	}
	folded(t, f, 1)
	facts, err := s.Facts(testRepo, 1, headA)
	if err != nil {
		t.Fatal(err)
	}
	if class, _ := gates.LandsOnWord(facts); class != "" {
		t.Fatalf("facts = %v read as landing on %q, want no word, as PortAt reads the newest record", facts, class)
	}
}

func TestARootOverrideAtAHeadWhosePassesStoppedReadsAsItsGrade(t *testing.T) {
	ready := store.ReviewRow{Number: 50125, Head: headA, Hold: None, SupersededBy: None, State: StateOpen}
	base := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderClear), machine.GradeStoppedFact: "fidelity: its pass ended without a grade"}

	before := Materialize(ready, base, Held{}, nil, "dev")
	if before.Grade != GradePending || !strings.HasPrefix(before.WaitsOn, NotGraded) {
		t.Fatalf("no override: grade %q, waits %q", before.Grade, before.WaitsOn)
	}

	overridden := maps.Clone(base)
	overridden[machine.OverrideFact("fidelity")] = "accept"
	if after := Materialize(ready, overridden, Held{}, nil, "dev"); after.Grade != GradePass {
		t.Fatalf("overridden: grade %q, waits %q", after.Grade, after.WaitsOn)
	}
}

func postOverride(t *testing.T, text string) {
	t.Helper()
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "decide", Text: text, PRs: []string{testRepo + "#1"}}); err != nil {
		t.Fatal(err)
	}
}

func TestAFinderOverrideLeavesAFailedFidelityFailedAndAFidelityOverrideClearsIt(t *testing.T) {
	row := store.ReviewRow{Number: 1, Head: headA, Hold: None, SupersededBy: None, State: StateOpen}
	failed := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderAsk), "fidelity": string(machine.FidelityFail), machine.OverrideFact("finder"): "284378"}

	if got := Materialize(row, failed, Held{}, nil, "dev"); got.Grade != GradeFail || got.ReadyForTap {
		t.Fatalf("finder override alone: grade %q, ready %v, want fail and not ready", got.Grade, got.ReadyForTap)
	}
	both := maps.Clone(failed)
	both[machine.OverrideFact("fidelity")] = "443549"
	if got := Materialize(row, both, Held{}, nil, "dev"); got.Grade != GradePass {
		t.Fatalf("both overrides: grade %q, waits %q, want pass", got.Grade, got.WaitsOn)
	}
}

func failedFidelity(t *testing.T, s *store.Store) {
	t.Helper()
	for kind, value := range map[string]string{"fidelity": "fail", "finder": "clear", "verdict": "accept"} {
		if _, err := s.PutFact(testRepo, 1, headA, kind, value, time.Now().Add(-time.Hour)); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAnOldVerdictWithNoRecordNamingItsGraderClearsNothing(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	failedFidelity(t, s)
	if row := folded(t, f, 1); row.Grade != GradeFail {
		t.Fatalf("grade %q, want fail", row.Grade)
	}
}

func TestAnOldOverrideIsReadBackFromItsOwnRecordOnce(t *testing.T) {
	f, s := foldFor(t)
	postOpened(t, 1, headA, "dev", fullBody)
	postOverride(t, "fidelity-override #1 at "+headA[:7]+": 443549. the plan does not reach this.")
	failedFidelity(t, s)
	if row := folded(t, f, 1); row.Grade != GradePass {
		t.Fatalf("grade %q, want pass", row.Grade)
	}
	facts, err := s.Facts(testRepo, 1, headA)
	if err != nil {
		t.Fatal(err)
	}
	if facts[machine.OverrideFact("fidelity")] != "443549" {
		t.Fatalf("facts = %v, want the record's cite", facts)
	}
	if read, err := s.Cursor(oldOverridesCursor); err != nil || read == 0 {
		t.Fatalf("cursor = %d, %v, want it advanced so the read runs once", read, err)
	}
}

func TestATappedRowSaysWhyTheLandingPassCannotMoveIt(t *testing.T) {
	const why = "the landing pass reads it as grading"
	row := store.ReviewRow{Number: 1, Head: headA, Hold: None, SupersededBy: None, State: StateOpen}
	facts := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityPass), bus.StuckFact: why}

	if got := Materialize(row, facts, Held{}, nil, "dev"); strings.Contains(got.WaitsOn, why) {
		t.Fatalf("an untapped row says %q", got.WaitsOn)
	}

	facts["exempt"] = "test-only"
	if got := Materialize(row, facts, Held{}, nil, "dev"); !strings.HasSuffix(got.WaitsOn, "; "+why) {
		t.Fatalf("a tapped row says %q, want it to end on the reason", got.WaitsOn)
	}

	facts[bus.StuckFact] = ""
	if got := Materialize(row, facts, Held{}, nil, "dev"); strings.Contains(got.WaitsOn, ";") {
		t.Fatalf("a cleared reason still reads %q", got.WaitsOn)
	}
}

func TestBlocksWordsSaysWhatStopsARowWithNoRecordOfIt(t *testing.T) {
	for name, tc := range map[string]struct{ waits, hold, want string }{
		"his post": {
			"", "root:Hold on #50160 until he or owner-relay lifts it: his 2:30 PM word (72a4a5f) holds it until compaction-time marker insertion is out. The code writes none, tests enforce that, and he has been told. Replaces 440473, which closed itself when it resolved 439442. (record #440500)",
			"Held: until he or owner-relay lifts it: his 2:30 PM word holds it until compaction-time marker insertion is out.",
		},
		"a lane's hold": {
			"", "cutover:cutover-deletion-sweep-3 holds #48841: lands only once plan 12.0 reads true and the owner promotes. (record #361725)",
			"Held: lands only once plan 12.0 reads true and the owner promotes.",
		},
		"a clocked hold": {
			"", "cutover:2:51 PM hold #48173 widened (replaces 324972): lands only when the cutover has deleted the old tree. orphan-pr",
			"Held: lands only when the cutover has deleted the old tree.",
		},
		"grade running":  {NotGraded, None, "Its review grade is still running at this version."},
		"grade verdict":  {"a grade needs a verdict: finder: ok f3123c0 luna over 1b73703", None, "A review grade needs a verdict at this version."},
		"below a parent": {"the change below it, #48034, has not landed", None, "The change below it, #48034, has not landed."},
		"already a line": {"Only your approval.", None, "Only your approval."},
		"landed":         {"Nothing: it landed.", None, "Nothing: it landed."},
	} {
		t.Run(name, func(t *testing.T) {
			row := store.ReviewRow{WaitsOn: tc.waits, Hold: tc.hold}
			if tc.waits == "" {
				row.WaitsOn = heldLead + tc.hold
			}
			if got := BlocksWords(row); got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestAFidelityGradeParkedOnACardSaysOnItsRowWhatItWaitsOn(t *testing.T) {
	row := store.ReviewRow{Number: 1, Head: headA, Hold: None, SupersededBy: None, State: StateOpen}
	waits := "fidelity waits on his answer to card rmw37abc, and that card was taken off his board before he answered, so nothing will answer it: root redraws the card or settles the grade"
	parked := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityFail), "fidelity-said": "fidelity: missing " + headA[:7] + " class owner-pick", machine.FidelityWaitsFact: waits}

	got := Materialize(row, parked, Held{}, nil, "dev")

	if got.Grade != GradeFail || !strings.HasSuffix(got.WaitsOn, waits) {
		t.Errorf("grade %q, waits %q", got.Grade, got.WaitsOn)
	}
}

func TestARowWaitingOnAGradeNamesOnlyTheGraderNotYetCleared(t *testing.T) {
	row := store.ReviewRow{Number: 51660, Head: headA, Hold: None, SupersededBy: None, State: StateOpen}
	const finderSaid, fidelitySaid = "finder: major 67502b2 1 open over delta 71347fe..67502b2: SessionPage.tsx:20 second fix in this area", "fidelity: missing 67502b2 answer/c65420f"
	facts := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderAsk), "finder-said": finderSaid, machine.OverrideFact("finder"): "519362", "fidelity": string(machine.FidelityFail), "fidelity-said": fidelitySaid}

	got := Materialize(row, facts, Held{}, nil, "dev")
	if got.Grade != GradeFail || got.GradeFindings != fidelitySaid {
		t.Fatalf("an overridden finder with a missing fidelity: grade %q, findings %q, want only %q", got.Grade, got.GradeFindings, fidelitySaid)
	}

	facts["fidelity"], facts[machine.OverrideFact("finder")] = string(machine.FidelityPass), ""
	if got := Materialize(row, facts, Held{}, nil, "dev"); got.GradeFindings != finderSaid {
		t.Fatalf("a major finder with a passing fidelity: findings %q, want only %q", got.GradeFindings, finderSaid)
	}
}

func TestAnEvictedRowReadsEvictedWithItsReasonUntilTheQueueHoldsItAgain(t *testing.T) {
	const why = "Trunk failed it at t1 (TEST_RUN_REQUIRED_STATUS_FAILED); failing check buildkite/test"
	evicted := map[string]string{factCI: CIGreen, factQueue: string(machine.QueueEvicted), machine.EvictionFact: why, "tap": string(machine.TapTapped)}

	row := Materialize(store.ReviewRow{Number: 1, Head: headA}, evicted, Held{}, nil, "dev")
	if row.State != StateEvicted || !strings.Contains(row.WaitsOn, why) {
		t.Fatalf("state = %q, waits on %q", row.State, row.WaitsOn)
	}

	evicted[factQueue] = string(machine.QueueQueued)
	if row := Materialize(store.ReviewRow{Number: 1, Head: headA}, evicted, Held{}, nil, "dev"); row.State != StateQueued {
		t.Fatalf("a row the queue holds again reads %q", row.State)
	}
}

func TestAnOldTreePRsRowSaysItsPortIsOwedUntilAPortOrANoPortReasonNamesIt(t *testing.T) {
	const steptree = "go/sand/internal/steptree/tree.go"
	f, _ := foldFor(t)
	oldTree := func(number int, head string) machine.Facts {
		pr := cleanRead(number, head)
		pr.OldTree = steptree
		return pr
	}
	read := func(prs ...machine.Facts) {
		t.Helper()
		if _, err := f.Pass(context.Background(), nil); err != nil {
			t.Fatal(err)
		}
		if err := f.PostReads(context.Background(), prs); err != nil {
			t.Fatal(err)
		}
	}
	post := func(number int, kind string, attrs map[string]string) {
		t.Helper()
		if _, err := client.Post(context.Background(), client.Draft{
			Drive: "d", Lane: "port-lane", Kind: kind, Text: fmt.Sprintf("%s #%d %s", kind, number, headB), PRs: []string{fmt.Sprintf("%s#%d", testRepo, number)}, Attrs: attrs,
		}); err != nil {
			t.Fatal(err)
		}
	}

	postOpened(t, 50525, headA, "dev", fullBody)
	postOpened(t, 1, headA, "dev", fullBody)
	read(oldTree(50525, headA), cleanRead(1, headA))
	if row := folded(t, f, 50525); !strings.HasPrefix(row.Port, PortOwed+": it changes "+steptree) {
		t.Fatalf("open old-tree fix with no port: port %q, want it owed", row.Port)
	}
	if row := folded(t, f, 1); row.Port != None {
		t.Fatalf("a PR outside the old tree: port %q, want %q", row.Port, None)
	}

	merged := oldTree(50525, headA)
	merged.Merged, merged.Closed = true, true
	read(merged)
	if row := folded(t, f, 50525); row.State != StateLanded || !strings.HasPrefix(row.Port, PortOwed) {
		t.Fatalf("merged old-tree fix with no port: state %q, port %q, want it still owed", row.State, row.Port)
	}

	post(50849, "head", map[string]string{gates.PortOfAttr: "50525", gates.PortInteractsAttr: gates.PortCleanWord})
	read()
	postOpened(t, 50849, headB, "dev", fullBody)
	read(cleanRead(50849, headB))
	if row := folded(t, f, 50525); row.Port != "port #50849, open" {
		t.Fatalf("old-tree fix once its port opened: port %q", row.Port)
	}
	if row := folded(t, f, 50849); row.Port != PortOf+"50525" {
		t.Fatalf("the port itself: port %q, want %q", row.Port, PortOf+"50525")
	}

	postOpened(t, 48967, headA, "dev", fullBody)
	both := oldTree(48967, headA)
	both.NextTree = "go/sand/next/internal/steptree/tree.go"
	read(both)
	if row := folded(t, f, 48967); row.Port != PortInPR+": it changes go/sand/next/internal/steptree/tree.go in the next tree too" {
		t.Fatalf("an old-tree fix that changes the next tree too: port %q, want it ported in the same PR", row.Port)
	}

	postOpened(t, 2, headA, "dev", fullBody)
	read(oldTree(2, headA))
	post(2, "decide", map[string]string{NoPortAttr: "old-only: the next tree has no steptree wrapper"})
	if row := folded(t, f, 2); row.Port != "no port: old-only: the next tree has no steptree wrapper" {
		t.Fatalf("old-tree PR with a recorded reason: port %q", row.Port)
	}
	read(oldTree(2, headB))
	if row := folded(t, f, 2); !strings.HasPrefix(row.Port, PortOwed) {
		t.Fatalf("a new head past the reason: port %q, want it owed again", row.Port)
	}
}

func TestARowsMovedAtHoldsUntilItsHeadGradeOrQueueChanges(t *testing.T) {
	f, _ := foldFor(t)
	now := time.Date(2026, 11, 5, 18, 0, 0, 0, time.UTC)
	f.Now = func() time.Time { return now }
	read := func(pr machine.Facts) store.ReviewRow {
		t.Helper()
		if _, err := f.Pass(context.Background(), nil); err != nil {
			t.Fatal(err)
		}
		if err := f.PostReads(context.Background(), []machine.Facts{pr}); err != nil {
			t.Fatal(err)
		}
		return folded(t, f, 50849)
	}

	postOpened(t, 50849, headA, "dev", fullBody)
	read(cleanRead(50849, headA))
	opened := store.Stamp(now)
	now = now.Add(2 * time.Hour)
	if row := read(cleanRead(50849, headA)); row.MovedAt != opened {
		t.Fatalf("an unchanged row two hours on: moved at %q, want %q", row.MovedAt, opened)
	}
	queued := cleanRead(50849, headA)
	queued.Queue, queued.QueuedHead = machine.QueueQueued, headA
	if row := read(queued); row.MovedAt != store.Stamp(now) {
		t.Fatalf("a row that entered the queue: moved at %q, want %q", row.MovedAt, store.Stamp(now))
	}
}

func TestAnExemptHeadSaysItsClassAndOnlyHisTapReadsTapped(t *testing.T) {
	const from = "aaaaaaaaaaaa"
	for name, tc := range map[string]struct {
		facts  map[string]string
		held   Held
		tapped bool
		ready  bool
		waits  string
	}{
		"exempt with no tap":               {map[string]string{"exempt": "test-only"}, Held{}, false, false, "Exempt as test-only; the landing desk lands it next."},
		"a class no ruling lands untapped": {map[string]string{"exempt": "dev-red"}, Held{}, false, true, onlyHim},
		"exempt and tapped by him":         {map[string]string{"exempt": "test-only", "tap": string(machine.TapTapped)}, Held{}, true, true, tappedWaits},
		"exempt with a tap carried": {
			map[string]string{"exempt": "test-only", factCarriedBy: gates.ByTap, factTapFrom: from},
			Held{},
			true, true,
			strings.TrimSuffix(tappedWaits, ".") + "; your tap at aaaaaaa carried to this head: " + gates.CarryWhy(gates.ByTap),
		},
		"tapped by him":         {map[string]string{"tap": string(machine.TapTapped)}, Held{}, true, true, tappedWaits},
		"exempt, until his tap": {map[string]string{"exempt": "test-only"}, Held{AwaitsTap: "asks his tap"}, false, true, tapEndsHold + "asks his tap"},
	} {
		t.Run(name, func(t *testing.T) {
			facts := map[string]string{factCI: CIGreen, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityPass)}
			maps.Copy(facts, tc.facts)
			row := Materialize(readyRow(), facts, tc.held, nil, "dev")
			if row.Tapped != tc.tapped || row.ReadyForTap != tc.ready || row.WaitsOn != tc.waits {
				t.Fatalf("tapped %v, ready %v, waits %q; want %v, %v, %q", row.Tapped, row.ReadyForTap, row.WaitsOn, tc.tapped, tc.ready, tc.waits)
			}
			if !row.Tapped && (row.TappedBy != NotYet || row.TappedAt != NotYet) {
				t.Fatalf("an untapped row names a tap: by %q at %q", row.TappedBy, row.TappedAt)
			}
			if exempt := !row.Tapped && !row.ReadyForTap; exempt != (row.State == StateExempt) {
				t.Fatalf("state %q on a row tapped %v, ready %v", row.State, row.Tapped, row.ReadyForTap)
			}
			if refusal := TapRefusal(row); refusal != "" {
				t.Fatalf("his tap on this row is refused: %q", refusal)
			}
			if wait := gates.RowWait(row, true, headA); wait != "" {
				t.Fatalf("the row gate waits on %q, and an exempt head must pass it as it did", wait)
			}
		})
	}
}

func TestAnExemptHeadWithAnUnmetGateSaysTheGate(t *testing.T) {
	facts := map[string]string{factCI: CIRed, factApprovers: "a", factMergeable: string(machine.MergeableClean), "finder": string(machine.FinderClear), "fidelity": string(machine.FidelityPass), "exempt": "test-only"}
	row := Materialize(readyRow(), facts, Held{}, nil, "dev")
	if row.Tapped || row.WaitsOn != RedAtHead || gates.LandsExempt(row) {
		t.Fatalf("row = %+v", row)
	}
}

func TestAFoldClearsAnExemptTapFromALandedRowAndKeepsHisOwn(t *testing.T) {
	f, s := foldFor(t)
	landed := func(number int, by string, facts map[string]string) {
		t.Helper()
		row := readyRow()
		row.Number, row.State, row.Tapped, row.TappedAt, row.TappedBy = number, StateLanded, true, "2026-11-04T05:00:00Z", by
		if err := s.PutReviewRows([]store.ReviewRow{row}); err != nil {
			t.Fatal(err)
		}
		for kind, value := range facts {
			if _, err := s.PutFact(testRepo, number, headA, kind, value, time.Now()); err != nil {
				t.Fatal(err)
			}
		}
	}
	landed(1, notRecorded, map[string]string{"exempt": "test-only-or-ci-only"})
	landed(2, "owner", map[string]string{"exempt": "test-only", "tap": string(machine.TapTapped)})
	landed(3, "carried from aaaaaaa", map[string]string{"exempt": "test-only", factCarriedBy: "review-fix"})
	landed(4, "owner", map[string]string{"tap": string(machine.TapTapped)})

	if row := folded(t, f, 1); row.Tapped || row.TappedBy != NotYet || row.TappedAt != NotYet || row.State != StateLanded {
		t.Fatalf("exempt with no tap: row = %+v", row)
	}
	for _, number := range []int{2, 3, 4} {
		if row := folded(t, f, number); !row.Tapped {
			t.Fatalf("#%d lost its tap: row = %+v", number, row)
		}
	}
}

func gitHubPull(t *testing.T, number int, outcome, head string, at time.Time) client.Record {
	t.Helper()
	var record client.Record
	raw := fmt.Sprintf(`{"seq":1,"drive":"github-pr","lane":"github","kind":"github","at":%q,"topic":"pull_request","refs":{"prs":["%s#%d"]},"fields":{"outcome":%q,"commit":%q}}`,
		at.Format(time.RFC3339Nano), testRepo, number, outcome, head)
	if err := json.Unmarshal([]byte(raw), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func followed(t *testing.T, row store.ReviewRow, records ...client.Record) map[int]store.ReviewRow {
	t.Helper()
	f, s := foldFor(t)
	rows := map[int]store.ReviewRow{row.Number: row}
	for _, record := range records {
		if err := s.Update(func(tx *store.Tx) error { return f.follow(tx, rows, record) }); err != nil {
			t.Fatal(err)
		}
	}
	return rows
}

func TestAPushGitHubReportsMovesTheRowsHeadAndKeepsItsLane(t *testing.T) {
	pushedAt := time.Now()
	row := readyRow()
	row.Lane, row.HeadAt = "lane-7", store.Stamp(pushedAt.Add(-time.Hour))
	got := followed(t, row, gitHubPull(t, 1, "synchronize", headB, pushedAt))[1]
	if got.Head != headB || got.HeadAt != store.Stamp(pushedAt) || got.Lane != "lane-7" {
		t.Fatalf("row = head %s at %s lane %q, want head %s at %s on lane-7", got.Head, got.HeadAt, got.Lane, headB, store.Stamp(pushedAt))
	}
}

func TestAnEditedDescriptionRefreshesTheRowAndAPushWithoutOneKeepsIt(t *testing.T) {
	edited := gitHubPull(t, 1, "edited", headA, time.Now())
	body, err := BodyRecord("## Summary\n\nthe new words a user sees\n\n## Risk profile\n\nnone")
	if err != nil {
		t.Fatal(err)
	}
	edited.Body = body
	got := followed(t, readyRow(), edited, gitHubPull(t, 1, "synchronize", headB, time.Now().Add(time.Second)))[1]
	if got.ForUsers != "the new words a user sees" || got.Risk != "none" {
		t.Fatalf("row for_users %q risk %q, want the edited description's", got.ForUsers, got.Risk)
	}
}

func TestAnEditOlderThanTheRowsWordsLeavesThemAndANewerOneWaitsForARead(t *testing.T) {
	read := time.Now()
	row := readyRow()
	row.ReadHead, row.WordsAt = headA, read.UTC().Format(time.RFC3339Nano)
	body, err := BodyRecord("## Summary\n\nthe words before the read\n\n## Risk profile\n\nnone")
	if err != nil {
		t.Fatal(err)
	}
	older := gitHubPull(t, 1, "edited", headA, read.Add(-time.Minute))
	older.Body = body
	if got := followed(t, row, older)[1]; got.ForUsers != "a user sees it" || WordsWait(got) != "" {
		t.Fatalf("for users %q, %q after an edit older than the read; want the read's words kept", got.ForUsers, WordsWait(got))
	}
	newer := gitHubPull(t, 1, "edited", headA, read.Add(time.Minute))
	if got := followed(t, row, newer)[1]; WordsWait(got) == "" {
		t.Fatal("an edit newer than the read left the row's words read")
	}
}

func TestAPushOlderThanTheRowsHeadLeavesTheHead(t *testing.T) {
	headAt := time.Now()
	row := readyRow()
	row.HeadAt = store.Stamp(headAt)
	if got := followed(t, row, gitHubPull(t, 1, "synchronize", headB, headAt.Add(-time.Minute)))[1]; got.Head != headA {
		t.Fatalf("head = %s after an older push, want %s kept", got.Head, headA)
	}
}

func TestAMergeGitHubReportsLandsTheRowAndALaterCloseLeavesItLanded(t *testing.T) {
	at := time.Now()
	got := followed(t, readyRow(), gitHubPull(t, 1, stateMerged, headA, at), gitHubPull(t, 1, StateClosed, headA, at.Add(time.Second)))[1]
	if got.State != StateLanded || got.LandedAt != store.Stamp(at) {
		t.Fatalf("row = %s landed at %s, want landed at %s", got.State, got.LandedAt, store.Stamp(at))
	}
}

func TestACloseClosesTheRowAndAReopenOpensIt(t *testing.T) {
	at := time.Now()
	row := readyRow()
	if got := followed(t, row, gitHubPull(t, 1, StateClosed, headA, at))[1]; got.State != StateClosed {
		t.Fatalf("state = %s after a close, want closed", got.State)
	}
	if got := followed(t, row, gitHubPull(t, 1, StateClosed, headA, at), gitHubPull(t, 1, "reopened", headA, at.Add(time.Second)))[1]; got.State != StateOpen {
		t.Fatalf("state = %s after a reopen, want open", got.State)
	}
}

func TestAPullRequestEventMakesNoRowForAPRTheDriveDoesNotHold(t *testing.T) {
	rows := followed(t, readyRow(), gitHubPull(t, 2, "opened", headB, time.Now()))
	if _, made := rows[2]; made || len(rows) != 1 {
		t.Fatalf("rows = %v, want only #1", slices.Collect(maps.Keys(rows)))
	}
}

func gitHubCheck(t *testing.T, repo, head, check, outcome, required string) client.Record {
	t.Helper()
	var record client.Record
	raw := fmt.Sprintf(`{"seq":1,"drive":"github-pr","lane":"github","kind":"github","at":%q,"topic":"check","fields":{"outcome":%q,"commit":%q,"attrs":{"repo":%q,"check":%q,"required":%q}}}`,
		time.Now().Format(time.RFC3339Nano), outcome, head, repo, check, required)
	if err := json.Unmarshal([]byte(raw), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func checkedCI(t *testing.T, records ...client.Record) string {
	t.Helper()
	f, s := foldFor(t)
	rows := map[int]store.ReviewRow{1: readyRow()}
	for _, record := range records {
		if err := s.Update(func(tx *store.Tx) error { return f.follow(tx, rows, record) }); err != nil {
			t.Fatal(err)
		}
	}
	facts, err := s.Facts(testRepo, 1, headA)
	if err != nil {
		t.Fatal(err)
	}
	return facts[factCI]
}

func TestTheOnlyRequiredCheckPassingAtTheHeadReadsGreen(t *testing.T) {
	if ci := checkedCI(t, gitHubCheck(t, testRepo, headA, "buildkite/test", "success", "buildkite/test")); ci != CIGreen {
		t.Fatalf("ci = %q, want green", ci)
	}
}

func TestARowWaitsForEveryRequiredCheckBeforeGreen(t *testing.T) {
	first := gitHubCheck(t, testRepo, headA, "test", "success", "test,lint")
	if ci := checkedCI(t, first); ci != "" {
		t.Fatalf("ci = %q with one of two required checks passed, want it left to the read", ci)
	}
	if ci := checkedCI(t, first, gitHubCheck(t, testRepo, headA, "lint", "skipped", "test,lint")); ci != CIGreen {
		t.Fatalf("ci = %q with both required checks passed, want green", ci)
	}
}

func TestARequiredFailureReadsRedAndARerunThatPassesReadsGreen(t *testing.T) {
	failed := gitHubCheck(t, testRepo, headA, "test", "failure", "test")
	if ci := checkedCI(t, failed); ci != CIRed {
		t.Fatalf("ci = %q after a required failure, want red", ci)
	}
	if ci := checkedCI(t, failed, gitHubCheck(t, testRepo, headA, "test", "success", "test")); ci != CIGreen {
		t.Fatalf("ci = %q after the rerun passed, want green", ci)
	}
}

func TestARerunAtTheSameHeadReadsPendingUntilItsResult(t *testing.T) {
	passed := gitHubCheck(t, testRepo, headA, "test", "success", "test")
	rerun := gitHubCheck(t, testRepo, headA, "test", "pending", "test")
	if ci := checkedCI(t, passed, rerun); ci != CIPending {
		t.Fatalf("ci = %q while the rerun runs, want pending", ci)
	}
	if ci := checkedCI(t, passed, rerun, gitHubCheck(t, testRepo, headA, "test", "failure", "test")); ci != CIRed {
		t.Fatalf("ci = %q after the rerun failed, want red", ci)
	}
}

func TestACheckOnAnotherHeadOrRepoLeavesTheRow(t *testing.T) {
	for _, record := range []client.Record{
		gitHubCheck(t, testRepo, headB, "test", "failure", "test"),
		gitHubCheck(t, "o/other", headA, "test", "failure", "test"),
	} {
		if ci := checkedCI(t, record); ci != "" {
			t.Fatalf("ci = %q from %+v, want the row untouched", ci, record.Fields)
		}
	}
}

func postWordsHead(t *testing.T, head string, attrs map[string]string, body string) {
	t.Helper()
	draft := client.Draft{
		Drive: "d", Lane: "lane-7", Kind: "head", Text: "HEAD #1 " + head, PRs: []string{testRepo + "#1"},
		Attrs: map[string]string{"repo": testRepo, "head": head},
	}
	maps.Copy(draft.Attrs, attrs)
	if body != "" {
		encoded, err := BodyRecord(body)
		if err != nil {
			t.Fatal(err)
		}
		draft.Body = encoded
	}
	if _, err := client.Post(context.Background(), draft); err != nil {
		t.Fatal(err)
	}
}

func postRead(t *testing.T, f Fold, pr machine.Facts) store.ReviewRow {
	t.Helper()
	if err := f.PostReads(context.Background(), []machine.Facts{pr}); err != nil {
		t.Fatal(err)
	}
	return folded(t, f, 1)
}

func TestAHeadThatChangesThePRsWordsWaitsForAGitHubReadTakenAfterItAndThenShowsIt(t *testing.T) {
	rebuilt := strings.Replace(fullBody, "A user sees it.", "A user sees the rebuild.", 1)
	for name, head := range map[string]struct {
		attrs    map[string]string
		body     string
		forUsers string
	}{
		"delta=fundamental":       {map[string]string{"delta": gates.DeltaFundamental}, "", "A user sees it."},
		"a body the head carries": {nil, rebuilt, "A user sees the rebuild."},
	} {
		t.Run(name, func(t *testing.T) {
			f, _ := foldFor(t)
			postOpened(t, 1, headA, "dev", "")
			folded(t, f, 1)
			first := cleanRead(1, headA)
			first.Body, first.ReadAt = fullBody, time.Now().Add(-time.Minute)
			if row := postRead(t, f, first); WordsWait(row) != "" {
				t.Fatalf("after the first read: %q, want the row's words read at its head", WordsWait(row))
			}

			postWordsHead(t, headB, head.attrs, head.body)
			row := folded(t, f, 1)
			if row.Head != headB || WordsWait(row) != "waits on a read of its GitHub title and body at bbbbbbb" {
				t.Fatalf("after the head: head %s, %q; want %s waiting on a read there", row.Head, WordsWait(row), headB)
			}

			stale := cleanRead(1, headB)
			stale.Body, stale.ReadAt = fullBody, time.Now().Add(-30*time.Second)
			row = postRead(t, f, stale)
			if WordsWait(row) == "" || row.ForUsers != head.forUsers || row.Title != "the title" {
				t.Fatalf("after a read taken before the head: %q, for users %q, title %q; want still waiting with %q kept", WordsWait(row), row.ForUsers, row.Title, head.forUsers)
			}

			fresh := cleanRead(1, headB)
			fresh.Title, fresh.Body, fresh.ReadAt = "the rebuilt title", rebuilt, time.Now().Add(time.Second)
			row = postRead(t, f, fresh)
			if WordsWait(row) != "" || row.Title != "the rebuilt title" || row.ForUsers != "A user sees the rebuild." {
				t.Fatalf("after a read taken after the head: %q, title %q, for users %q; want GitHub's rebuilt words", WordsWait(row), row.Title, row.ForUsers)
			}
		})
	}
}

func TestAFundamentalDeltaAtTheReadHeadPostsOneMoreReadWithItsBodyAndATitleEditPostsOne(t *testing.T) {
	f, _ := foldFor(t)
	postOpened(t, 1, headA, "dev", "")
	folded(t, f, 1)
	read := cleanRead(1, headA)
	read.Body, read.ReadAt = fullBody, time.Now().Add(-time.Minute)
	postRead(t, f, read)
	counts := func() (reads, bodies int) {
		t.Helper()
		found, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{readKind}})
		if err != nil {
			t.Fatal(err)
		}
		for _, record := range found {
			if len(record.Body) > 0 {
				bodies++
			}
		}
		return len(found), bodies
	}

	postWordsHead(t, headA, map[string]string{"delta": gates.DeltaFundamental}, "")
	if row := folded(t, f, 1); WordsWait(row) == "" {
		t.Fatalf("a delta=fundamental head at the read head left the row's words read")
	}
	again := read
	again.ReadAt = time.Now().Add(time.Second)
	row := postRead(t, f, again)
	postRead(t, f, again)
	if reads, bodies := counts(); reads != 2 || bodies != 2 || WordsWait(row) != "" {
		t.Fatalf("reads %d, bodies %d, %q; want one more read carrying the body, and the row's words read", reads, bodies, WordsWait(row))
	}

	renamed := again
	renamed.Title = "a new title"
	if row := postRead(t, f, renamed); row.Title != "a new title" {
		t.Fatalf("title %q after GitHub renamed it, want the new title", row.Title)
	}
	if reads, _ := counts(); reads != 3 {
		t.Fatalf("reads %d after a title edit, want 3", reads)
	}
}

func TestARowsPortStateReadsItsHeadRecordThenItsBodysPortLine(t *testing.T) {
	for name, tc := range map[string]struct {
		facts map[string]string
		body  string
		want  string
	}{
		"its head record names the source":        {map[string]string{gates.PortOfAttr: "52125"}, "", PortOf + "52125"},
		"#52199's body line and no head record":   {map[string]string{}, "port of #52125\n\n## Summary\n\nA table load's trace splits its timings.", PortOf + "52125"},
		"#52183's port words opening":             {map[string]string{}, "Port, old tree to new: dev #52125 (port of #52125)\n\n## Summary", PortOf + "52125"},
		"the head record wins over the body":      {map[string]string{gates.PortOfAttr: "52007"}, "port of #52125", PortOf + "52007"},
		"a follow-up to a port is no port":        {map[string]string{}, "follow-up to port of #52125", None},
		"no port anywhere":                        {map[string]string{}, "## Summary\n\nChats load.", None},
		"an old-tree PR keeps its own port state": {map[string]string{FactOldTree: "go/sand"}, "port of #52125", fmt.Sprintf("%s: it changes go/sand in the old tree, and no port PR's head record or no-port decide names it", PortOwed)},
	} {
		t.Run(name, func(t *testing.T) {
			if got := portState(tc.facts, store.ReviewRow{}, tc.body); got != tc.want {
				t.Fatalf("portState = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestABodyIsCardReadyOnlyWithAPlainSummaryAPlanLineAndAContext(t *testing.T) {
	const ready = "## Context\n\nChats reconnected late after a network drop.\n\n## Summary\n\nChats load right after a network drop.\n\nPlan: bug list item 4 (slack-bugs)\n\n## Show me\n\n![a chat loading right after the drop](https://example.com/drop.png)\n"
	if gaps := BodyGaps(ready); len(gaps) != 0 {
		t.Fatalf("gaps = %q, want none", gaps)
	}
	drawn := strings.Replace(ready, "![a chat loading right after the drop](https://example.com/drop.png)", "The relay now reconnects:\n\n```arch\n"+`{"arch":1,"name":"relay","title":"Reconnect","nodes":[{"id":"chat","kind":"service","name":"Chat","role":"reconnects"}]}`+"\n```", 1)
	if gaps := BodyGaps(drawn); len(gaps) != 0 {
		t.Fatalf("gaps = %q, want an arch block that draws to stand as the show-me", gaps)
	}
	if gaps := BodyGaps(strings.Replace(ready, "![a chat loading right after the drop](https://example.com/drop.png)", "Before: a chat spins for a minute after the drop. After: it loads at once.", 1)); len(gaps) != 0 {
		t.Fatalf("gaps = %q, want a sentence to stand as the show-me when it reads best", gaps)
	}
	if words, doc := ShowMeWords(store.ReviewRow{Description: drawn}), ShowMeDiagram(store.ReviewRow{Description: drawn}); words != "The relay now reconnects:" || !strings.HasPrefix(string(doc), `{"arch":1`) {
		t.Fatalf("words = %q, doc = %s; want the words beside the block and the block as the diagram", words, doc)
	}
	for name, tc := range map[string]struct{ body, gap string }{
		"no Plan: line":                    {strings.Replace(ready, "Plan: bug list item 4 (slack-bugs)\n", "", 1), "the plan row: its PR body has no Plan: line"},
		"a code term":                      {strings.Replace(ready, "Chats load right", "`reconnect()` runs right", 1), "what it changes for a user: its Summary carries"},
		"no Context":                       {strings.Replace(ready, "## Context\n\nChats reconnected late after a network drop.\n\n", "", 1), "its plain-words ELI5"},
		"an unnamed plan form":             {strings.Replace(ready, "bug list item 4", "the slack bugs list", 1), "names no plan step"},
		"no Show me":                       {strings.Split(ready, "## Show me")[0], "its show-me: its PR body has no Show me section"},
		"an arch block that does not draw": {strings.Replace(ready, "![a chat loading right after the drop](https://example.com/drop.png)", "```arch\n{}\n```", 1), "its show-me: its Show me arch block does not draw"},
		"a new function, no call":          {strings.Replace(ready, "Chats load right after a network drop.", "Chats load right after a network drop, and a new function presigns uploads.", 1), "it adds a function, and its Show me section has no real Sand API call"},
	} {
		if gaps := strings.Join(BodyGaps(tc.body), "; "); !strings.Contains(gaps, tc.gap) {
			t.Errorf("%s: gaps = %q, want %q", name, gaps, tc.gap)
		}
	}
}

func TestAReleaseCardNamingALoopBreakerSaysTheLLMResolverStays(t *testing.T) {
	const body = "## Context\n\nA release stalled when two stacks named each other.\n\n## Summary\n\n%s\n\nPlan: owner:m1913\n\n## Show me\n\n![the release page](https://example.com/release.png)\n"
	for name, tc := range map[string]struct{ summary, gap string }{
		"#52709's card, the resolver unsaid": {"Releases no longer stall: the loop breaker is deleted.", "that the LLM resolver stays"},
		"the resolver said to stay":          {"Releases no longer stall: the loop breaker is deleted, and the LLM resolver that builds the first plan stays.", ""},
		"the resolver said to be kept":       {"Releases no longer stall. This keeps the LLM loop resolver and deletes the loop-breaker.", ""},
		"no loop breaker":                    {"Releases no longer stall.", ""},
	} {
		gaps := strings.Join(BodyGaps(fmt.Sprintf(body, tc.summary)), "; ")
		if tc.gap == "" && gaps != "" || !strings.Contains(gaps, tc.gap) {
			t.Errorf("%s: gaps = %q, want %q", name, gaps, tc.gap)
		}
	}
}

func TestAPortCardSaysItIsAPortAndWhatTheNewTreeForcesWhenItAsks(t *testing.T) {
	const ready = "## Context\n\nChats reconnected late after a network drop.\n\n## Summary\n\n%s\n\nPlan: bug list item 4 (slack-bugs)\n"
	for name, tc := range map[string]struct{ summary, gap string }{
		"a port that says so":              {"Port of the reconnect fix: chats load right after a network drop.", ""},
		"a port that does not say so":      {"Chats load right after a network drop.", "its Summary's first line does not say it is a port"},
		"a port asking with no tree":       {"Port of the reconnect fix: chats load after a drop.\nKeep the old retry?", "what differs in the new tree"},
		"a port asking with what it found": {"Port of the reconnect fix: chats load after a drop.\nThe new tree has no retry queue. Keep the old retry?", ""},
	} {
		var row store.ReviewRow
		ApplyBody(&row, fmt.Sprintf(ready, tc.summary))
		row.Port = PortOf + "49412"
		gaps := strings.Join(CardGaps(row), "; ")
		if tc.gap == "" && strings.Contains(gaps, "port") || tc.gap != "" && !strings.Contains(gaps, tc.gap) {
			t.Errorf("%s: gaps = %q, want %q", name, gaps, tc.gap)
		}
	}
}

const findings52504 = "finder: major 867546b37e021f19e78f388535eb383f3c945144 5 open over whole 867546b^..867546b: infra/lib/s3tables.ts:83-92; [Pulumi DataLakeSettings docs](https://www.pulumi.com/registry/packages/aws/api-docs/lakeformation/datalakesettings/) The account-wide DataLakeSettings resource omits read-only admins, trusted resource owners and external filtering allowlists; fidelity: missing 867546b37e021f19e78f388535eb383f3c945144 plan:81,AGENTS.md:144 plan names no lakeFormationAdmins row field, added at infra/lib/s3tables.ts:21"

func TestAGreenApprovedRowHeldOnlyByItsLaneNamesEachGapInPlainWords(t *testing.T) {
	held := readyRow()
	held.Grade, held.GradeFindings, held.PlanRow = "fail", findings52504, ""
	gaps := LaneGaps(held)
	if len(gaps) != 3 || !strings.HasPrefix(gaps[0], "The account-wide DataLakeSettings resource omits") || !strings.HasPrefix(gaps[1], "plan names no lakeFormationAdmins row field") || gaps[2] != PlanUnnamed {
		t.Fatalf("gaps = %q, want the finder's sentence, the fidelity sentence and the missing plan row", gaps)
	}
	if strings.Contains(strings.Join(gaps, " "), "867546b") {
		t.Fatalf("gaps = %q carry a hash", gaps)
	}
	red := held
	red.CI = CIRed
	if gaps := LaneGaps(red); gaps != nil {
		t.Fatalf("a red row's gaps = %q, want none, since CI is not its lane's gap alone", gaps)
	}
	if gaps := LaneGaps(readyRow()); gaps != nil {
		t.Fatalf("a ready row's gaps = %q, want none", gaps)
	}
	tapped := held
	tapped.Grade, tapped.Tapped = GradePass, true
	if row := Decide(tapped); !row.ReadyForTap {
		t.Fatalf("a tapped row with no plan row = %+v, want his tap to stand in for the plan row", row)
	}
}
