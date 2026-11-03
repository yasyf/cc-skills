package facts_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	repo  = "org/repo"
	head  = "e718a7434fa01ee9835f992a9a647a825350732c"
	head2 = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
	head3 = "ab0de038c1100e5c66e9ad3b21b1b8bb1b1ad0bb"
)

type recordedPR struct {
	number  int
	head    string
	login   string
	labels  []string
	commits []string
}

func graphQLPages(prs []recordedPR) string {
	var pages strings.Builder
	for start := 0; start == 0 || start < len(prs); start += 100 {
		page := prs[start:min(start+100, len(prs))]
		nodes := make([]map[string]any, 0, len(page))
		for _, pr := range page {
			labels, commits := make([]map[string]any, 0, len(pr.labels)), make([]map[string]any, 0, len(pr.commits))
			for _, name := range pr.labels {
				labels = append(labels, map[string]any{"name": name})
			}
			for _, oid := range pr.commits {
				commits = append(commits, map[string]any{"commit": map[string]any{"oid": oid}})
			}
			nodes = append(nodes, map[string]any{
				"number": pr.number, "headRefOid": pr.head, "author": map[string]any{"login": pr.login},
				"labels": map[string]any{"nodes": labels}, "commits": map[string]any{"nodes": commits},
			})
		}
		encoded, err := json.Marshal(map[string]any{"data": map[string]any{"repository": map[string]any{"pullRequests": map[string]any{"nodes": nodes}}}})
		if err != nil {
			panic(err)
		}
		pages.Write(encoded)
	}
	return pages.String()
}

func status(number int, overrides ...func(*ccx.PRStatus)) ccx.PRStatus {
	row := ccx.PRStatus{Number: number, Head: head, Base: "dev", State: "OPEN", Queue: "not queued", Mergeability: "notQueued", Verdict: "landable", CI: ccx.Check{State: "green"}, Approval: ccx.Check{State: "approved"}}
	for _, override := range overrides {
		override(&row)
	}
	return row
}

type harness struct {
	source facts.Source
	store  *store.Store
	runner *shelltest.Fake
}

func newHarness(t *testing.T, prs []recordedPR, rows ...ccx.PRStatus) harness {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	statuses, err := json.Marshal(rows)
	if err != nil {
		t.Fatal(err)
	}
	runner := &shelltest.Fake{}
	runner.Reply(graphQLPages(prs), "ccx", "vcs", "gh")
	runner.Reply(string(statuses), "ccx", "vcs", "pr", "status")
	cfg := config.Config{Repo: repo, Drive: "d", Checkout: "/checkouts/repo", Author: "drive-bot", Label: "drive"}
	a := app.New(io.Discard)
	a.Runner = runner
	return harness{facts.New(a, cfg, s), s, runner}
}

func (h harness) snapshot(t *testing.T) map[int]machine.Facts {
	t.Helper()
	rows, err := h.source.Snapshot(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	by := make(map[int]machine.Facts, len(rows))
	for _, row := range rows {
		by[row.Number] = row
	}
	return by
}

func (h harness) statusNumbers(t *testing.T) []string {
	t.Helper()
	for _, call := range h.runner.Calls() {
		if slices.Equal(call.Argv[:5], []string{"ccx", "vcs", "pr", "status", "--repo"}) {
			return call.Argv[slices.Index(call.Argv, "--json")+1:]
		}
	}
	t.Fatal("no status read")
	return nil
}

func post(t *testing.T, lane string, body bus.Body, to ...string) {
	t.Helper()
	if _, err := (bus.Bus{Drive: "d", Lane: lane}).Post(context.Background(), to, body); err != nil {
		t.Fatal(err)
	}
}

func TestEveryOpenPRByTheDriveAuthorOrLabelIsAdmitted(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 45757, head: head, login: "unregistered-user", labels: []string{"drive"}},
		{number: 1, head: head2, login: "drive-bot"},
		{number: 2, head: head3, login: "someone-else"},
	}, status(1), status(45757))

	h.snapshot(t)

	if got := h.statusNumbers(t); !slices.Equal(got, []string{"1", "45757"}) {
		t.Fatalf("status read = %v", got)
	}
}

