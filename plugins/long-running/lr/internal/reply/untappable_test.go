package reply

import (
	"context"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestEveryPRInPrsMustDrawHisApproveOrNothingPosts(t *testing.T) {
	const pulls = "https://github.com/yasyf/cc-skills/pull/"
	viewed := func(number string) []string {
		return []string{"ccx", "vcs", "gh", "--max-age", "5m", "--", "pr", "view", number}
	}
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

	ledgerRow(t, f, store.ReviewRow{Title: "Relay fix", State: prrow.StateLanded})
	if err := f.untappable(context.Background(), Reply{PRs: []string{landPR}}); err == nil || !strings.Contains(err.Error(), landPR+" is merged") {
		t.Fatalf("refusal = %v, want the landed row's PR named merged", err)
	}
}
