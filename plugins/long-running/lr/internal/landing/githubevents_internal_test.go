package landing

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const eventsRepo = "Forge-AI/monorepo"

func githubRecord(t *testing.T, seq int64, topic, outcome, repo string, number int, head, base string) client.Record {
	t.Helper()
	body, err := json.Marshal(map[string]any{
		"seq": seq, "lane": "github", "kind": "github", "topic": topic, "text": "a pull request moved",
		"fields": map[string]any{"outcome": outcome, "commit": head, "attrs": map[string]string{"base": base, "draft": "false"}},
		"refs":   map[string]any{"prs": []string{repo + "#" + strconv.Itoa(number)}},
	})
	if err != nil {
		t.Fatal(err)
	}
	var record client.Record
	if err := json.Unmarshal(body, &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func openPage(prs ...string) string {
	return `{"data":{"repository":{"pullRequests":{"pageInfo":{"hasNextPage":false},"nodes":[` + strings.Join(prs, ",") + `]}}}}`
}

func openNode(number int, head, base string) string {
	return `{"number":` + strconv.Itoa(number) + `,"headRefOid":"` + head + `","baseRefName":"` + base + `","isDraft":false}`
}

func TestRecordsMoveTheOpenPullRequestsOfTheRepoAlone(t *testing.T) {
	g := &GithubEvents{Repo: eventsRepo, open: map[int]githubPR{7: {Head: "aaa", Base: "dev"}, 8: {Head: "bbb", Base: "dev"}}}
	g.apply([]client.Record{
		githubRecord(t, 1, "pull_request", "synchronize", eventsRepo, 7, "ccc", "dev"),
		githubRecord(t, 2, "pull_request", "merged", eventsRepo, 8, "bbb", "dev"),
		githubRecord(t, 3, "pull_request", "opened", eventsRepo, 9, "ddd", "lane/base"),
		githubRecord(t, 4, "pull_request", "opened", "yasyf/cc-skills", 10, "eee", "main"),
		githubRecord(t, 5, "check", "success", eventsRepo, 7, "zzz", ""),
	})
	want := map[int]githubPR{7: {Head: "ccc", Base: "dev"}, 9: {Head: "ddd", Base: "lane/base"}}
	if len(g.open) != len(want) || g.open[7] != want[7] || g.open[9] != want[9] || g.after != 5 {
		t.Fatalf("open = %+v after %d, want %+v after 5", g.open, g.after, want)
	}
}

func TestDisagreementsNameEachPullRequestTheRecordsGotWrong(t *testing.T) {
	events := map[int]githubPR{7: {Head: "aaa", Base: "dev"}, 8: {Head: "bbb", Base: "dev"}, 9: {Head: "ccc", Base: "dev"}}
	read := map[int]githubPR{7: {Head: "aaa", Base: "dev"}, 8: {Head: "bbx", Base: "dev"}, 10: {Head: "ddd", Base: "dev"}}
	got := disagreements(events, read)
	want := []string{
		"#10 is open at ddd with no record",
		"#8 records say bbb onto dev, GitHub says bbx onto dev",
		"#9 records say open at ccc, GitHub lists it shut",
	}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("disagreements = %q, want %q", got, want)
	}
	if found := disagreements(read, read); len(found) != 0 {
		t.Fatalf("an agreeing read found %q", found)
	}
}

func TestTheAuditReadsGitHubAtStartAndHourlyAndFilesADriftAsADefect(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	now := time.Date(2026, 11, 3, 6, 0, 0, 0, time.UTC)
	fake := &shelltest.Fake{}
	fake.Reply(openPage(openNode(7, "aaa", "dev")), "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql")
	g := &GithubEvents{CCX: ccx.CLI{Runner: fake}, Drive: "d", Repo: eventsRepo, Now: func() time.Time { return now }}
	for range 2 {
		if err := g.Pass(context.Background()); err != nil {
			t.Fatal(err)
		}
		now = now.Add(30 * time.Minute)
	}
	if reads := len(fake.Calls()); reads != 1 {
		t.Fatalf("GitHub read %d times in the first hour, want once at start", reads)
	}
	hourly := &shelltest.Fake{}
	hourly.Reply(openPage(openNode(7, "bbb", "dev")), "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql")
	g.CCX = ccx.CLI{Runner: hourly}
	if err := g.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	defects, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"defect"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(hourly.Calls()) != 1 || len(defects) != 1 || !strings.Contains(defects[0].Text, "#7 records say aaa onto dev, GitHub says bbb onto dev") {
		t.Fatalf("after an hour: %d reads, defects %+v", len(hourly.Calls()), defects)
	}
	if g.open[7].Head != "bbb" {
		t.Fatalf("the audit kept %+v, want GitHub's read", g.open)
	}
}
