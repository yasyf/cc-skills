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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
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
	number    int
	head      string
	login     string
	labels    []string
	commits   []string
	rollup    string
	merge     string
	draft     bool
	diff      string
	body      string
	unopened  bool
	approvers []string
	reviews   []map[string]any
	state     string
	branch    string
	base      string
	queued    bool
	mergedBy  string
}

type ciRow struct {
	State   string   `json:"state"`
	Failing []string `json:"failing,omitempty"`
	Running int      `json:"running,omitempty"`
}

type statusRow struct {
	ccx.PRStatus
	CI ciRow `json:"ci"`
}

func memberReply(prs []recordedPR) string {
	repository := make(map[string]any, len(prs))
	for _, pr := range prs {
		var rollup any
		if pr.rollup != "" {
			rollup = map[string]any{"state": pr.rollup}
		}
		labels, commits, reviews := make([]map[string]any, 0, len(pr.labels)), make([]map[string]any, 0, len(pr.commits)), make([]map[string]any, 0, len(pr.approvers))
		for _, name := range pr.labels {
			labels = append(labels, map[string]any{"name": name})
		}
		for _, oid := range pr.commits {
			commits = append(commits, map[string]any{"commit": map[string]any{"oid": oid}})
		}
		for _, login := range pr.approvers {
			reviews = append(reviews, map[string]any{"state": "APPROVED", "author": map[string]any{"login": login}, "commit": map[string]any{"oid": pr.head}})
		}
		reviews = append(reviews, pr.reviews...)
		var mergedBy any
		if pr.mergedBy != "" {
			mergedBy = map[string]any{"login": pr.mergedBy}
		}
		state := pr.state
		if state == "" {
			state = "OPEN"
		}
		repository[fmt.Sprintf("r%d", pr.number)] = map[string]any{
			"number": pr.number, "state": state, "isDraft": pr.draft, "headRefOid": pr.head, "headRefName": pr.branch, "baseRefName": "dev", "body": pr.body, "mergedBy": mergedBy,
			"author": map[string]any{"login": pr.login}, "labels": map[string]any{"nodes": labels}, "commits": map[string]any{"nodes": commits}, "mergeable": pr.merge,
			"tip":     map[string]any{"nodes": []map[string]any{{"commit": map[string]any{"statusCheckRollup": rollup}}}},
			"reviews": map[string]any{"nodes": reviews},
		}
	}
	encoded, err := json.Marshal(map[string]any{"data": map[string]any{"repository": repository}})
	if err != nil {
		panic(err)
	}
	return string(encoded)
}

func status(number int, overrides ...func(*statusRow)) statusRow {
	row := statusRow{ccx.PRStatus{Number: number, Head: head, Base: "dev", State: "OPEN", Queue: "not queued", Mergeability: "notQueued", Verdict: "landable", Approval: ccx.Check{State: "approved"}}, ciRow{State: "green"}}
	for _, override := range overrides {
		override(&row)
	}
	return row
}

var landingConfig = config.Config{Repo: repo, Drive: "d", Checkout: "/checkouts/repo", Author: "drive-bot", Trunk: "dev", Movers: []string{config.MoverLanding}}

type harness struct {
	source facts.Source
	store  *store.Store
	runner *shelltest.Fake
}

const branchRules = `[{"type":"pull_request"},{"type":"required_status_checks","parameters":{"required_status_checks":[{"context":"buildkite/test"},{"context":"lint"}]}}]`

func newHarness(t *testing.T, prs []recordedPR, rows ...statusRow) harness {
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
	runner.Reply(memberReply(prs), "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql")
	runner.Reply(branchRules, "ccx", "vcs", "gh", "--max-age", "5m")
	runner.Reply(string(statuses), "ccx", "vcs", "pr", "status")
	runner.Reply("", "git", "fetch")
	for _, pr := range prs {
		runner.Reply("below-"+pr.head+"\n", "git", "merge-base", pr.head)
		runner.Reply(pr.diff, "git", "diff", "--no-color", "--no-ext-diff", "below-"+pr.head, pr.head)
	}
	seeded := make([]store.ReviewRow, 0, len(prs))
	for _, pr := range prs {
		if !pr.unopened {
			seeded = append(seeded, store.ReviewRow{Repo: repo, Number: pr.number, Head: pr.head, State: prrow.StateOpen})
		}
	}
	if err := s.PutReviewRows(seeded); err != nil {
		t.Fatal(err)
	}
	a := app.New(io.Discard)
	a.Runner = runner
	return harness{facts.New(a, landingConfig, s), s, runner}
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

func TestOnlyAPROpenedOnTheRecordIsRead(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 45757, head: head, login: "drive-bot"},
		{number: 1, head: head2, login: "drive-bot"},
		{number: 2, head: head3, login: "drive-bot", unopened: true},
	}, status(1), status(45757))

	h.snapshot(t)

	if got := h.statusNumbers(t); !slices.Equal(got, []string{"1", "45757"}) {
		t.Fatalf("status read = %v, want the rows only and never a PR no record opened", got)
	}
	for _, call := range h.runner.Calls() {
		if slices.ContainsFunc(call.Argv, func(arg string) bool { return strings.Contains(arg, "pullRequests(") }) {
			t.Fatalf("a read listed GitHub's open PRs: %v", call.Argv)
		}
	}
}

func TestAnEndedPRCostsNoStatusReadAndLeavesTheRowsWithItsState(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot"},
		{number: 2, head: head2, login: "drive-bot", state: "MERGED"},
		{number: 3, head: head3, login: "drive-bot", state: "CLOSED"},
		{number: 4, head: head, login: "drive-bot", state: "CLOSED", labels: []string{"trunk-merged"}},
		{number: 5, head: head, login: "drive-bot", state: "MERGED"},
	}, status(1), status(5, func(row *statusRow) { row.State = "MERGED" }))
	if _, err := h.store.OpenItem(store.NewItem{Kind: "tap", Repo: repo, Number: 5, Head: head, Mover: machine.MoverOwner, Deadline: time.Hour}, time.Now()); err != nil {
		t.Fatal(err)
	}

	by := h.snapshot(t)

	if got := h.statusNumbers(t); !slices.Equal(got, []string{"1", "5"}) {
		t.Fatalf("status read = %v, want the open PR and the one holding an item", got)
	}
	if _, drawn := by[2]; drawn {
		t.Fatalf("snapshot drew an ended PR, which would announce an old landing: %+v", by[2])
	}
	if !by[5].Merged {
		t.Fatalf("#5 = %+v, want its item's PR read as merged so the item closes", by[5])
	}
	snapshot := make([]machine.Facts, 0, len(by))
	for _, f := range by {
		snapshot = append(snapshot, f)
	}
	if err := h.source.WriteRows(context.Background(), snapshot); err != nil {
		t.Fatal(err)
	}
	for number, want := range map[int]string{1: prrow.StateOpen, 2: prrow.StateLanded, 3: prrow.StateClosed, 4: prrow.StateLanded} {
		row, known, err := h.store.ReviewRow(repo, number)
		if err != nil || !known || row.State != want {
			t.Errorf("#%d = %+v, %v, %v, want state %s", number, row, known, err, want)
		}
	}
	calls := len(h.runner.Calls())
	h.snapshot(t)
	if got := h.runner.Calls()[calls:]; !slices.ContainsFunc(got, func(call shell.Command) bool {
		return slices.Contains(call.Argv, "graphql") && strings.Contains(call.Argv[len(call.Argv)-1], "pullRequest(number:") && !strings.Contains(call.Argv[len(call.Argv)-1], "r2: pullRequest")
	}) {
		t.Fatalf("the next pass still read a landed row: %+v", got)
	}
}

func TestANumberGitHubCannotResolveEndsItsRowAndThePassGoesOn(t *testing.T) {
	h := newHarness(t, nil)
	if err := h.store.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: 1, Head: head, State: prrow.StateOpen}, {Repo: repo, Number: 99999, State: prrow.StateOpen}}); err != nil {
		t.Fatal(err)
	}
	statuses, err := json.Marshal([]statusRow{status(1)})
	if err != nil {
		t.Fatal(err)
	}
	partial := `{"data":{"repository":{"r99999":null,"r1":{"number":1,"state":"OPEN","headRefOid":"` + head + `"}}},"errors":[{"type":"NOT_FOUND","message":"Could not resolve"}]}`
	runner := &shelltest.Fake{}
	runner.FailOut(1, partial, "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql")
	runner.Reply(branchRules, "ccx", "vcs", "gh", "--max-age", "5m")
	runner.Reply(string(statuses), "ccx", "vcs", "pr", "status")
	a := app.New(io.Discard)
	a.Runner = runner
	source := facts.New(a, landingConfig, h.store)

	rows, err := source.Snapshot(context.Background())
	if err != nil {
		t.Fatalf("one number GitHub cannot resolve failed the whole pass: %v", err)
	}
	if len(rows) != 1 || rows[0].Number != 1 {
		t.Fatalf("rows = %+v, want only #1", rows)
	}
	if err := source.WriteRows(context.Background(), rows); err != nil {
		t.Fatal(err)
	}
	row, _, err := h.store.ReviewRow(repo, 99999)
	if err != nil || row.State != prrow.StateClosed {
		t.Fatalf("#99999 = %+v, %v, want closed so it stops being read", row, err)
	}
}

func TestAnotherErrorFromTheMemberReadStillFailsThePass(t *testing.T) {
	h := newHarness(t, nil)
	if err := h.store.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: 1, Head: head, State: prrow.StateOpen}}); err != nil {
		t.Fatal(err)
	}
	runner := &shelltest.Fake{}
	runner.FailOut(1, `{"data":null,"errors":[{"type":"RATE_LIMITED"}]}`, "ccx", "vcs", "gh", "--max-age", "0s")
	a := app.New(io.Discard)
	a.Runner = runner

	if _, err := facts.New(a, landingConfig, h.store).Snapshot(context.Background()); err == nil {
		t.Fatal("a rate limit read as a clean pass")
	}
}