func TestAPassReadsEachSourceOnceWhateverThePRCount(t *testing.T) {
	calls := map[int]int{}
	for _, n := range []int{1, 1000} {
		prs, rows := make([]recordedPR, 0, n), make([]ccx.PRStatus, 0, n)
		for i := 1; i <= n; i++ {
			sha := fmt.Sprintf("%040x", i)
			prs = append(prs, recordedPR{number: i, head: sha, login: "drive-bot", commits: []string{sha}})
			rows = append(rows, status(i, func(row *ccx.PRStatus) { row.Head = sha }))
		}
		h := newHarness(t, prs, rows...)

		if got := h.snapshot(t); len(got) != n {
			t.Fatalf("n=%d: snapshot has %d rows", n, len(got))
		}

		calls[n] = len(h.runner.Calls())
	}
	if calls[1] != 2 || calls[1000] != calls[1] {
		t.Fatalf("command runs at 1 and 1,000 open PRs = %d, %d, want 2 each", calls[1], calls[1000])
	}
}

func TestEveryReadRunsFromTheCheckoutNamingItsRepositoryAndCaller(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))

	h.snapshot(t)

	calls := h.runner.Calls()
	if len(calls) != 2 {
		t.Fatalf("calls = %+v", calls)
	}
	for _, call := range calls {
		if call.Dir != "/checkouts/repo" {
			t.Errorf("%v ran in %q", call.Argv, call.Dir)
		}
		if !slices.Contains(call.Env, "GH_REPO="+repo) || !slices.Contains(call.Env, "CCX_CALLER=lr-facts") {
			t.Errorf("%v env = %v", call.Argv, call.Env)
		}
	}
	graphql := calls[0].Argv
	if !slices.Equal(graphql[:10], []string{"ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql", "--paginate", "-f"}) || !slices.Contains(graphql, "owner=org") || !slices.Contains(graphql, "name=repo") {
		t.Errorf("GraphQL read = %v", graphql)
	}
}

func TestTheOpenListReadsPastOnePage(t *testing.T) {
	prs := make([]recordedPR, 0, 700)
	for i := 1; i <= 700; i++ {
		prs = append(prs, recordedPR{number: i, head: fmt.Sprintf("%040x", i), login: "drive-bot"})
	}
	h := newHarness(t, prs)

	h.snapshot(t)

	if got := h.statusNumbers(t); len(got) != 700 || got[699] != strconv.Itoa(700) {
		t.Fatalf("status read names %d PRs", len(got))
	}
}

func TestQueueCIAndMergeabilityComeFromTheOneStatusRead(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot"},
		{number: 2, head: head2, login: "drive-bot"},
		{number: 3, head: head3, login: "drive-bot"},
		{number: 4, head: fmt.Sprintf("%040x", 4), login: "drive-bot"},
		{number: 5, head: fmt.Sprintf("%040x", 5), login: "drive-bot"},
		{number: 6, head: fmt.Sprintf("%040x", 6), login: "drive-bot"},
		{number: 7, head: fmt.Sprintf("%040x", 7), login: "drive-bot"},
		{number: 8, head: fmt.Sprintf("%040x", 8), login: "drive-bot"},
		{number: 9, head: fmt.Sprintf("%040x", 9), login: "drive-bot"},
	},
		status(1, func(r *ccx.PRStatus) { r.Queue = "evicted" }),
		status(2, func(r *ccx.PRStatus) { r.Queue = "queued" }),
		status(3, func(r *ccx.PRStatus) { r.CI.State, r.Verdict = "red", "blocked:ci-red" }),
		status(4, func(r *ccx.PRStatus) { r.Verdict = "blocked:conflict" }),
		status(5, func(r *ccx.PRStatus) { r.Verdict = "blocked:mergeable-unknown" }),
		status(6, func(r *ccx.PRStatus) { r.Verdict = "blocked:needs-restack (parent landed),unapproved" }),
		status(7, func(r *ccx.PRStatus) { r.Queue, r.State, r.CI.State = "landed", "MERGED", "none" }),
		status(8, func(r *ccx.PRStatus) { r.Mergeability = "failed" }),
		status(9, func(r *ccx.PRStatus) { r.Mergeability = "cancelled" }),
	)

	by := h.snapshot(t)

	if by[1].Queue != machine.QueueEvicted || by[1].CI != machine.CISuccess {
		t.Errorf("#1 = %+v", by[1])
	}
	if by[2].Queue != machine.QueueQueued || by[2].QueuedHead != head {
		t.Errorf("#2 = %+v", by[2])
	}
	if by[3].CI != machine.CIFailure {
		t.Errorf("#3 = %+v", by[3])
	}
	if by[4].Mergeable != machine.MergeableConflict {
		t.Errorf("#4 = %+v", by[4])
	}
	if by[5].Mergeable != machine.MergeableUnknown {
		t.Errorf("#5 = %+v", by[5])
	}
	if by[6].Mergeable != machine.MergeableBehind {
		t.Errorf("#6 = %+v", by[6])
	}
	if !by[7].Merged || !by[7].Closed || by[7].CI != machine.CIPending || machine.Classify(by[7]).State != machine.StateLanded {
		t.Errorf("#7 = %+v", by[7])
	}
	if by[8].Queue != machine.QueueEvicted {
		t.Errorf("a failed Trunk entry = %+v, want evicted", by[8])
	}
	if by[9].Queue != machine.QueueNone {
		t.Errorf("a cancelled Trunk entry = %+v, want not queued", by[9])
	}
}

