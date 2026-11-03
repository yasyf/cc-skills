package landing_test

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	redBuild   = "https://buildkite.com/forge/test/builds/105354"
	greenBuild = "https://buildkite.com/forge/test/builds/105355"
	trunkOID   = "efe25ce04d1234567890abcdef1234567890abcd"
	olderOID   = "a786019e701234567890abcdef1234567890abcd"
	buildPath  = "/pipelines/test/builds/"
	ciTool     = "/co/tools/ci"
)

func trunkHistory(commits ...string) string {
	return `{"data":{"repository":{"object":{"history":{"nodes":[` + strings.Join(commits, ",") + `]}}}}}`
}

func trunkCommit(oid, state, url string) string {
	return fmt.Sprintf(`{"oid":%q,"committedDate":"2026-10-30T20:00:00Z","message":"sand: fix (#48001)\n\nbody","status":{"contexts":[{"context":"buildkite/test","state":%q,"targetUrl":%q}]}}`, oid, state, url)
}

type trunkFixture struct {
	watch *landing.TrunkWatch
}

func newTrunkWatch(t *testing.T) trunkFixture {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return trunkFixture{&landing.TrunkWatch{
		Store:    s,
		Drive:    "d",
		Repo:     monorepo,
		Trunk:    "dev",
		Checkout: "/co",
		Config:   landing.TrunkConfig{Check: "buildkite/test", CIBuild: "tools/ci bk api /pipelines/{pipeline}/builds/{number}", FlakeLane: "red-sweep-3"},
		Now:      func() time.Time { return t0 },
	}}
}

func (f trunkFixture) pass(t *testing.T, history string, replies func(*shelltest.Fake)) {
	t.Helper()
	fake := &shelltest.Fake{}
	fake.Reply(history, "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql")
	if replies != nil {
		replies(fake)
	}
	f.watch.Runner, f.watch.CCX = fake, ccx.CLI{Runner: fake}
	if err := f.watch.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func bkReply(fake *shelltest.Fake, path, body string) {
	fake.Reply(body, ciTool, "bk", "api", path)
}

func trunkRecords(t *testing.T, kind string) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{kind}})
	if err != nil {
		t.Fatal(err)
	}
	return records
}

const (
	failedJob  = `{"id":"job-1","type":"script","name":"go tests","step_key":"go-test","state":"failed"}`
	failedRead = `{"branch":"dev","state":"failed","jobs":[` + failedJob + `]}`
	greenRead  = `{"branch":"dev","state":"passed","jobs":[]}`
)

func TestATrunkRetryThatPassesPostsOneFlakeNamingTheBuildCommitAndTest(t *testing.T) {
	f := newTrunkWatch(t)
	f.pass(t, trunkHistory(trunkCommit(trunkOID, "FAILURE", redBuild)), func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105354", failedRead)
		bkReply(fake, "/pipelines/test/builds?per_page=30", `[]`)
	})
	if got := trunkRecords(t, "incident"); len(got) != 1 || !strings.HasPrefix(got[0].Text, "DEV-RED since ") || !strings.Contains(got[0].Text, "fails go-test on "+redBuild) || !strings.Contains(got[0].Text, "landed by #48001") {
		t.Fatalf("incidents = %+v", got)
	}

	retried := func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105354", failedRead)
		bkReply(fake, buildPath+"105354/jobs/job-1/log", `{"content":"\u001b[31m--- FAIL: TestRace (0.1s)\n"}`)
		fake.Reply("go/race_test.go\n", "git", "-C", "/co", "grep")
		fake.Reply("sand: race (#47000)\n", "git", "-C", "/co", "log")
	}
	f.pass(t, trunkHistory(trunkCommit(trunkOID, "SUCCESS", greenBuild)), retried)
	f.pass(t, trunkHistory(trunkCommit(trunkOID, "SUCCESS", greenBuild)), retried)

	flakes := trunkRecords(t, "defect")
	if len(flakes) != 1 {
		t.Fatalf("flakes = %+v, want one", flakes)
	}
	for _, want := range []string{"[finding:supervisor]", "go/race_test.go", "FLAKE TestRace", redBuild, trunkOID[:10], "#47000 last changed it", "Fix due"} {
		if !strings.Contains(flakes[0].Text, want) {
			t.Fatalf("flake %q lacks %q", flakes[0].Text, want)
		}
	}
	reports := trunkRecords(t, "report")
	if len(reports) != 1 || !strings.HasPrefix(reports[0].Text, "DEV-GREEN dev's buildkite/test passes again") {
		t.Fatalf("reports = %+v, want one DEV-GREEN", reports)
	}
}