func TestAnOpenedRecordMakesTheRow(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 7, head: head, login: "drive-bot", unopened: true}}, status(7))
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "opened", Text: "opened", PRs: []string{repo + "#7"}, Attrs: map[string]string{"repo": repo, "head": head}}); err != nil {
		t.Fatal(err)
	}

	by := h.snapshot(t)

	if by[7].Head != head || by[7].Lane != "lane" {
		t.Fatalf("#7 = %+v, want it read at its head under the lane that opened it", by[7])
	}
}

func TestAHostWithoutTheLandingMoverReadsTheRowsAndNeverGitHub(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	if _, err := client.Post(context.Background(), client.Draft{
		Drive: "d", Lane: "supervisor", Kind: "github", Text: "the title", PRs: []string{repo + "#1"},
		Attrs: map[string]string{"record": "pr.read", "repo": repo, "head": head, "state": "open", "base": "dev", "branch": "b", "ci": "green", "approvers": "a, b", "mergeable": "clean", "queue": "none"},
	}); err != nil {
		t.Fatal(err)
	}
	a := app.New(io.Discard)
	a.Runner = h.runner
	away := landingConfig
	away.Movers = []string{"lanes", "grading"}

	rows, err := facts.New(a, away, h.store).Snapshot(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if calls := h.runner.Calls(); len(calls) != 0 {
		t.Fatalf("it read GitHub: %+v", calls)
	}
	if len(rows) != 1 || rows[0].CI != machine.CISuccess || rows[0].Mergeable != machine.MergeableClean || !rows[0].Approved || rows[0].Title != "the title" {
		t.Fatalf("rows = %+v, want the landing host's read", rows)
	}
}

func TestAHostWithoutTheLandingMoverReadsATapCarriedToTheRowsHead(t *testing.T) {
	for _, tt := range []struct {
		name    string
		carried map[string]string
		want    machine.Tap
	}{
		{"the landing host carried it to this head", map[string]string{"carried_by": "same-diff", "tap_from": "1111111111"}, machine.TapTapped},
		{"the landing host carried nothing", nil, ""},
	} {
		t.Run(tt.name, func(t *testing.T) {
			h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
			attrs := map[string]string{"record": "pr.read", "repo": repo, "head": head, "state": "open", "base": "dev", "branch": "b", "ci": "green", "approvers": "a, b", "mergeable": "clean", "queue": "none"}
			maps.Copy(attrs, tt.carried)
			if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "supervisor", Kind: "github", Text: "the title", PRs: []string{repo + "#1"}, Attrs: attrs}); err != nil {
				t.Fatal(err)
			}
			a := app.New(io.Discard)
			a.Runner = h.runner
			away := landingConfig
			away.Movers = []string{"lanes", "grading"}

			rows, err := facts.New(a, away, h.store).Snapshot(context.Background())
			if err != nil {
				t.Fatal(err)
			}

			if len(rows) != 1 || rows[0].Tap != tt.want {
				t.Fatalf("rows = %+v, want tap %q", rows, tt.want)
			}
		})
	}
}

func TestAHostWithoutTheLandingMoverActsOnNoRowTheLandingHostHasNotRead(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}})
	a := app.New(io.Discard)
	a.Runner = h.runner
	away := landingConfig
	away.Movers = []string{"lanes", "grading"}

	rows, err := facts.New(a, away, h.store).Snapshot(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if len(rows) != 0 {
		t.Fatalf("rows = %+v, want none: an unread row may already have merged", rows)
	}
}

func TestACutoverHoldComesThroughWithItsReason(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 49428, head: head, login: "drive-bot"}, {number: 49307, head: head2, login: "drive-bot"}}, status(49428), status(49307))
	for lane, text := range map[string]string{
		"cutover-deletion-sweep-4": "Hold #49428: deletes the run takeover; held until plan 12.0 reads true",
		"root":                     "hold #49307 until the old-route warn lines have stopped",
	} {
		number := "49428"
		if lane == "root" {
			number = "49307"
		}
		if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: "hold", Text: text, PRs: []string{number}}); err != nil {
			t.Fatal(err)
		}
	}

	h.snapshot(t)

	cutover, _, err := h.store.ReviewRow(repo, 49428)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(cutover.Hold, "cutover:deletes the run takeover; held until plan 12.0 reads true (record #") || cutover.ReadyForTap || !strings.Contains(cutover.WaitsOn, "cutover:") {
		t.Fatalf("cutover row = %+v", cutover)
	}
	root, _, err := h.store.ReviewRow(repo, 49307)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(root.Hold, "root:until the old-route warn lines have stopped (record #") {
		t.Fatalf("root hold = %q", root.Hold)
	}
}

func TestBackfillPostsOneOpenedRecordPerOpenPRItFindsThroughOwnersAndRows(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot", branch: "one", body: "## Summary\n\nA user sees it.\n", unopened: true},
		{number: 2, head: head2, login: "drive-bot", state: "MERGED", unopened: true},
		{number: 3, head: head3, login: "drive-bot", branch: "three", body: "## Summary\n\nThree.\n"},
	})
	if err := h.store.Own("lane-7", []int{1, 2}); err != nil {
		t.Fatal(err)
	}
	if err := h.store.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: 3, Head: head3, Lane: "lane-9", State: prrow.StateOpen}}); err != nil {
		t.Fatal(err)
	}

	posted, err := h.source.Backfill(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if posted != 2 {
		t.Fatalf("posted = %d, want #1 and #3 and never a merged PR", posted)
	}

	h.snapshot(t)

	one, known, err := h.store.ReviewRow(repo, 1)
	if err != nil || !known {
		t.Fatalf("#1 row = %+v, %v, %v", one, known, err)
	}
	if one.ForUsers != "A user sees it." || one.Lane != "lane" || one.Branch != "one" || one.Head != head {
		t.Fatalf("#1 = %+v, want its body's fields, its owner and its head", one)
	}
	if _, known, _ := h.store.ReviewRow(repo, 2); known {
		t.Fatal("a merged PR got a row")
	}
}

func TestAPRAnotherPersonWroteReadsWithTheirLoginWhetherARowOrAnOpenItemKeepsItAndTheDrivesOwnWithNone(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 47613, head: head, login: "teammate", unopened: true},
		{number: 47615, head: head, login: "teammate"},
		{number: 1, head: head2, login: "drive-bot"},
	}, status(1), status(47613), status(47615))
	if _, err := h.store.OpenItem(store.NewItem{Kind: "tap", Repo: repo, Number: 47613, Head: head, Mover: machine.MoverOwner, Deadline: time.Hour}, time.Now()); err != nil {
		t.Fatal(err)
	}

	authors := map[int]string{}
	for _, f := range h.snapshot(t) {
		authors[f.Number] = f.ForeignAuthor
	}
	if want := map[int]string{1: "", 47613: "teammate", 47615: "teammate"}; !maps.Equal(authors, want) {
		t.Fatalf("foreign authors = %v, want %v", authors, want)
	}
}

func TestTheMemberReadNamesTheWaitEachDrivePRsBodyLeavesUnsafeOnDev(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot", body: "## Risk\n\nThis goes live only after #46014 is live on every cluster.\n"},
		{number: 2, head: head2, login: "drive-bot", body: "Nothing calls this yet. It goes live only after #46014 is live.\n"},
	}, status(1), status(2))

	got := h.snapshot(t)

	if got[1].UnsafeWait != "This goes live only after #46014 is live on every cluster" || got[2].UnsafeWait != "" {
		t.Fatalf("waits = %q, %q", got[1].UnsafeWait, got[2].UnsafeWait)
	}
}

func TestADraftIsReadAsADraft(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot", draft: true},
		{number: 2, head: head2, login: "drive-bot"},
	}, status(1), status(2))

	drafts := map[int]bool{}
	for _, f := range h.snapshot(t) {
		drafts[f.Number] = f.Draft
	}
	if !drafts[1] || drafts[2] {
		t.Fatalf("drafts = %v, want only #1", drafts)
	}
}

func TestAPassesGitHubCallsGrowOnlyByPageWhateverThePRCount(t *testing.T) {
	calls := map[int]int{}
	for _, n := range []int{1, 1000} {
		prs, rows := make([]recordedPR, 0, n), make([]statusRow, 0, n)
		for i := 1; i <= n; i++ {
			sha := fmt.Sprintf("%040x", i)
			prs = append(prs, recordedPR{number: i, head: sha, login: "drive-bot", commits: []string{sha}})
			rows = append(rows, status(i, func(row *statusRow) { row.Head = sha }))
		}
		h := newHarness(t, prs, rows...)

		if got := h.snapshot(t); len(got) != n {
			t.Fatalf("n=%d: snapshot has %d rows", n, len(got))
		}

		calls[n] = len(h.runner.Calls())
	}
	if calls[1] != 4 || calls[1000] != 4+39 {
		t.Fatalf("command runs at 1 and 1,000 PRs = %d, %d, want 4 and 43: one status read, one rules read, one search, one read per page of 25", calls[1], calls[1000])
	}
}