func TestAQueueStateCcxCouldNotReadIsNeverLandable(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}},
		status(1, func(r *ccx.PRStatus) { r.Queue, r.Verdict = "unknown", "blocked:queue-unknown" }))

	row := h.snapshot(t)[1]

	if row.Mergeable != machine.MergeableUnknown || row.Landable() {
		t.Fatalf("row = %+v", row)
	}
}

func TestARowCcxCouldNotReadIsSkippedThisPassAndNeverReadsAsClosed(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}, {number: 2, head: head2, login: "drive-bot"}},
		status(1), ccx.PRStatus{Number: 2, Queue: "unknown", Verdict: "unread"})

	by := h.snapshot(t)

	if _, read := by[2]; read || len(by) != 1 {
		t.Fatalf("rows = %+v, want #1 alone", by)
	}
}

func TestAnUnknownQueueStateIsAnUnreadPassNamingIt(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1, func(r *ccx.PRStatus) { r.Queue = "parked" }))

	_, err := h.source.Snapshot(context.Background())

	if err == nil || !strings.Contains(err.Error(), `#1: unknown queue state "parked"`) {
		t.Fatalf("err = %v", err)
	}
}

func TestABranchCarryingASiblingsHeadIsMarkedWhateverGitHubSaysItsBaseIs(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 45799, head: head, login: "drive-bot", commits: []string{head2, head3, head}},
		{number: 45501, head: head2, login: "drive-bot", commits: []string{head2}},
		{number: 45583, head: head3, login: "drive-bot", commits: []string{head3}},
	}, status(45501, func(r *ccx.PRStatus) { r.Head = head2 }), status(45583, func(r *ccx.PRStatus) { r.Head = head3 }), status(45799))

	by := h.snapshot(t)

	if !slices.Equal(by[45799].Carries, []int{45501, 45583}) || len(by[45501].Carries) != 0 || len(by[45583].Carries) != 0 {
		t.Fatalf("carries = %v, %v, %v", by[45799].Carries, by[45501].Carries, by[45583].Carries)
	}
	if machine.Classify(by[45799]).State != machine.StateNeedsRestack {
		t.Errorf("a carrying branch classified %v", machine.Classify(by[45799]).State)
	}
}