func TestAJobItsAgentStoppedUnderIsNoFlake(t *testing.T) {
	f := newTrunkWatch(t)
	stopped := `{"branch":"dev","state":"failed","jobs":[{"id":"job-1","type":"script","name":"go tests","state":"failed","signal_reason":"agent_stop"}]}`
	f.pass(t, trunkHistory(trunkCommit(trunkOID, "FAILURE", redBuild)), func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105354", stopped)
		bkReply(fake, "/pipelines/test/builds?per_page=30", `[]`)
	})

	f.pass(t, trunkHistory(trunkCommit(trunkOID, "SUCCESS", greenBuild)), func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105354", stopped)
	})

	if got := trunkRecords(t, "defect"); len(got) != 0 {
		t.Fatalf("flakes = %+v, want none", got)
	}
}

func TestARedOnlyPostMergeJobsReadIsAReportAndLandingContinues(t *testing.T) {
	f := newTrunkWatch(t)
	f.pass(t, trunkHistory(trunkCommit(trunkOID, "FAILURE", redBuild)), func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105354", failedRead)
		bkReply(fake, "/pipelines/test/builds?per_page=30", `[{"branch":"yasyf/x","state":"passed","jobs":[{"type":"script","step_key":"lint"}]}]`)
	})

	if got := trunkRecords(t, "incident"); len(got) != 0 {
		t.Fatalf("incidents = %+v, want none", got)
	}
	got := trunkRecords(t, "report")
	if len(got) != 1 || !strings.HasPrefix(got[0].Text, "POST-MERGE-RED since ") || got[0].Topic != "dev-red" {
		t.Fatalf("reports = %+v", got)
	}
}

func TestARetriedJobInANewTrunkBuildIsAFlakeAndANewFailingJobIsAPostMergeRed(t *testing.T) {
	f := newTrunkWatch(t)
	history := trunkHistory(trunkCommit(trunkOID, "SUCCESS", greenBuild), trunkCommit(olderOID, "SUCCESS", redBuild))
	newer := `{"branch":"dev","state":"passed","jobs":[{"id":"job-2","type":"script","name":"go tests","state":"passed","retry_source":{"job_id":"job-1"}},{"id":"job-3","type":"script","name":"evals","state":"failed","soft_failed":true}]}`
	f.pass(t, history, func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105355", newer)
		bkReply(fake, buildPath+"105354", greenRead)
		bkReply(fake, buildPath+"105355/jobs/job-1", `{"signal_reason":""}`)
		bkReply(fake, buildPath+"105355/jobs/job-1/log", `{"content":"test sand::race ... FAILED\n"}`)
		fake.Fail(1, "", "git", "-C", "/co", "grep")
	})

	flakes := trunkRecords(t, "defect")
	if len(flakes) != 1 || !strings.Contains(flakes[0].Text, "FLAKE sand::race") {
		t.Fatalf("flakes = %+v", flakes)
	}
	reports := trunkRecords(t, "report")
	if len(reports) != 1 || !strings.HasPrefix(reports[0].Text, "POST-MERGE-RED trunk-jobs:105355 ") || !strings.Contains(reports[0].Text, "evals soft-failed") || !strings.Contains(reports[0].Text, "#48001") {
		t.Fatalf("reports = %+v", reports)
	}

	f.pass(t, history, nil)
	if got := trunkRecords(t, "report"); len(got) != 1 {
		t.Fatalf("the same build compared twice: %+v", got)
	}
}

func TestAFlakyNoticeInSlackBecomesOneFlake(t *testing.T) {
	f := newTrunkWatch(t)
	f.watch.Config.Channel = "C1"
	notice := fmt.Sprintf(`{"messages":[{"ts":"%d.000100","permalink":"https://slack/p1","text":"> failed `+"`go TestRace`"+` on <%s|test #105354> at <https://github.com/o/r/commit/%s|efe25ce>; it looks flaky and nobody was told"},{"ts":"%d.000200","permalink":"https://slack/p2","text":"a build passed"}]}`, t0.Unix()-5, redBuild, trunkOID, t0.Unix()-4)
	replies := func(fake *shelltest.Fake) {
		fake.Reply(notice, "cc-slack", "history")
		fake.Fail(1, "", "git", "-C", "/co", "grep")
	}

	f.pass(t, trunkHistory(), replies)
	f.pass(t, trunkHistory(), replies)

	flakes := trunkRecords(t, "defect")
	if len(flakes) != 1 || !strings.Contains(flakes[0].Text, "FLAKE TestRace") || !strings.Contains(flakes[0].Text, "https://slack/p1") || !strings.Contains(flakes[0].Text, trunkOID[:10]) {
		t.Fatalf("flakes = %+v", flakes)
	}
}