func TestEachLandingsMergerRidesThePageThePassAlreadyReads(t *testing.T) {
	calls := map[string]int{}
	for _, n := range []int{1, 1000} {
		for _, state := range []string{"OPEN", "MERGED"} {
			prs, rows := make([]recordedPR, 0, n), make([]statusRow, 0, n)
			for i := 1; i <= n; i++ {
				sha := fmt.Sprintf("%040x", i)
				pr := recordedPR{number: i, head: sha, login: "drive-bot", commits: []string{sha}, state: state}
				if state == "MERGED" {
					pr.mergedBy = "someone"
				}
				prs = append(prs, pr)
				rows = append(rows, status(i, func(row *statusRow) { row.Head, row.State = sha, state }))
			}
			h := newHarness(t, prs, rows...)
			for i := 1; i <= n; i++ {
				if _, err := h.store.OpenItem(store.NewItem{Kind: "tap", Repo: repo, Number: i, Head: fmt.Sprintf("%040x", i), Mover: machine.MoverOwner, Deadline: time.Hour}, time.Now()); err != nil {
					t.Fatal(err)
				}
			}

			got := h.snapshot(t)

			for i := 1; i <= n; i++ {
				if want := prs[i-1].mergedBy; got[i].MergedBy != want {
					t.Fatalf("n=%d #%d merged by %q, want %q", n, i, got[i].MergedBy, want)
				}
			}
			calls[fmt.Sprintf("%d %s", n, state)] = len(h.runner.Calls())
		}
	}
	if calls["1 MERGED"] != calls["1 OPEN"] || calls["1000 MERGED"] != calls["1000 OPEN"] {
		t.Fatalf("command runs = %v, want the merged pass to cost what the open pass does at 1 and 1,000 PRs, its merger read on the member page", calls)
	}
}

func TestAQueueEntryTheDriveDidNotEnqueueAtOrBelowItReadsAsAPersons(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot", branch: "bottom"},
		{number: 2, head: head2, login: "drive-bot", branch: "above"},
		{number: 3, head: head3, login: "drive-bot", branch: "alone"},
	},
		status(1, func(r *statusRow) { r.Queue = "queued" }),
		status(2, func(r *statusRow) { r.Head, r.Base, r.Queue = head2, "bottom", "queued" }),
		status(3, func(r *statusRow) { r.Head, r.Queue = head3, "queued" }),
	)
	if _, err := h.store.PutFact(repo, 1, head, "enqueued", "exit 0: #1 QUEUED", time.Now()); err != nil {
		t.Fatal(err)
	}

	by := h.snapshot(t)

	if by[1].PersonQueued || by[2].PersonQueued || !by[3].PersonQueued {
		t.Fatalf("person queued = %v, %v, %v; want only #3, since the drive enqueued #1 and its stack carried #2", by[1].PersonQueued, by[2].PersonQueued, by[3].PersonQueued)
	}
}

func TestTheMemberReadAsksByNumberInBatchesGitHubAnswers(t *testing.T) {
	const members = 60
	prs := make([]recordedPR, 0, members)
	for i := 1; i <= members; i++ {
		prs = append(prs, recordedPR{number: i, head: head, login: "drive-bot"})
	}
	h := newHarness(t, prs)

	h.snapshot(t)

	var batches, asked int
	for _, call := range h.runner.Calls() {
		if !slices.Contains(call.Argv, "graphql") || strings.Contains(call.Argv[len(call.Argv)-1], "search(") {
			continue
		}
		batches++
		named := strings.Count(call.Argv[len(call.Argv)-1], "pullRequest(number:")
		if named > 25 {
			t.Errorf("a batch of %d is past what GitHub answers before it times out", named)
		}
		asked += named
	}
	if batches != 3 || asked != members {
		t.Fatalf("%d batches naming %d PRs, want 3 naming %d", batches, asked, members)
	}
}

func TestEveryReadRunsFromTheCheckoutNamingItsRepositoryAndCaller(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))

	h.snapshot(t)

	calls := h.runner.Calls()
	if len(calls) != 4 {
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
	if !slices.Equal(graphql[:9], []string{"ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql", "-f"}) || !slices.Contains(graphql, "owner=org") || !slices.Contains(graphql, "name=repo") {
		t.Errorf("GraphQL read = %v", graphql)
	}
}

func TestTheMemberReadReadsPastOnePage(t *testing.T) {
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
		status(1, func(r *statusRow) { r.Queue = "evicted" }),
		status(2, func(r *statusRow) { r.Queue = "queued" }),
		status(3, func(r *statusRow) { r.CI.State, r.Verdict = "red", "blocked:ci-red" }),
		status(4, func(r *statusRow) { r.Verdict = "blocked:conflict" }),
		status(5, func(r *statusRow) { r.Verdict = "blocked:mergeable-unknown" }),
		status(6, func(r *statusRow) { r.Verdict = "blocked:needs-restack (parent landed),unapproved" }),
		status(7, func(r *statusRow) { r.Queue, r.State, r.CI.State = "landed", "MERGED", "none" }),
		status(8, func(r *statusRow) { r.Mergeability = "failed" }),
		status(9, func(r *statusRow) { r.Mergeability = "cancelled" }),
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

func TestTrunkRefusingAPRThatAStackEntryAboveItCarriesReadsAsQueued(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 48964, head: head, login: "drive-bot"}}, status(48964))
	at := time.Date(2026, 11, 3, 19, 0, 0, 0, time.UTC)
	for kind, value := range map[string]string{
		"lane":     "lane-a",
		"tap":      "tapped",
		"enqueued": "exit 5: submission not confirmed: ci trunk: Trunk POST /v2/merge-queues/d5e509f9-5b88-46fe-8276-5914bd90f234/pull-requests answered HTTP 422 (PR_NOT_ENQUEUEABLE): PR #49006, above this one in the same stack, is already in the merge queue as a stack that includes this pull request and will merge it. Wait for it to finish, or cancel #49006 first.; request dc5575a0-2486-93be-8441-446e84a16647; inspect Trunk before retrying",
	} {
		if _, err := h.store.PutFact(repo, 48964, head, kind, value, at); err != nil {
			t.Fatal(err)
		}
	}

	row := h.snapshot(t)[48964]

	if !row.Queued() || machine.Classify(row).Action != machine.ActionWatchQueue {
		t.Fatalf("row = %+v, verdict = %+v", row, machine.Classify(row))
	}
}

func TestAQueueStateCcxCouldNotReadIsNeverLandable(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}},
		status(1, func(r *statusRow) { r.Queue, r.Verdict = "unknown", "blocked:queue-unknown" }))

	row := h.snapshot(t)[1]

	if row.Mergeable != machine.MergeableUnknown || row.Landable() {
		t.Fatalf("row = %+v", row)
	}
}

func TestARowCcxCouldNotReadIsSkippedThisPassAndNeverReadsAsClosed(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}, {number: 2, head: head2, login: "drive-bot"}},
		status(1), statusRow{PRStatus: ccx.PRStatus{Number: 2, Queue: "unknown", Verdict: "unread"}})

	by := h.snapshot(t)

	if _, read := by[2]; read || len(by) != 1 {
		t.Fatalf("rows = %+v, want #1 alone", by)
	}
}

func TestAnUnknownQueueStateIsAnUnreadPassNamingIt(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1, func(r *statusRow) { r.Queue = "parked" }))

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
	}, status(45501, func(r *statusRow) { r.Head = head2 }), status(45583, func(r *statusRow) { r.Head = head3 }), status(45799))
	if err := h.store.Own("lane-9", []int{45799}); err != nil {
		t.Fatal(err)
	}

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
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "decide", PRs: []string{"1"}, Text: "finder-override #1 at " + head[:7] + ": 439849."}); err != nil {
		t.Fatal(err)
	}
	post(t, "supervisor", bus.Tap{PR: 1, Head: head, By: "rule", Value: "tapped", Rule: "test-only"}, "supervisor")
	post(t, "fidelity-desk", bus.Grade{PR: 1, Head: head, Grader: "fidelity", Verdict: "pass"}, "supervisor")

	row := h.snapshot(t)[1]

	if row.Lane != "lane-7" || row.Finder != machine.FinderAsk || row.Fidelity != machine.FidelityPass || !row.FinderOverride || row.Tap != machine.TapTapped || !row.Exempt {
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

func TestAHeadRecordStoresOnlyItsLaneOnItsHeadNeverASecondCopyOfThePRsWords(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	post(t, "lane-7", bus.Head{PR: 1, Head: head, Lane: "lane-7", Title: "the title"}, "supervisor")

	h.snapshot(t)

	stored, err := h.store.Facts(repo, 1, head)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"lane": "lane-7", "seen": "yes"}
	if !maps.Equal(stored, want) {
		t.Fatalf("facts = %+v, want %+v", stored, want)
	}
}

func TestAClearThatLandsAfterThePassReadTheBusAppliesNextPass(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	post(t, "fidelity-desk", bus.Grade{PR: 1, Head: head, Grader: "fidelity", Verdict: "pass"}, "supervisor")
	post(t, "finder-desk", bus.Grade{PR: 1, Head: head, Grader: "finder", Verdict: "ask"}, "supervisor")

	before := h.snapshot(t)[1]
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "decide", PRs: []string{"1"}, Text: "finder-override #1 at " + head[:7] + ": 439849."}); err != nil {
		t.Fatal(err)
	}
	after := h.snapshot(t)[1]

	if before.Cleared() || !after.Cleared() {
		t.Fatalf("cleared before %v, after %v", before.Cleared(), after.Cleared())
	}
}

func TestRedCountsOnlyARequiredCheck(t *testing.T) {
	red := func(r *statusRow, failing ...string) {
		r.CI.State, r.CI.Failing, r.Verdict = "red", failing, "blocked:ci-red"
	}
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}, {number: 2, head: head, login: "drive-bot"}, {number: 3, head: head, login: "drive-bot"}},
		status(1, func(r *statusRow) { red(r, "buildkite/test", "codecov") }),
		status(2, func(r *statusRow) { red(r, "codecov") }),
		status(3, func(r *statusRow) { red(r, "codecov"); r.CI.Running = 2 }),
	)

	by := h.snapshot(t)

	if by[1].CI != machine.CIFailure || by[2].CI != machine.CISuccess || by[3].CI != machine.CIPending {
		t.Fatalf("ci = %v, %v, %v", by[1].CI, by[2].CI, by[3].CI)
	}
}