func TestBusRecordsLandAsFactRowsAndMoveTheCursor(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	post(t, "lane-7", bus.Head{PR: 1, Head: head, Lane: "lane-7"}, "supervisor")
	post(t, "finder-desk", bus.Grade{PR: 1, Head: head, Grader: "finder", Verdict: "ask"}, "supervisor")
	post(t, "root", bus.Verdict{PR: 1, Head: head, Verdict: "accept"}, "supervisor")
	post(t, "supervisor", bus.Tap{PR: 1, Head: head, By: "rule", Value: "tapped", Rule: "test-only"}, "supervisor")
	post(t, "fidelity-desk", bus.Grade{PR: 1, Head: head, Grader: "fidelity", Verdict: "pass"}, "supervisor")

	row := h.snapshot(t)[1]

	if row.Lane != "lane-7" || row.Finder != machine.FinderAsk || row.Fidelity != machine.FidelityPass || !row.Verdict || row.Tap != machine.TapTapped || !row.Exempt {
		t.Fatalf("row = %+v", row)
	}
	if !row.Cleared() || !row.Landable() {
		t.Fatalf("row is not landable: %+v", row)
	}
	cursor, err := h.store.Cursor(facts.BusCursor)
	if err != nil || cursor == 0 {
		t.Fatalf("cursor = %d, %v", cursor, err)
	}
}

func TestAHeadRecordsTheBoardsWordsAsFactsOnItsHead(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	post(t, "lane-7", bus.Head{PR: 1, Head: head, Lane: "lane-7", Title: "the title", Change: "what a user sees", Risk: "none"}, "supervisor")

	h.snapshot(t)

	stored, err := h.store.Facts(repo, 1, head)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"lane": "lane-7", "words-title": "the title", "words-change": "what a user sees", "words-risk": "none"}
	if !maps.Equal(stored, want) {
		t.Fatalf("facts = %+v, want %+v", stored, want)
	}
}

func TestAClearThatLandsAfterThePassReadTheBusAppliesNextPass(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	post(t, "fidelity-desk", bus.Grade{PR: 1, Head: head, Grader: "fidelity", Verdict: "pass"}, "supervisor")
	post(t, "finder-desk", bus.Grade{PR: 1, Head: head, Grader: "finder", Verdict: "ask"}, "supervisor")

	before := h.snapshot(t)[1]
	post(t, "root", bus.Verdict{PR: 1, Head: head, Verdict: "accept"}, "supervisor")
	after := h.snapshot(t)[1]

	if before.Cleared() || !after.Cleared() {
		t.Fatalf("cleared before %v, after %v", before.Cleared(), after.Cleared())
	}
}

func TestAHoldNamesWhoLiftsItAndALiftedOneHoldsNothing(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 45821, head: head, login: "drive-bot"},
		{number: 30388, head: head2, login: "drive-bot"},
		{number: 46038, head: head3, login: "drive-bot"},
		{number: 50000, head: fmt.Sprintf("%040x", 5), login: "drive-bot"},
	}, status(45821), status(30388, func(r *ccx.PRStatus) { r.Head = head2 }), status(46038, func(r *ccx.PRStatus) { r.Head = head3 }), status(50000))
	ctx := context.Background()
	hold := func(lane, text, pr string) client.Record {
		record, err := client.Post(ctx, client.Draft{Drive: "d", Lane: lane, Kind: "hold", Text: text, PRs: []string{pr}})
		if err != nil {
			t.Fatal(err)
		}
		return record
	}
	hold("root", "hold #45821 until the audit", repo+"#45821")
	hold("owner", "hold #30388 for me", "30388")
	lifted := hold("root", "hold #46038 for now", repo+"#46038")
	if _, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "root", Kind: "lift", Text: "lift #46038", Re: lifted.Seq, PRs: []string{repo + "#46038"}}); err != nil {
		t.Fatal(err)
	}
	hold("root", "hold #50000 in the other repository", "other/repo#50000")

	by := h.snapshot(t)

	if by[45821].Hold != machine.MoverRoot || by[30388].Hold != machine.MoverOwner || by[46038].Hold != "" || by[50000].Hold != "" {
		t.Fatalf("holds = %q, %q, %q, %q", by[45821].Hold, by[30388].Hold, by[46038].Hold, by[50000].Hold)
	}
}

func TestFactsBelongToAHeadSoANewPushStartsUngraded(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	at := time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)
	for kind, value := range map[string]string{"finder": "clear", "tap": "tapped"} {
		if _, err := h.store.PutFact(repo, 1, head2, kind, value, at); err != nil {
			t.Fatal(err)
		}
	}

	row := h.snapshot(t)[1]

	if row.Finder != "" || row.Tap != "" || row.Graded() {
		t.Fatalf("row = %+v", row)
	}
}

