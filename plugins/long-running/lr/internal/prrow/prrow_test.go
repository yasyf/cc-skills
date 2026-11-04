package prrow

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	testRepo = "o/r"
	headA    = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	headB    = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	fullBody = "## Summary\n\nA user sees it.\n\n## Risk profile\n\nLow.\n\nThis carries plan step 1.\n"
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
		{"no plan", func(r *store.ReviewRow) { r.PlanRow = "" }, "no plan row"},
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

func TestATappedRowStaysReadySoTheGateCanLandItAndIsNotWaitingOnHim(t *testing.T) {
	row := readyRow()
	row.State, row.Tapped = StateTapped, true
	if row = Decide(row); !row.ReadyForTap || row.WaitsOn != tappedWaits {
		t.Fatalf("row = %+v", row)
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
	return machine.Facts{Number: number, Head: head, Title: "the title", Branch: fmt.Sprintf("branch-%d", number), Base: "dev", CI: machine.CISuccess, Approved: true, Approvers: []string{"a", "b"}, Mergeable: machine.MergeableClean, Queue: machine.QueueNone}
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
		Repo: testRepo, Number: 1, Title: "the title", Lane: "lane-7", Branch: "branch-1", Base: "dev", Parent: None, ParentState: None, Head: headA,
		ForUsers: "A user sees it.", PlanLine: "This carries plan step 1.", PlanRow: "This carries plan step 1. " + planChecked, Risk: "Low.\n\nThis carries plan step 1.",
		CI: CIGreen, Approvers: "a, b", Review: "approved by a, b", Grade: GradePass, GradeFindings: None, Mergeable: "clean", Queue: "none",
		Hold: None, SupersededBy: None, WaitsOn: onlyHim, ReadyForTap: true, State: StateOpen, TappedAt: NotYet, TappedBy: NotYet, LandedAt: NotYet,
	}
	got := row
	got.HeadAt, got.ReadHead, got.Description, got.LastRecord, got.WrittenAt = "", "", "", 0, ""
	if got != want {
		t.Fatalf("row =\n%+v\nwant\n%+v", got, want)
	}
	if row.Description == "" || row.WrittenAt == "" || row.LastRecord == 0 || row.ReadHead != headA {
		t.Fatalf("row = %+v, want description, written_at, last_record and read_head", row)
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

func TestTheBodyParserReadsEachField(t *testing.T) {
	var row store.ReviewRow
	ApplyBody(&row, "## Context\n\nWhy.\n\n## Summary\n\nFor a user.\n\n## Risk profile\n\nNone.\n\n## Design\n\nRow 12 of plan step 3 applies.\n\nThis goes live only after #1 is live.\n")
	if row.ForUsers != "For a user." || row.Risk != "None." || row.PlanLine != "Row 12 of plan step 3 applies." || !strings.HasPrefix(row.Description, "## Context") {
		t.Fatalf("row = %+v", row)
	}
	if row.UnsafeWait == "" {
		t.Fatalf("row = %+v, want the body's unsafe wait", row)
	}
}

func TestAPlanRowIsTheBodysLineOrTheFidelityPass(t *testing.T) {
	if got := planRow("", machine.FidelityPass); got != planChecked {
		t.Fatalf("no line with a pass = %q", got)
	}
	if got := planRow("", machine.FidelityFail); got != "" {
		t.Fatalf("no line with a fail = %q, want empty so the row is not ready", got)
	}
	if got := planRow("plan step 1", ""); got != "plan step 1" {
		t.Fatalf("line ungraded = %q", got)
	}
}

func TestARefusedTapShowsOnItsRowUntilALaterTapStands(t *testing.T) {
	const reason = "held, root:waits on the cutover"
	refused := Materialize(store.ReviewRow{Number: 1, Head: headA}, map[string]string{bus.TapRefusedFact: reason}, "", nil, "dev")
	if refused.TapRefused != reason || refused.Tapped {
		t.Fatalf("row = %+v", refused)
	}
	tapped := Materialize(store.ReviewRow{Number: 1, Head: headA}, map[string]string{bus.TapRefusedFact: reason, "tap": "tapped"}, "", nil, "dev")
	if tapped.TapRefused != "" || !tapped.Tapped {
		t.Fatalf("row = %+v", tapped)
	}
}

func TestATappedRowIsStillReadySoLandingCanRequireBoth(t *testing.T) {
	row := readyRow()
	row.State, row.Tapped = StateTapped, true
	if row = Decide(row); !row.ReadyForTap {
		t.Fatalf("row = %+v", row)
	}
}