func TestAnApprovalAtAHeadStandsAfterLaterReviewsOnOtherCommits(t *testing.T) {
	review := func(state, login, oid string) map[string]any {
		return map[string]any{"state": state, "author": map[string]any{"login": login}, "commit": map[string]any{"oid": oid}}
	}
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot", reviews: []map[string]any{
		review("APPROVED", "kept", head),
		review("APPROVED", "withdrew", head),
		review("CHANGES_REQUESTED", "withdrew", head),
		review("APPROVED", "kept", head2),
	}}}, status(1))

	if got := h.snapshot(t)[1].Approvers; !slices.Equal(got, []string{"kept"}) {
		t.Fatalf("approvers = %v, want only the reviewer whose last word at this head approved", got)
	}
}

func TestARestackCancelInTheQueueIsNotRed(t *testing.T) {
	queueRed := func(r *statusRow) {
		r.CI.State, r.CI.Failing, r.Verdict = "red", []string{"Trunk Merge Queue (dev)"}, "blocked:ci-red"
	}
	h := newHarness(t, []recordedPR{{number: 48317, head: head, login: "drive-bot", labels: []string{"trunk-cancelled"}}}, status(48317, queueRed))

	if got := h.snapshot(t)[48317].CI; got != machine.CISuccess {
		t.Errorf("a restack's cancel read %v", got)
	}
}

func TestAFailedQueueRunReentersOnceThenGoesToItsLane(t *testing.T) {
	queueRed := func(r *statusRow) {
		r.CI.State, r.CI.Failing, r.Verdict = "red", []string{"Trunk Merge Queue (dev)"}, "blocked:ci-red"
	}
	alsoRed := func(r *statusRow) {
		r.CI.State, r.CI.Failing, r.Verdict = "red", []string{"Trunk Merge Queue (dev)", "test"}, "blocked:ci-red"
	}
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot", labels: []string{"trunk-failed"}},
		{number: 2, head: head, login: "drive-bot", labels: []string{"trunk-failed"}},
		{number: 3, head: head, login: "drive-bot", labels: []string{"trunk-failed"}},
	}, status(1, queueRed), status(2, queueRed), status(3, alsoRed))
	if _, err := h.store.PutFact(repo, 2, head, "resubmitted", "1", time.Now()); err != nil {
		t.Fatal(err)
	}

	by := h.snapshot(t)

	if by[1].CI != machine.CISuccess || by[1].Queue != machine.QueueEvicted {
		t.Errorf("a first failed queue run read %v, %v, want it to re-enter", by[1].CI, by[1].Queue)
	}
	if by[2].CI != machine.CIFailure {
		t.Errorf("a second failed queue run read %v, want red for its lane", by[2].CI)
	}
	if by[3].CI != machine.CIFailure {
		t.Errorf("a failed queue run with its own red check read %v", by[3].CI)
	}
}

func TestPendingSettlesWhenTheRollupAtTheHeadReadsSuccess(t *testing.T) {
	pending := func(r *statusRow) { r.CI.State, r.Verdict = "pending", "blocked:ci-pending" }
	h := newHarness(t, []recordedPR{
		{number: 48252, head: head, login: "drive-bot", rollup: "SUCCESS"},
		{number: 2, head: head2, login: "drive-bot", rollup: "SUCCESS"},
		{number: 3, head: head, login: "drive-bot", rollup: "PENDING"},
		{number: 4, head: head, login: "drive-bot"},
	}, status(48252, pending), status(2, pending), status(3, pending), status(4, pending))

	by := h.snapshot(t)

	if by[48252].CI != machine.CISuccess {
		t.Errorf("a green rollup at the head read %v", by[48252].CI)
	}
	if by[2].CI != machine.CIPending {
		t.Errorf("a green rollup on a newer head settled an older status: %v", by[2].CI)
	}
	if by[3].CI != machine.CIPending || by[4].CI != machine.CIPending {
		t.Errorf("no green rollup read %v, %v", by[3].CI, by[4].CI)
	}
}

func TestGitHubSettlesAMergeabilityTheStatusReadCouldNot(t *testing.T) {
	unread := func(r *statusRow) { r.Verdict = "blocked:mergeable-unknown" }
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot", merge: "MERGEABLE"},
		{number: 2, head: head2, login: "drive-bot", merge: "MERGEABLE"},
		{number: 3, head: head, login: "drive-bot", merge: "UNKNOWN"},
	}, status(1, unread), status(2, unread), status(3, unread))

	by := h.snapshot(t)

	if by[1].Mergeable != machine.MergeableClean {
		t.Errorf("mergeable at the head read %v", by[1].Mergeable)
	}
	if by[2].Mergeable != machine.MergeableUnknown || by[3].Mergeable != machine.MergeableUnknown {
		t.Errorf("a newer head or an unread answer read %v, %v", by[2].Mergeable, by[3].Mergeable)
	}
}

func TestTrunkLabelsAnswerWhereTheStatusReadCannot(t *testing.T) {
	unknown := func(r *statusRow) { r.Queue = "unknown" }
	notGreen := func(r *statusRow) { r.CI.State = "pending" }
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot", labels: []string{"trunk-merged"}},
		{number: 2, head: head, login: "drive-bot", labels: []string{"trunk-testing"}},
		{number: 3, head: head, login: "drive-bot", labels: []string{"trunk-failed"}},
		{number: 4, head: head, login: "drive-bot", labels: []string{"trunk-cancelled"}},
		{number: 5, head: head, login: "drive-bot", labels: []string{"trunk-failed"}},
	},
		status(1, unknown), status(2, unknown), status(3, unknown, notGreen), status(4, notGreen), status(5))

	by := h.snapshot(t)

	if !by[1].Merged || machine.Classify(by[1]).State != machine.StateLanded {
		t.Errorf("a trunk-merged label read %+v", by[1])
	}
	if by[2].Queue != machine.QueueQueued || by[2].QueuedHead != head {
		t.Errorf("a trunk-testing label read %+v", by[2])
	}
	if by[3].Queue != machine.QueueEvicted || by[4].Queue != machine.QueueEvicted {
		t.Errorf("a fell label under a head not green read %v, %v", by[3].Queue, by[4].Queue)
	}
	if by[5].Queue != machine.QueueNone {
		t.Errorf("a fell label that outlived its head read %v on a green head", by[5].Queue)
	}
}

const (
	tapped45744 = "ddfd67265057900f2b400e70d7ba633948ff4e40"
	landed45744 = "23c548f7a846054a78609264606668e80b4f716b"
	ownDiff     = "diff --git a/list.go b/list.go\nindex 1111111..2222222 100644\n--- a/list.go\n+++ b/list.go\n@@ -10,3 +10,4 @@ func List() {\n \trows := read()\n+\trows = live(rows, \"a b\")\n \treturn rows\n"
)

var tappedAt = time.Date(2026, 11, 2, 9, 29, 0, 0, time.UTC)

func TestATapStandsOnlyAsTheOwnerLeftIt(t *testing.T) {
	const older = "a28ab62"
	for name, tc := range map[string]struct {
		put func(t *testing.T, s *store.Store)
		tap machine.Tap
	}{
		"tapped at this head": {
			put: func(t *testing.T, s *store.Store) {
				putFact(t, s, 1, older, "tap", "tapped", tappedAt)
				putFact(t, s, 1, head, "tap", "tapped", tappedAt.Add(time.Hour))
			},
			tap: machine.TapTapped,
		},
		"withdrawn at this head": {
			put: func(t *testing.T, s *store.Store) {
				putFact(t, s, 1, older, "tap", "tapped", tappedAt)
				putFact(t, s, 1, head, "tap", "withdrawn", tappedAt.Add(time.Hour))
			},
			tap: machine.TapWithdrawn,
		},
		"withdrawn at an older head, after the tap": {
			put: func(t *testing.T, s *store.Store) {
				putFact(t, s, 1, "b1b2b3b", "tap", "tapped", tappedAt)
				putFact(t, s, 1, older, "tap", "withdrawn", tappedAt.Add(time.Hour))
			},
		},
		"given with a condition at an older head": {
			put: func(t *testing.T, s *store.Store) {
				putFact(t, s, 1, older, "tap", "conditional", tappedAt)
				putFact(t, s, 1, older, "condition", "only if the finding is superseded", tappedAt)
			},
		},
		"never tapped": {put: func(*testing.T, *store.Store) {}},
	} {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot", diff: ownDiff}}, status(1))
			tc.put(t, h.store)

			row := h.snapshot(t)[1]

			if row.Tap != tc.tap || row.TapFrom != "" || row.CarriedBy != "" {
				t.Fatalf("tap = %q from %q by %q, want %q standing where he left it", row.Tap, row.TapFrom, row.CarriedBy, tc.tap)
			}
		})
	}
}

type goneRef struct {
	shell.Runner
	head string
}

func (g goneRef) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if slices.Contains(c.Argv, "fetch") && slices.Contains(c.Argv, g.head) {
		return shell.Result{Code: 128}, &shell.ExitError{Argv: c.Argv, Code: 128, Stderr: "fatal: couldn't find remote ref refs/heads/gone"}
	}
	return g.Runner.Run(ctx, c)
}

func TestATapWhoseDiffWillNotReadSkipsThatPRAndThePassMovesOn(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 1, head: head, login: "drive-bot", diff: ownDiff},
		{number: 2, head: head2, login: "drive-bot", diff: ownDiff},
	}, status(1), status(2, func(row *statusRow) { row.Head = head2 }))
	putFact(t, h.store, 1, head, "tap", "tapped", tappedAt)
	putFact(t, h.store, 2, head2, "tap", "tapped", tappedAt)
	a := app.New(io.Discard)
	a.Runner = goneRef{h.runner, head}

	rows, err := facts.New(a, landingConfig, h.store).Snapshot(context.Background())
	if err != nil {
		t.Fatalf("one PR's gone ref failed the whole pass: %v", err)
	}

	if len(rows) != 1 || rows[0].Number != 2 || rows[0].Tap != machine.TapTapped {
		t.Fatalf("rows = %+v, want only #2, tapped", rows)
	}
}