func TestAConditionalTapIsNotATapAndKeepsItsCondition(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 45470, head: head, login: "drive-bot"}}, status(45470))
	post(t, "owner-board", bus.Tap{PR: 45470, Head: head, By: "owner", Value: "tapped", Condition: "only if the finding is superseded"}, "supervisor")

	row := h.snapshot(t)[45470]

	if row.Tap != machine.TapConditional || row.Tapped() || row.Condition != "only if the finding is superseded" {
		t.Fatalf("row = %+v", row)
	}
}

func TestTheApprovingReviewComesFromTheStatusRead(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}, {number: 2, head: head2, login: "drive-bot"}},
		status(1), status(2, func(r *ccx.PRStatus) { r.Approval.State = "none" }))

	by := h.snapshot(t)

	if !by[1].Approved || by[2].Approved {
		t.Fatalf("approved = %v, %v", by[1].Approved, by[2].Approved)
	}
}

func TestAGradeTheFinderPostedAsAReviewLineCountsForTheFullHeadItAbbreviates(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot", commits: []string{head}}}, status(1))
	ctx := context.Background()
	if _, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "finder-desk", Kind: "review", Text: "finder: ok " + head + " luna finder over delta: no majors", PRs: []string{repo + "#1"}}); err != nil {
		t.Fatal(err)
	}
	fidelity, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "fidelity-desk-22", Kind: "review", Text: "fidelity: ok " + head[:7] + " the change stays in scope", PRs: []string{repo + "#1"}})
	if err != nil {
		t.Fatal(err)
	}

	row := h.snapshot(t)[1]

	if row.Finder != machine.FinderClear || row.Fidelity != machine.FidelityPass || !row.Cleared() {
		t.Fatalf("row = %+v", row)
	}
	if cursor, err := h.store.Cursor(bus.LegacyCursor); err != nil || cursor != fidelity.Seq {
		t.Fatalf("legacy cursor = %d, %v, want %d", cursor, err, fidelity.Seq)
	}
}

func TestAPRWithNoHeadRecordBelongsToTheLaneTheRegistryNames(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	if err := h.store.Own("lane-9", []int{1}); err != nil {
		t.Fatal(err)
	}

	if row := h.snapshot(t)[1]; row.Lane != "lane-9" {
		t.Fatalf("lane = %q", row.Lane)
	}
}

func TestAPRThatLeftTheOpenListWithAnOpenItemGetsItsTerminalRow(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}},
		status(1), status(44, func(r *ccx.PRStatus) { r.State, r.Queue = "MERGED", "landed" }))
	if _, err := h.store.OpenItem(store.NewItem{Kind: "verdict", Repo: repo, Number: 44, Head: head, Mover: machine.MoverLane, Deadline: time.Hour}, time.Now()); err != nil {
		t.Fatal(err)
	}

	by := h.snapshot(t)

	if got := h.statusNumbers(t); !slices.Equal(got, []string{"1", "44"}) {
		t.Fatalf("status read = %v", got)
	}
	if machine.Classify(by[44]).State != machine.StateLanded {
		t.Fatalf("#44 = %+v", by[44])
	}
}

func TestAFailedGitHubReadSkipsThePassAndLeavesNoRows(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	runner := &shelltest.Fake{}
	runner.Fail(1, "ccx: GitHub rate limit floor: graphql", "ccx", "vcs", "gh")
	a := app.New(io.Discard)
	a.Runner = runner
	source := facts.New(a, config.Config{Repo: repo, Drive: "d", Checkout: "/checkouts/repo", Author: "drive-bot", Label: "drive"}, s)

	rows, err := source.Snapshot(context.Background())

	var exit *shell.ExitError
	if !errors.As(err, &exit) || rows != nil {
		t.Fatalf("Snapshot = %+v, %v", rows, err)
	}
	if !strings.Contains(err.Error(), "read the open pull requests") {
		t.Fatalf("err = %v", err)
	}
}
