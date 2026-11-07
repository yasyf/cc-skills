package reply

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestEveryPRInPrsMustDrawHisApproveOrNothingPosts(t *testing.T) {
	const pulls = "https://github.com/yasyf/cc-skills/pull/"
	viewed := func(number string) []string {
		return []string{"ccx", "vcs", "gh", "--max-age", "5m", "--", "pr", "view", number}
	}
	t.Setenv("CCI_HOME", t.TempDir())
	fake := &shelltest.Fake{}
	fake.Reply(`{"state":"OPEN","author":{"login":"forge-matt"}}`, viewed("2200")...)
	fake.Reply(`{"state":"MERGED","author":{"login":"a"}}`, viewed("2201")...)
	fake.Fail(1, "HTTP 502: Bad Gateway", viewed("2202")...)
	fake.Reply(`{"state":"OPEN","author":{"login":"a"}}`, viewed("2203")...)
	f := fanout(t, fake)
	ledgerRow(t, f, store.ReviewRow{Title: "Relay fix", State: prrow.StateOpen})

	if err := f.untappable(context.Background(), Reply{PRs: []string{landPR}}); err != nil {
		t.Fatalf("an open PR with its row refused: %v", err)
	}
	for name, tc := range map[string]struct {
		pr, why string
	}{
		"another person's open PR":   {pulls + "2200", "is forge-matt's, and another person's PR stays off his list"},
		"a merged PR with no row":    {pulls + "2201", "is merged"},
		"a PR GitHub did not read":   {pulls + "2202", "did not read from GitHub"},
		"the drive's PR with no row": {pulls + "2203", "has no row in the drive's PR ledger"},
	} {
		t.Run(name, func(t *testing.T) {
			err := f.untappable(context.Background(), Reply{PRs: []string{landPR, tc.pr}})
			if err == nil || !strings.HasPrefix(err.Error(), "NO APPROVE") || !strings.Contains(err.Error(), tc.pr+" "+tc.why) || strings.Contains(err.Error(), landPR) {
				t.Fatalf("refusal = %v, want NO APPROVE naming only %s %s", err, tc.pr, tc.why)
			}
		})
	}

	if err := f.untappable(context.Background(), Reply{MD: "#2196 is closed unlanded.", PRs: []string{landPR}}); err == nil || !strings.Contains(err.Error(), landPR+" is one this reply says is closed") {
		t.Fatalf("refusal = %v, want the PR the reply calls closed refused though its row still reads open", err)
	}

	ledgerRow(t, f, store.ReviewRow{Title: "Relay fix", State: prrow.StateLanded})
	if err := f.untappable(context.Background(), Reply{PRs: []string{landPR}}); err == nil || !strings.Contains(err.Error(), landPR+" is merged") {
		t.Fatalf("refusal = %v, want the landed row's PR named merged", err)
	}

	ledgerRow(t, f, store.ReviewRow{Title: "Relay fix", State: prrow.StateOpen, ReadHead: "1111111111111111111111111111111111111111"})
	if err := f.untappable(context.Background(), Reply{PRs: []string{landPR}}); err == nil || !strings.Contains(err.Error(), landPR+" waits on a read of its GitHub title and body at e718a74") || !strings.Contains(err.Error(), "this reply's read of GitHub failed") {
		t.Fatalf("refusal = %v, want the PR whose read GitHub did not answer refused, naming the head it waits on and the failed read", err)
	}
}

func TestAPRWhoseHeadOutranItsReadDrawsHisApproveAfterOneGitHubReadAtOneAndTenPRs(t *testing.T) {
	const older = "1111111111111111111111111111111111111111"
	githubCalls := map[int]int{}
	for _, n := range []int{1, 10} {
		t.Setenv("CCI_HOME", t.TempDir())
		fake := &shelltest.Fake{}
		f := fanout(t, fake)
		ledgerRow(t, f, store.ReviewRow{State: prrow.StateOpen, ReadHead: older})
		heads := map[int]string{2196: "e718a7434fa01ee9835f992a9a647a825350732c"}
		rows := make([]store.ReviewRow, 0, n)
		for number := 2197; number < 2196+n; number++ {
			heads[number] = fmt.Sprintf("%040x", number)
			rows = append(rows, store.ReviewRow{Repo: "yasyf/cc-skills", Number: number, Head: heads[number], ReadHead: older, State: prrow.StateOpen})
		}
		s, err := store.Open(f.Entry.Store())
		if err != nil {
			t.Fatal(err)
		}
		if err := s.PutReviewRows(rows); err != nil {
			t.Fatal(err)
		}
		_ = s.Close()
		prs, numbers := make([]string, 0, n), make([]int, 0, n)
		members, open := map[string]any{}, make([]map[string]any, 0, len(heads))
		for number, head := range heads {
			prs, numbers = append(prs, fmt.Sprintf("https://github.com/yasyf/cc-skills/pull/%d", number)), append(numbers, number)
			members[fmt.Sprintf("r%d", number)] = map[string]any{"number": number, "state": "OPEN", "title": fmt.Sprintf("Relay fix %d", number), "headRefOid": head, "baseRefName": "main", "author": map[string]any{"login": "a"}}
			open = append(open, map[string]any{"number": number, "headRefOid": head})
		}
		openPages, err := json.Marshal([]any{map[string]any{"data": map[string]any{"repository": map[string]any{"pullRequests": map[string]any{"nodes": open}}}}})
		if err != nil {
			t.Fatal(err)
		}
		read, err := json.Marshal(map[string]any{"data": map[string]any{"repository": members}})
		if err != nil {
			t.Fatal(err)
		}
		fake.Reply(string(openPages), "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql", "--paginate")
		fake.Reply(string(read), "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql")
		fake.Reply(`[]`, "ccx", "vcs", "gh", "--max-age", "5m", "--", "api")

		if err := f.untappable(context.Background(), Reply{PRs: prs}); err != nil {
			t.Fatalf("n=%d: refusal = %v, want every PR read at its head and drawing his Approve", n, err)
		}

		_, after, err := board.Rows(f.Entry, numbers)
		if err != nil || len(after) != n {
			t.Fatalf("n=%d: rows = %+v, %v", n, after, err)
		}
		for _, row := range after {
			if prrow.WordsWait(row) != "" || row.Title != fmt.Sprintf("Relay fix %d", row.Number) {
				t.Errorf("n=%d: #%d = %+v, want its title read at its head %s", n, row.Number, row, heads[row.Number])
			}
		}
		var memberReads []string
		for _, call := range fake.Calls() {
			query := strings.Join(call.Argv, " ")
			if !strings.HasPrefix(query, "ccx vcs gh ") {
				continue
			}
			githubCalls[n]++
			if strings.Contains(query, "pullRequest(") {
				memberReads = append(memberReads, query)
			}
		}
		if len(memberReads) != 1 {
			t.Fatalf("n=%d: PR reads = %d, want one query for every PR waiting on a read", n, len(memberReads))
		}
		for _, number := range numbers {
			if !strings.Contains(memberReads[0], fmt.Sprintf("r%d: pullRequest(number: %d)", number, number)) {
				t.Errorf("n=%d: the read did not ask for #%d", n, number)
			}
		}
	}
	if githubCalls[1] != githubCalls[10] {
		t.Fatalf("GitHub calls at 1 and 10 PRs = %d and %d, want the same", githubCalls[1], githubCalls[10])
	}
}