func TestAPortWhoseDiffWillNotReadStillCarriesItsBodyToItsRow(t *testing.T) {
	const body = "port of #50266\n\n## Summary\n\nA memory id search returns that memory."
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot", diff: ownDiff, body: body}}, status(1))
	putFact(t, h.store, 1, head, gates.PortOfAttr, "50266", tappedAt)
	a := app.New(io.Discard)
	a.Runner = goneRef{h.runner, head}
	source := facts.New(a, landingConfig, h.store)

	rows, err := source.Snapshot(context.Background())
	if err != nil || len(rows) != 0 {
		t.Fatalf("rows = %+v, %v, want the port left out of the classified pass", rows, err)
	}
	if err := source.FoldRows(context.Background()); err != nil {
		t.Fatal(err)
	}

	row, _, err := h.store.ReviewRow(repo, 1)
	if err != nil || row.ForUsers != "A memory id search returns that memory." || row.ReadHead != head {
		t.Fatalf("row = %+v, %v, want the body's Summary read at %s", row, err, head)
	}
}

func TestATapTheOldCarryLeftOnTheCurrentHeadStandsThereAndAnchorsTheNextHead(t *testing.T) {
	s := memory(t)
	putFact(t, s, 1, head2, "tap", "tapped", tappedAt)
	putFact(t, s, 1, head, "tap", "tapped", tappedAt.Add(time.Minute))

	live := on(t, s, recordedPR{number: 1, head: head, login: "drive-bot", diff: ownDiff}).snapshot(t)[1]
	next := on(t, s, recordedPR{number: 1, head: head3, login: "drive-bot", diff: strings.Replace(ownDiff, "-10,3 +10,4", "-40,3 +40,4", 1)}).snapshot(t)[1]

	if live.Tap != machine.TapTapped || live.TapFrom != "" {
		t.Fatalf("at go-live: tap %q from %q, want his tap standing at the head it is on", live.Tap, live.TapFrom)
	}
	if next.Tap != machine.TapTapped || next.TapFrom != head || next.CarriedBy != gates.BySameDiff {
		t.Fatalf("next head: tap %q from %q by %q, want carried from %s by the same diff", next.Tap, next.TapFrom, next.CarriedBy, head)
	}
}

func TestATapCarriesToANewHeadOnlyWhenItMeansWhatTheTappedHeadMeant(t *testing.T) {
	restacked := strings.NewReplacer("1111111..2222222", "3333333..4444444", "-10,3 +10,4", "-42,3 +44,4").Replace(ownDiff)
	respaced := strings.Replace(ownDiff, `"a b"`, `"ab"`, 1)
	judged := func(behavior string, since string) map[string]string {
		return map[string]string{"fidelity": "pass", gates.BehaviorFact: behavior, gates.SinceFact: since, gates.ChangedFact: "the list now drops archived chats"}
	}
	for name, tc := range map[string]struct {
		diff   string
		graded map[string]string
		by     string
		back   string
	}{
		"a restack whose own diff is the same":       {diff: restacked, by: gates.BySameDiff},
		"whitespace changed inside a line, ungraded": {diff: respaced},
		"fidelity judged the new head the same":      {diff: respaced, graded: judged(gates.BehaviorSame, head2), by: gates.ByFidelity},
		"fidelity judged the new head changed":       {diff: respaced, graded: judged(gates.BehaviorChanged, head2)},
		"fidelity judged it against another head":    {diff: respaced, graded: judged(gates.BehaviorSame, head3)},
		"a review fix its head record names":         {diff: respaced, graded: map[string]string{gates.DeltaFact: gates.DeltaReviewFix, gates.DeltaFromFact: head2}, by: gates.DeltaReviewFix},
		"a behavior change its head record names":    {diff: respaced, graded: map[string]string{gates.DeltaFact: gates.DeltaBehavior, gates.DeltaFromFact: head2, gates.DeltaWhyFact: "drops chats"}, back: "a behavior change at " + head[:7] + ": drops chats"},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			putFact(t, s, 1, head2, "tap", "tapped", tappedAt)
			on(t, s, recordedPR{number: 1, head: head2, login: "drive-bot", diff: ownDiff}).snapshot(t)
			for kind, value := range tc.graded {
				putFact(t, s, 1, head, kind, value, tappedAt.Add(time.Hour))
			}

			row := on(t, s, recordedPR{number: 1, head: head, login: "drive-bot", diff: tc.diff}).snapshot(t)[1]

			if row.TapFrom != head2 || row.CarriedBy != tc.by || row.Tapped() != (tc.by != "") {
				t.Fatalf("tap %q from %q by %q, want from %s by %q", row.Tap, row.TapFrom, row.CarriedBy, head2, tc.by)
			}
			if tc.back != "" && row.TapReturned != tc.back {
				t.Fatalf("returned %q, want %q", row.TapReturned, tc.back)
			}
		})
	}
}

func TestEachNewHeadIsJudgedAgainstTheHeadHeTappedNeverTheLastCarriedOne(t *testing.T) {
	s := memory(t)
	putFact(t, s, 1, head2, "tap", "tapped", tappedAt)
	on(t, s, recordedPR{number: 1, head: head2, login: "drive-bot", diff: ownDiff}).snapshot(t)
	putFact(t, s, 1, head, "fidelity", "pass", tappedAt.Add(time.Hour))
	putFact(t, s, 1, head, gates.BehaviorFact, gates.BehaviorSame, tappedAt.Add(time.Hour))
	putFact(t, s, 1, head, gates.SinceFact, head2, tappedAt.Add(time.Hour))
	carried := on(t, s, recordedPR{number: 1, head: head, login: "drive-bot", diff: ownDiff + "+\tlog(rows)\n"}).snapshot(t)[1]

	next := on(t, s, recordedPR{number: 1, head: head3, login: "drive-bot", diff: ownDiff + "+\tlog(rows)\n"}).snapshot(t)[1]

	if carried.CarriedBy != gates.ByFidelity {
		t.Fatalf("first new head carried by %q, want fidelity", carried.CarriedBy)
	}
	if next.Tapped() || next.TapFrom != head2 {
		t.Fatalf("second new head: tap %q from %q, want it judged against %s, so the same diff as the carried head proves nothing", next.Tap, next.TapFrom, head2)
	}
}

const (
	devBefore = "0dd0dd0dd0dd0dd0dd0dd0dd0dd0dd0dd0dd0dd0"
	squashed  = "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a"
	lowerDiff = "diff --git a/read.go b/read.go\nindex 3333333..4444444 100644\n--- a/read.go\n+++ b/read.go\n@@ -1,2 +1,3 @@\n package list\n+var archived = true\n"
)

type replayedStack struct {
	shell.Runner
	own string
}

func (r replayedStack) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	switch {
	case slices.Equal(c.Argv[:2], []string{"git", "merge-base"}) && slices.Contains(c.Argv, "refs/lr/diff/dev"):
		return shell.Result{Stdout: squashed + "\n"}, nil
	case slices.Equal(c.Argv[:2], []string{"git", "merge-base"}):
		return shell.Result{Stdout: devBefore + "\n"}, nil
	case slices.Equal(c.Argv[:2], []string{"git", "diff"}) && slices.Contains(c.Argv, squashed):
		return shell.Result{Stdout: r.own}, nil
	case slices.Equal(c.Argv[:2], []string{"git", "diff"}):
		return shell.Result{Stdout: lowerDiff + r.own}, nil
	}
	return r.Runner.Run(ctx, c)
}

func TestAStackReplayBelowItCarriesHisTapAndAGoOnlyWhenItsOwnPatchIsTheSame(t *testing.T) {
	for name, tc := range map[string]struct {
		given   string
		own     string
		carries bool
	}{
		"a patch-identical replay carries his tap": {given: "tap", own: ownDiff, carries: true},
		"a patch-identical replay carries the go":  {given: "exempt", own: ownDiff, carries: true},
		"a changed patch carries no tap":           {given: "tap", own: ownDiff + "+\tlog(rows)\n"},
		"a changed patch carries no go":            {given: "exempt", own: ownDiff + "+\tlog(rows)\n"},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			value := map[string]string{"tap": "tapped", "exempt": "regression"}[tc.given]
			putFact(t, s, 2, head2, tc.given, value, tappedAt)
			putFact(t, s, 2, head2, "enqueued", "exit 0: #2 QUEUED", tappedAt)
			on(t, s, recordedPR{number: 2, head: head2, login: "drive-bot", base: "lower", queued: true, diff: ownDiff}).snapshot(t)
			putFact(t, s, 2, head, "lane", "go-lane", tappedAt.Add(time.Hour))
			h := on(t, s, recordedPR{number: 2, head: head, login: "drive-bot", base: "lower", queued: true})
			h.source.Runner = replayedStack{h.runner, tc.own}

			row := h.snapshot(t)[2]

			if row.Tapped() != tc.carries {
				t.Fatalf("tap %q from %q by %q, exempt %v: want carried %v from %s", row.Tap, row.TapFrom, row.CarriedBy, row.Exempt, tc.carries, head2)
			}
			if verdict := machine.Classify(row); (verdict.Action == machine.ActionDequeue) == tc.carries {
				t.Fatalf("verdict = %+v, want a dequeue only when nothing carries", verdict)
			}
		})
	}
}

func TestRetroRow47ContentPushedOnto45744AfterHisTapGoesBackToHimInsteadOfLanding(t *testing.T) {
	s := memory(t)
	putFact(t, s, 45744, tapped45744[:12], "tap", "tapped", time.Date(2026, 10, 31, 1, 50, 0, 0, time.UTC))
	on(t, s, recordedPR{number: 45744, head: tapped45744, login: "drive-bot", diff: ownDiff}).snapshot(t)
	putFact(t, s, 45744, landed45744, "lane", "sandsql-journal-next-2", time.Date(2026, 10, 31, 2, 41, 45, 0, time.UTC))
	h := on(t, s, recordedPR{number: 45744, head: landed45744, login: "drive-bot", diff: ownDiff + "+\tdropRacyTick()\n"})
	review(t, "finder-desk", "finder: ok "+landed45744+" no majors", 45744)
	review(t, "fidelity-pipeline", "fidelity: ok "+landed45744+" stays in scope", 45744)
	review(t, "fidelity-pipeline", "behavior: changed "+landed45744+" since "+tapped45744[:12]+": drops the racy-tick re-read and finds an evicted chat's team another way", 45744)

	drawLandable(t, s, 45744, landed45744)
	row := h.snapshot(t)[45744]

	if row.Tapped() || row.TapFrom != tapped45744[:12] {
		t.Fatalf("tap %q from %q, want his tap at %s left behind", row.Tap, row.TapFrom, tapped45744[:12])
	}
	if verdict := machine.Classify(row); verdict.State != machine.StateAwaitingTap || verdict.Mover != machine.MoverOwner {
		t.Fatalf("verdict = %+v, want the PR back waiting on him", verdict)
	}
	if said := stored(t, s, 45744, landed45744)[gates.ChangedFact]; said != "drops the racy-tick re-read and finds an evicted chat's team another way" {
		t.Fatalf("what changed = %q", said)
	}
}

func TestRetroRow98ThreeNewHeadsWithNoVerdictAtTheirVersionWaitOnHimWhileTheSameDiffLands(t *testing.T) {
	s := memory(t)
	prs := []int{1, 2, 3, 4}
	tapped := func(number int) string { return strings.Repeat(strconv.Itoa(number), 40) }
	moved := func(number int) string { return strings.Repeat("a"+strconv.Itoa(number), 20) }
	before, after := make([]recordedPR, 0, len(prs)), make([]recordedPR, 0, len(prs))
	for _, number := range prs {
		putFact(t, s, number, tapped(number), "tap", "tapped", tappedAt)
		before = append(before, recordedPR{number: number, head: tapped(number), login: "drive-bot", diff: ownDiff})
	}
	onAll(t, s, before).snapshot(t)
	for _, number := range prs {
		diff := ownDiff + fmt.Sprintf("+\tchange%d()\n", number)
		if number == 4 {
			diff = ownDiff
		}
		for kind, value := range map[string]string{"lane": "lane-7", "finder": "clear", "fidelity": "pass"} {
			putFact(t, s, number, moved(number), kind, value, tappedAt.Add(time.Hour))
		}
		after = append(after, recordedPR{number: number, head: moved(number), login: "drive-bot", diff: diff})
	}
	for _, number := range prs {
		drawLandable(t, s, number, moved(number))
	}

	rows := onAll(t, s, after).snapshot(t)

	for _, number := range prs[:3] {
		if verdict := machine.Classify(rows[number]); rows[number].Tapped() || verdict.Action != machine.ActionTap {
			t.Fatalf("#%d: tap %q, verdict %+v, want it waiting on his tap", number, rows[number].Tap, verdict)
		}
	}
	if verdict := machine.Classify(rows[4]); rows[4].CarriedBy != gates.BySameDiff || verdict.Action != machine.ActionEnqueue {
		t.Fatalf("#4 carried by %q, verdict %+v, want the same diff to land on his tap", rows[4].CarriedBy, verdict)
	}
}

func memory(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func on(t *testing.T, s *store.Store, pr recordedPR) harness {
	t.Helper()
	return onAll(t, s, []recordedPR{pr})
}

func onAll(t *testing.T, s *store.Store, prs []recordedPR) harness {
	t.Helper()
	rows := make([]statusRow, 0, len(prs))
	for _, pr := range prs {
		rows = append(rows, status(pr.number, func(row *statusRow) {
			row.Head = pr.head
			if pr.base != "" {
				row.Base = pr.base
			}
			if pr.queued {
				row.Queue = "queued"
			}
		}))
	}
	for _, pr := range prs {
		if _, known, err := s.ReviewRow(repo, pr.number); err != nil {
			t.Fatal(err)
		} else if !known && !pr.unopened {
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: pr.number, Head: pr.head, State: prrow.StateOpen}}); err != nil {
				t.Fatal(err)
			}
		}
	}
	h := newHarness(t, prs, rows...)
	h.source.Store, h.store = s, s
	return h
}

func putFact(t *testing.T, s *store.Store, number int, at, kind, value string, posted time.Time) {
	t.Helper()
	if _, err := s.PutFact(repo, number, at, kind, value, posted); err != nil {
		t.Fatal(err)
	}
}

func stored(t *testing.T, s *store.Store, number int, at string) map[string]string {
	t.Helper()
	found, err := s.Facts(repo, number, at)
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func review(t *testing.T, lane, text string, number int) {
	t.Helper()
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: "review", Text: text, PRs: []string{fmt.Sprintf("%s#%d", repo, number)}}); err != nil {
		t.Fatal(err)
	}
}

func hold(t *testing.T, lane, text, pr string) client.Record {
	t.Helper()
	record, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: "hold", Text: text, PRs: []string{pr}})
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func goRecord(t *testing.T, lane, text string) client.Record {
	t.Helper()
	record, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: "go", Text: text})
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func TestALaterRootGoLiftsARootHoldAtAnyHeadSoARestackDoesNotRestallThePR(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 48401, head: head, login: "drive-bot"}, {number: 2, head: head, login: "drive-bot"}}, status(48401), status(2))
	hold(t, "root", "hold #48401 until the audit", repo+"#48401")
	goRecord(t, "root", "go #48401 at e718a74")
	hold(t, "root", "hold #2 for now", repo+"#2")
	goRecord(t, "root", "go #2 at aa11bb2")

	by := h.snapshot(t)

	if by[48401].Hold != "" || by[2].Hold != "" {
		t.Fatalf("holds = %q, %q after a later go", by[48401].Hold, by[2].Hold)
	}
}

func TestAGoLanesExemptGoLeavesARootHoldStanding(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	h.source.GoLanes = []string{"landing-desk"}
	hold(t, "root", "hold #1 for now", repo+"#1")
	goRecord(t, "landing-desk-3", "go #1 at "+head+" on green [exempt: owner-pick]")

	row := h.snapshot(t)[1]

	if row.Hold != machine.MoverRoot || !row.Exempt {
		t.Fatalf("hold = %q, exempt = %v; want the coordinator's hold and the exempt class", row.Hold, row.Exempt)
	}
}

func TestAWithdrawnExemptGoStopsCountingAndALaterGoCountsAgain(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	h.source.GoLanes = []string{"landing-desk"}
	exempt := "go #1 at " + head + " on green [exempt: owner-pick]"
	first := goRecord(t, "landing-desk-3", exempt)
	if !h.snapshot(t)[1].Exempt {
		t.Fatal("the go marks the PR exempt")
	}

	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "withdraw", Text: "open card", Re: first.Seq}); err != nil {
		t.Fatal(err)
	}
	if h.snapshot(t)[1].Exempt {
		t.Fatal("a withdrawn go still marks the PR exempt")
	}

	time.Sleep(time.Second)
	goRecord(t, "landing-desk-3", exempt+" after the card closed")
	if !h.snapshot(t)[1].Exempt {
		t.Fatal("a later go must mark it exempt again")
	}
}

func TestAWithdrawalFromALaneWithNoSayLeavesTheExemptGo(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	first := goRecord(t, "root", "go #1 at "+head+" on green [exempt: owner-pick]")
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "withdraw", Text: "not mine", Re: first.Seq}); err != nil {
		t.Fatal(err)
	}

	if !h.snapshot(t)[1].Exempt {
		t.Fatal("a lane outside root and the go lanes cleared an exempt go")
	}
}

func TestAHoldStandsAgainstEveryGoThatIsNotALaterLiveRootGo(t *testing.T) {
	for name, setup := range map[string]func(t *testing.T){
		"a go before the hold": func(t *testing.T) {
			goRecord(t, "root", "go #1 at e718a74")
			hold(t, "root", "hold #1 after all", repo+"#1")
		},
		"a go by a lane": func(t *testing.T) {
			hold(t, "root", "hold #1 for now", repo+"#1")
			goRecord(t, "lane-7", "go #1 at e718a74")
		},
		"the landing runner's go on a tap": func(t *testing.T) {
			hold(t, "root", "hold #1 for now", repo+"#1")
			goRecord(t, "desk-runner", "go #1 at e718a74")
		},
		"a go naming another PR": func(t *testing.T) {
			hold(t, "root", "hold #1 for now", repo+"#1")
			goRecord(t, "root", "go #2 at e718a74")
		},
		"a withdrawn go": func(t *testing.T) {
			hold(t, "root", "hold #1 for now", repo+"#1")
			g := goRecord(t, "root", "go #1 at e718a74")
			if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "withdraw", Text: "not yet", Re: g.Seq}); err != nil {
				t.Fatal(err)
			}
		},
	} {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
			setup(t)

			if row := h.snapshot(t)[1]; row.Hold != machine.MoverRoot {
				t.Fatalf("hold = %q, want the coordinator", row.Hold)
			}
		})
	}
}

func TestARootGoNeverLiftsTheOwnersHoldAndTheRowNamesTheOwnersRecord(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	owner := hold(t, "owner", "hold #1 for me", repo+"#1")
	hold(t, "root", "hold #1 too", repo+"#1")
	goRecord(t, "root", "go #1 at e718a74")

	if row := h.snapshot(t)[1]; row.Hold != machine.MoverOwner || row.HoldSeq != owner.Seq {
		t.Fatalf("hold = %q at #%d, want owner at #%d", row.Hold, row.HoldSeq, owner.Seq)
	}
}

func TestAHoldThatLiftsAnEarlierHoldHoldsNothingAndOneThatAnswersAnAskStillHolds(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}, {number: 2, head: head2, login: "drive-bot"}}, status(1), status(2))
	first := hold(t, "root", "hold #1 for now", repo+"#1")
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "hold", Text: "lift: the rework is done", PRs: []string{repo + "#1"}, Resolves: first.Seq}); err != nil {
		t.Fatal(err)
	}
	ask, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "ask", Text: "should #2 wait?"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "hold", Text: "yes, it waits", PRs: []string{repo + "#2"}, Resolves: ask.Seq}); err != nil {
		t.Fatal(err)
	}

	by := h.snapshot(t)

	if by[1].Hold != "" || by[2].Hold != machine.MoverRoot {
		t.Fatalf("holds = %q, %q", by[1].Hold, by[2].Hold)
	}
}

func TestAHoldHoldsEveryPRItWasPostedWithWhateverItsTextLeadsWith(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}, {number: 2, head: head2, login: "drive-bot"}},
		status(1), status(2, func(r *statusRow) { r.Head = head2 }))
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "hold", Text: "hold #1 because #2 landed badly", PRs: []string{repo + "#1", repo + "#2"}}); err != nil {
		t.Fatal(err)
	}

	by := h.snapshot(t)

	if by[1].Hold != machine.MoverRoot || by[2].Hold != machine.MoverRoot {
		t.Fatalf("holds = %q, %q", by[1].Hold, by[2].Hold)
	}
}

func TestAHoldNamesWhoLiftsItAndALiftedOneHoldsNothing(t *testing.T) {
	h := newHarness(t, []recordedPR{
		{number: 45821, head: head, login: "drive-bot"},
		{number: 30388, head: head2, login: "drive-bot"},
		{number: 46038, head: head3, login: "drive-bot"},
		{number: 50000, head: fmt.Sprintf("%040x", 5), login: "drive-bot"},
	}, status(45821), status(30388, func(r *statusRow) { r.Head = head2 }), status(46038, func(r *statusRow) { r.Head = head3 }), status(50000))
	hold(t, "root", "hold #45821 until the audit", repo+"#45821")
	hold(t, "owner", "hold #30388 for me", "30388")
	lifted := hold(t, "root", "hold #46038 for now", repo+"#46038")
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "lift", Text: "lift #46038", Re: lifted.Seq, PRs: []string{repo + "#46038"}}); err != nil {
		t.Fatal(err)
	}
	hold(t, "root", "hold #50000 in the other repository", "other/repo#50000")

	by := h.snapshot(t)

	if by[45821].Hold != machine.MoverRoot || by[30388].Hold != machine.MoverOwner || by[46038].Hold != "" || by[50000].Hold != "" {
		t.Fatalf("holds = %q, %q, %q, %q", by[45821].Hold, by[30388].Hold, by[46038].Hold, by[50000].Hold)
	}
}

func TestGradesBelongToAHeadSoANewPushStartsUngradedWithItsTapAwaitingProof(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	at := time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)
	for kind, value := range map[string]string{"finder": "clear", "tap": "tapped"} {
		if _, err := h.store.PutFact(repo, 1, head2, kind, value, at); err != nil {
			t.Fatal(err)
		}
	}

	row := h.snapshot(t)[1]

	if row.Finder != "" || row.Graded() || row.Tapped() || row.TapFrom != head2 {
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
		status(1), status(2, func(r *statusRow) { r.Approval.State = "none" }))

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
		status(1), status(44, func(r *statusRow) { r.State, r.Queue = "MERGED", "landed" }))
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
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: 1, Head: head, State: prrow.StateOpen}}); err != nil {
		t.Fatal(err)
	}
	source := facts.New(a, landingConfig, s)

	rows, err := source.Snapshot(context.Background())

	var exit *shell.ExitError
	if !errors.As(err, &exit) || rows != nil {
		t.Fatalf("Snapshot = %+v, %v", rows, err)
	}
	if !strings.Contains(err.Error(), "read the drive's pull requests") {
		t.Fatalf("err = %v", err)
	}
}

func TestOnlyGitHubsOwnUnknownCountsAsUncomputed(t *testing.T) {
	for verdict, want := range map[string]bool{
		"blocked:mergeable-unknown":               true,
		"blocked:queue-unknown":                   false,
		"blocked:queue-unknown,mergeable-unknown": false,
		"blocked:conflict,mergeable-unknown":      false,
		"landable":                                false,
	} {
		t.Run(verdict, func(t *testing.T) {
			h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot", merge: "UNKNOWN"}}, status(1, func(row *statusRow) { row.Verdict = verdict }))

			if got := h.snapshot(t)[1].Uncomputed; got != want {
				t.Fatalf("Uncomputed = %v, want %v", got, want)
			}
		})
	}
}

func TestReadMergeableSaysEachPRsAnswerFromOneBatchedRead(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot", merge: "MERGEABLE"}, {number: 2, head: head2, login: "drive-bot", merge: "UNKNOWN"}})

	read, err := h.source.ReadMergeable(context.Background(), []int{1, 2})
	if err != nil {
		t.Fatal(err)
	}

	if !strings.HasPrefix(read[1], "MERGEABLE ") || !strings.HasSuffix(read[1], head) || !strings.HasPrefix(read[2], "UNKNOWN ") || !strings.HasSuffix(read[2], head2) {
		t.Fatalf("read = %v", read)
	}
	if got := len(h.runner.Calls()); got != 1 {
		t.Fatalf("%d calls, want one batched read", got)
	}
}

func TestAnOverrideCarriesToANewHeadOnlyWhenItsOwnDiffIsTheSame(t *testing.T) {
	restacked := strings.NewReplacer("1111111..2222222", "3333333..4444444", "-10,3 +10,4", "-42,3 +44,4").Replace(ownDiff)
	respaced := strings.Replace(ownDiff, `"a b"`, `"ab"`, 1)
	for name, tc := range map[string]struct {
		diff string
		want bool
	}{
		"a restack with the same patch": {restacked, true},
		"a patch that differs":          {respaced, false},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			putFact(t, s, 1, head2, "finder", "ask", tappedAt)
			putFact(t, s, 1, head2, "fidelity", "pass", tappedAt)
			putFact(t, s, 1, head2, machine.OverrideFact("finder"), "434757", tappedAt.Add(time.Minute))
			on(t, s, recordedPR{number: 1, head: head2, login: "drive-bot", diff: ownDiff}).snapshot(t)
			putFact(t, s, 1, head, "finder", "ask", tappedAt.Add(time.Hour))
			putFact(t, s, 1, head, "fidelity", "pass", tappedAt.Add(time.Hour))

			moved := on(t, s, recordedPR{number: 1, head: head, login: "drive-bot", diff: tc.diff})
			moved.snapshot(t)
			if row := moved.snapshot(t)[1]; row.Cleared() != tc.want {
				t.Fatalf("cleared = %v, want %v: %+v", row.Cleared(), tc.want, row)
			}

			posted, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"decide"}})
			if err != nil {
				t.Fatal(err)
			}
			if tc.want && (len(posted) != 1 || posted[0].Lane != bus.SweepLane || !strings.HasPrefix(posted[0].Text, "finder-override #1 at "+head+": 434757.")) {
				t.Fatalf("posted = %+v, want one carried override", posted)
			}
			if !tc.want && len(posted) != 0 {
				t.Fatalf("posted = %+v, want none", posted)
			}
		})
	}
}

func TestAStandingClaimCarriesToANewHeadOnlyWhenItsOwnDiffIsTheSame(t *testing.T) {
	restacked := strings.NewReplacer("1111111..2222222", "3333333..4444444", "-10,3 +10,4", "-42,3 +44,4").Replace(ownDiff)
	respaced := strings.Replace(ownDiff, `"a b"`, `"ab"`, 1)
	port := map[string]string{gates.PortOfAttr: "48435", gates.PortInteractsAttr: gates.PortCleanWord}
	flake := map[string]string{gates.FlakeFixAttr: "TestChatStream"}
	for name, tc := range map[string]struct {
		claim map[string]string
		diff  string
		class string
	}{
		"a clean port restacked with the same patch":     {port, restacked, gates.OldTreePortClass},
		"a flake fix restacked with the same patch":      {flake, restacked, gates.FlakeFixClass},
		"a clean port whose patch changed gates in full": {port, respaced, ""},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			for attr, value := range tc.claim {
				putFact(t, s, 1, head2, attr, value, tappedAt)
			}
			on(t, s, recordedPR{number: 1, head: head2, login: "drive-bot", diff: ownDiff}).snapshot(t)

			moved := on(t, s, recordedPR{number: 1, head: head, login: "drive-bot", diff: tc.diff})
			moved.snapshot(t)
			moved.snapshot(t)

			stored, err := s.Facts(repo, 1, head)
			if err != nil {
				t.Fatal(err)
			}
			if class, refused := gates.LandsOnWord(stored); class != tc.class || refused != "" {
				t.Fatalf("the new head reads %q, refused %q, want %q: %v", class, refused, tc.class, stored)
			}
			posted, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"decide"}})
			if err != nil {
				t.Fatal(err)
			}
			claim := gates.Claim{Repo: repo, PR: 1, Head: head}
			_, claimed := gates.PortAt(claim, posted)
			if claimed != (tc.class == gates.OldTreePortClass) || gates.FlakeAt(claim, posted) != (tc.class == gates.FlakeFixClass) {
				t.Fatalf("the go reads port %v, flake %v from %+v, want %q", claimed, gates.FlakeAt(claim, posted), posted, tc.class)
			}
			if tc.class != "" && len(posted) != 1 {
				t.Fatalf("posted = %+v, want one carried claim across two passes", posted)
			}
		})
	}
}

func TestAHostCountsTheCountedRecordAndNeverReadsAnOwnerRowForAnAppeal(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	putFact(t, h.store, 1, head, "finder", "ask", tappedAt)
	putFact(t, h.store, 1, head, "fidelity", "pass", tappedAt)
	post := func(lane, text string) {
		t.Helper()
		if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: "decide", PRs: []string{"1"}, Text: text}); err != nil {
			t.Fatal(err)
		}
	}

	post("grader-appeals-1", "finder-override #1 at "+head[:7]+": 434757. Row rule/4236611 grounds it")
	if row := h.snapshot(t)[1]; row.Cleared() {
		t.Fatalf("an appeal nothing has counted cleared the grade: %+v", row)
	}
	for _, call := range h.runner.Calls() {
		if call.Argv[0] == "ccn" {
			t.Fatalf("a host read the owner's rows: %v", call.Argv)
		}
	}

	post(bus.SweepLane, "finder-override #1 at "+head[:7]+": 434757. Counted from grader-appeals-1's cci #7, whose owner rows exist.")
	if row := h.snapshot(t)[1]; !row.Cleared() {
		t.Fatalf("the counted override did not clear the grade: %+v", row)
	}
}

func TestAShardsGradeReachesTheStoreSoItsRowReadsGraded(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	putFact(t, h.store, 1, head, "finder", "clear", tappedAt)
	if row := h.snapshot(t)[1]; row.Graded() {
		t.Fatalf("graded with no fidelity grade: %+v", row)
	}
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "fidelity-shard-5", Kind: "review", PRs: []string{repo + "#1"}, Text: "fidelity: ok " + head + " class regression: carries my ok at a2fdafc (438794)."}); err != nil {
		t.Fatal(err)
	}

	if row := h.snapshot(t)[1]; row.Fidelity != machine.FidelityPass || !row.Cleared() {
		t.Fatalf("the shard's ok did not reach the row: %+v", row)
	}
}

func TestAnOverrideClearsAHeadWithNoGradeOnAHostThatNeverHeardItStopped(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	putFact(t, h.store, 1, head, "finder", "clear", tappedAt)
	if row := h.snapshot(t)[1]; row.Graded() {
		t.Fatalf("a head with no fidelity grade read graded: %+v", row)
	}
	override := client.Draft{Drive: "d", Lane: bus.SweepLane, Kind: "decide", PRs: []string{"1"}, Text: "fidelity-override #1 at " + head[:7] + ": 439849. Counted from grader-appeals-1's cci #7, whose owner rows exist."}
	if _, err := client.Post(context.Background(), override); err != nil {
		t.Fatal(err)
	}

	if row := h.snapshot(t)[1]; row.Fidelity != machine.FidelityPass || !row.Cleared() {
		t.Fatalf("the override did not clear it: %+v", row)
	}
}

func TestAStoppedReportReachesAHostThatDidNotGradeIt(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	stopped := client.Draft{Drive: "d", Lane: bus.RunnerLane, Kind: "report", PRs: []string{repo + "#1"}, Text: "GRADE-STOPPED #1", Attrs: map[string]string{bus.GradeStoppedWhyAttr: "fidelity: its pass ended without a grade", bus.GradeStoppedHeadAttr: head}}
	if _, err := client.Post(context.Background(), stopped); err != nil {
		t.Fatal(err)
	}

	if row := h.snapshot(t)[1]; row.GradeStopped == "" {
		t.Fatalf("the stop did not reach this host: %+v", row)
	}
}

func TestAFidelityGradeBehindTheCursorReachesTheStoreOnceAndFillsOnlyAHole(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}, {number: 2, head: head, login: "drive-bot"}}, status(1), status(2))
	grade := func(number int, word string) int64 {
		t.Helper()
		posted, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "fidelity-shard-5", Kind: "review", PRs: []string{repo + "#" + strconv.Itoa(number)}, Text: "fidelity: " + word + " " + head + " read by a shard"})
		if err != nil {
			t.Fatal(err)
		}
		return posted.Seq
	}
	putFact(t, h.store, 2, head, "fidelity", "fail", tappedAt)
	grade(1, "missing")
	grade(1, "ok")
	passed := grade(2, "ok")
	if err := h.store.Advance(bus.LegacyCursor, passed); err != nil {
		t.Fatal(err)
	}

	rows := h.snapshot(t)

	if rows[1].Fidelity != machine.FidelityPass {
		t.Fatalf("the newest lost grade did not reach the store: %+v", rows[1])
	}
	if rows[2].Fidelity != machine.FidelityFail {
		t.Fatalf("a lost grade overwrote a stored one: %+v", rows[2])
	}
	stored, err := h.store.Facts(repo, 1, head)
	if err != nil {
		t.Fatal(err)
	}
	if said := stored[prrow.SaidFact("fidelity")]; !strings.HasPrefix(said, "fidelity: ok") {
		t.Fatalf("the grader's words = %q", said)
	}

	if err := h.store.DeleteFact(repo, 1, head, "fidelity"); err != nil {
		t.Fatal(err)
	}
	if row := h.snapshot(t)[1]; row.Fidelity != "" {
		t.Fatalf("the read ran twice: %+v", row)
	}
}

func TestANewHoldReadsOnlyTheHeldAndQueuedPRsHoweverManyRowsExist(t *testing.T) {
	for _, rows := range []int{1, 1000} {
		h := newHarness(t, []recordedPR{{number: 7, head: head, login: "drive-bot"}, {number: 9, head: head2, login: "drive-bot"}},
			status(7, func(row *statusRow) { row.Queue = "queued" }), status(9, func(row *statusRow) { row.Queue = "queued"; row.Head = head2 }))
		seeded := make([]store.ReviewRow, 0, rows+2)
		seeded = append(seeded,
			store.ReviewRow{Repo: repo, Number: 9, Head: head2, State: prrow.StateOpen, Queue: string(machine.QueueQueued)},
			store.ReviewRow{Repo: repo, Number: 11, Head: head3, State: prrow.StateLanded, Queue: string(machine.QueueQueued)},
		)
		for number := range rows {
			seeded = append(seeded, store.ReviewRow{Repo: repo, Number: 100 + number, Head: head, State: prrow.StateOpen})
		}
		if err := h.store.PutReviewRows(seeded); err != nil {
			t.Fatal(err)
		}
		if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "hold", Text: "hold #7 stop it", PRs: []string{"7"}}); err != nil {
			t.Fatal(err)
		}

		read, err := h.source.HeldAndQueued(context.Background(), []int{7})
		if err != nil {
			t.Fatal(err)
		}

		if got := h.statusNumbers(t); !slices.Equal(got, []string{"7", "9"}) {
			t.Fatalf("%d rows: status read = %v, want the held PR and the live queued row", rows, got)
		}
		by := map[int]machine.Facts{}
		for _, f := range read {
			by[f.Number] = f
		}
		if by[7].Hold != machine.MoverRoot || !by[7].Queued() || by[9].Hold != "" || !by[9].Queued() {
			t.Fatalf("%d rows: read = %+v", rows, by)
		}
	}
}

type searched struct {
	shell.Runner
	reply string
}

func (s *searched) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if strings.Contains(c.Argv[len(c.Argv)-1], "search(") {
		return shell.Result{Stdout: s.reply}, nil
	}
	return s.Runner.Run(ctx, c)
}

func TestAnOldTreePRNoDriveLaneOpenedIsAdmittedOutsideTheRowsAndOwesItsPort(t *testing.T) {
	const steptree = "go/sand/internal/steptree/tree.go"
	node := func(number int, at, state string, paths ...string) string {
		files := make([]string, len(paths))
		for i, path := range paths {
			files[i] = fmt.Sprintf(`{"path":%q}`, path)
		}
		return fmt.Sprintf(`{"number":%d,"state":%q,"title":"fix %d","headRefOid":%q,"updatedAt":"2026-11-05T10:00:00Z","files":{"nodes":[%s]}}`, number, state, number, at, strings.Join(files, ","))
	}
	reply := func(nodes ...string) string {
		return `{"data":{"search":{"nodes":[` + strings.Join(nodes, ",") + `]}}}`
	}
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	runner := &searched{h.runner, reply(node(50525, head, "OPEN", steptree), node(50526, head, "MERGED", "go/sand/next/vm.go"), node(1, head, "OPEN", steptree), node(48967, head, "OPEN", steptree, "go/sand/next/internal/steptree/tree.go"))}
	a := app.New(io.Discard)
	a.Runner = runner
	source := facts.New(a, landingConfig, h.store)
	admitted := func(passes int) map[int]store.OutsidePR {
		t.Helper()
		for range passes {
			if _, err := source.Snapshot(context.Background()); err != nil {
				t.Fatal(err)
			}
		}
		prs, err := h.store.OutsidePRs(repo)
		if err != nil {
			t.Fatal(err)
		}
		return prs
	}

	prs := admitted(2)
	if len(prs) != 1 || prs[50525].State != prrow.StateOpen || !strings.HasPrefix(prs[50525].Port, prrow.PortOwed+": it changes "+steptree) {
		t.Fatalf("admitted = %+v, want only #50525, open and owed; a drive row, a next-tree PR and a PR changing both trees stay out", prs)
	}
	if rows, err := h.store.ReviewRows(repo); err != nil || len(rows) != 1 {
		t.Fatalf("rows = %+v, %v, want only the drive's own row", rows, err)
	}

	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "old-tree-port-desk", Kind: "decide", PRs: []string{repo + "#50525"}, Attrs: map[string]string{prrow.NoPortAttr: "old-only"}, Text: "#50525: no port owed"}); err != nil {
		t.Fatal(err)
	}
	if prs := admitted(1); prs[50525].Port != "no port: old-only" {
		t.Fatalf("port = %q after a no-port decide", prs[50525].Port)
	}

	runner.reply = reply(node(50525, head2, "MERGED", steptree))
	if prs := admitted(2); prs[50525].State != prrow.StateLanded || !strings.HasPrefix(prs[50525].Port, prrow.PortOwed) {
		t.Fatalf("admitted = %+v, want a new merged head owed again", prs[50525])
	}
}
