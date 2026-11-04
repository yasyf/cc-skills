package landing_test

import (
	"context"
	"fmt"
	"os"
	"slices"
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
	tokenTool  = "print-read-token"
	refusal    = `ci bk: GET /pipelines/test/builds/105354 failed: 401 {"message":"Authentication required."}`
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
		State:    t.TempDir(),
		Config:   landing.TrunkConfig{Check: "buildkite/test", CIBuild: "tools/ci bk api /pipelines/{pipeline}/builds/{number}", TokenCommand: []string{tokenTool}, FlakeLane: "red-sweep-3"},
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
	fake.Reply("read-token\n", tokenTool)
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
	if got := trunkRecords(t, "incident"); len(got) != 1 || !strings.HasPrefix(got[0].Text, "DEV-RED for 60 min: ") || !strings.Contains(got[0].Text, "fails go-test on "+redBuild) || !strings.Contains(got[0].Text, "landed by #48001") {
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
	for _, want := range []string{"[finding:supervisor]", "go/race_test.go", "FLAKE TestRace", redBuild, trunkOID[:10], "#47000 last changed it", "fix it by Oct 30"} {
		if !strings.Contains(flakes[0].Text, want) {
			t.Fatalf("flake %q lacks %q", flakes[0].Text, want)
		}
	}
	reports := trunkRecords(t, "report")
	if want := fmt.Sprintf("DEV-GREEN dev's buildkite/test passes again; it was red from #%d.", trunkRecords(t, "incident")[0].Seq); len(reports) != 1 || reports[0].Text != want {
		t.Fatalf("reports = %+v, want %q", reports, want)
	}
}

func TestDevGreenAfterAnOldRedRecordCitesThatRecordNotTheClockItsTextCarries(t *testing.T) {
	f := newTrunkWatch(t)
	old, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "supervisor", Kind: "incident", To: []string{"root"}, Topic: "dev-red", Text: "DEV-RED since 1:00 PM: dev's buildkite/test is red on " + redBuild + ", first red at efe25ce04d."})
	if err != nil {
		t.Fatal(err)
	}

	f.pass(t, trunkHistory(trunkCommit(trunkOID, "SUCCESS", greenBuild)), nil)

	reports := trunkRecords(t, "report")
	if want := fmt.Sprintf("DEV-GREEN dev's buildkite/test passes again; it was red from #%d.", old.Seq); len(reports) != 1 || reports[0].Text != want {
		t.Fatalf("reports = %+v, want %q", reports, want)
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
	if len(got) != 1 || !strings.HasPrefix(got[0].Text, "POST-MERGE-RED for 60 min: ") || got[0].Topic != "dev-red" {
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

func TestEveryBuildReadInAPassCarriesOneReadOfTheToken(t *testing.T) {
	f := newTrunkWatch(t)
	reads := func(token string) func(*shelltest.Fake) {
		return func(fake *shelltest.Fake) {
			fake.Reply(token+"\n", tokenTool)
			bkReply(fake, buildPath+"105354", failedRead)
			bkReply(fake, "/pipelines/test/builds?per_page=30", `[]`)
		}
	}
	history := trunkHistory(trunkCommit(trunkOID, "FAILURE", redBuild))
	carried := func(token string) {
		t.Helper()
		printed, built := 0, 0
		for _, call := range f.watch.Runner.(*shelltest.Fake).Calls() {
			switch call.Argv[0] {
			case tokenTool:
				printed++
			case ciTool:
				built++
				if len(call.Env) != 1 || call.Env[0] != "BUILDKITE_TOKEN="+token {
					t.Fatalf("%v ran with env %v, want the read token", call.Argv, call.Env)
				}
			}
		}
		if printed != 1 || built != 2 {
			t.Fatalf("token read %d times for %d build reads, want 1 and 2", printed, built)
		}
	}

	f.pass(t, history, reads("first"))
	carried("first")

	f.watch.Now = func() time.Time { return t0.Add(time.Hour) }
	f.pass(t, history, reads("rotated"))
	carried("rotated")
}

func TestAWatchWithoutAUsableTokenReadsNoBuildAndNamesTheSetting(t *testing.T) {
	for name, set := range map[string]struct {
		command []string
		reply   func(*shelltest.Fake)
		want    string
	}{
		"unset":   {nil, func(*shelltest.Fake) {}, "reading Buildkite needs landing.ci_token_command"},
		"failing": {[]string{tokenTool}, func(fake *shelltest.Fake) { fake.Fail(254, "the SSO session expired", tokenTool) }, "landing.ci_token_command failed: "},
		"empty":   {[]string{tokenTool}, func(fake *shelltest.Fake) { fake.Reply("\n", tokenTool) }, "landing.ci_token_command printed nothing"},
	} {
		t.Run(name, func(t *testing.T) {
			f := newTrunkWatch(t)
			f.watch.Config.TokenCommand = set.command
			fake := &shelltest.Fake{}
			fake.Reply(trunkHistory(trunkCommit(trunkOID, "SUCCESS", greenBuild), trunkCommit(olderOID, "SUCCESS", redBuild)), "ccx", "vcs", "gh")
			set.reply(fake)
			f.watch.Runner, f.watch.CCX = fake, ccx.CLI{Runner: fake}

			err := f.watch.Pass(context.Background())

			if err == nil || !strings.Contains(err.Error(), set.want) {
				t.Fatalf("err = %v, want it to name %q", err, set.want)
			}
			for _, call := range fake.Calls() {
				if call.Argv[0] == ciTool {
					t.Fatalf("read a build with no token: %v", call.Argv)
				}
			}
		})
	}
}

func TestABuildkite401NamesItselfAndARedTrunkStillPostsThenTheComparisonCatchesUp(t *testing.T) {
	f := newTrunkWatch(t)
	history := trunkHistory(trunkCommit(trunkOID, "FAILURE", redBuild), trunkCommit(olderOID, "SUCCESS", greenBuild))
	refused := &shelltest.Fake{}
	refused.Reply(history, "ccx", "vcs", "gh")
	refused.Reply("read-token\n", tokenTool)
	refused.Fail(1, refusal, ciTool, "bk", "api")
	f.watch.Runner, f.watch.CCX = refused, ccx.CLI{Runner: refused}

	err := f.watch.Pass(context.Background())

	if err == nil || !strings.Contains(err.Error(), "not a red build") || !strings.Contains(err.Error(), "auth failure") || !strings.Contains(err.Error(), "failed: 401") {
		t.Fatalf("err = %v, want it to name the auth failure", err)
	}
	if strings.Contains(err.Error(), "read-token") {
		t.Fatalf("the error carries the token: %v", err)
	}
	if got := trunkRecords(t, "incident"); len(got) != 1 || !strings.HasPrefix(got[0].Text, "DEV-RED for ") {
		t.Fatalf("incidents = %+v, want the red trunk said despite the 401", got)
	}

	f.pass(t, history, func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105354", failedRead)
		bkReply(fake, buildPath+"105355", greenRead)
	})
	if got := trunkRecords(t, "report"); len(got) != 1 || !strings.HasPrefix(got[0].Text, "POST-MERGE-RED trunk-jobs:105354 ") {
		t.Fatalf("reports = %+v, want the comparison the 401 held back", got)
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

func flakyNotices(stamps ...int64) func(*shelltest.Fake) {
	messages := make([]string, len(stamps))
	for i, stamp := range stamps {
		messages[i] = fmt.Sprintf(`{"ts":"%d.000100","permalink":"https://slack/p%d","text":"> failed `+"`go TestRace`"+` on <%s|test #105354> at <https://github.com/o/r/commit/%s|efe25ce>; it looks flaky and nobody was told"}`, stamp, i, redBuild, trunkOID)
	}
	return func(fake *shelltest.Fake) {
		fake.Reply(`{"messages":[`+strings.Join(messages, ",")+`]}`, "cc-slack", "history")
		fake.Fail(1, "", "git", "-C", "/co", "grep")
	}
}

func TestAFlakeNoLiveLaneHoldsLaunchesTheNextFlakeLaneAndTellsRootInOneLine(t *testing.T) {
	f := newTrunkWatch(t)
	f.watch.Config.Channel = "C1"

	f.pass(t, trunkHistory(), flakyNotices(t0.Unix()-5))

	launches := trunkRecords(t, "go")
	if len(launches) != 1 || !strings.HasPrefix(launches[0].Text, "orca-desk: launch red-sweep-1 opus xhigh role=fix brief="+f.watch.State+"/red-sweep/brief.md") {
		t.Fatalf("launches = %+v, want red-sweep-1 from the standing brief", launches)
	}
	brief, err := os.ReadFile(f.watch.State + "/red-sweep/brief.md")
	if err != nil || !strings.Contains(string(brief), "A retry, a skip, a quarantine or a longer timeout is not a fix") || !strings.Contains(string(brief), "Register slice: lane=red-sweep") {
		t.Fatalf("brief = %q, %v", brief, err)
	}
	flakes := trunkRecords(t, "defect")
	if len(flakes) != 1 || len(flakes[0].To) != 1 || flakes[0].To[0] != "red-sweep-1" || !strings.Contains(flakes[0].Text, "fix it by Oct 30 4:00 PM") {
		t.Fatalf("flakes = %+v, want one addressed to the launched lane, due in two hours", flakes)
	}
	notes := trunkRecords(t, "note")
	if len(notes) != 1 || notes[0].To[0] != "root" || notes[0].Re != flakes[0].Seq || strings.Contains(notes[0].Text, "\n") {
		t.Fatalf("notes = %+v, want one line to root", notes)
	}
}

func TestARepeatFailureOfAnOpenFlakeAttachesToItsRecord(t *testing.T) {
	f := newTrunkWatch(t)
	f.watch.Config.Channel = "C1"

	f.pass(t, trunkHistory(), flakyNotices(t0.Unix()-5, t0.Unix()-4))

	flakes := trunkRecords(t, "defect")
	if len(flakes) != 1 {
		t.Fatalf("flakes = %+v, want the repeat on the first record", flakes)
	}
	attached := slices.DeleteFunc(trunkRecords(t, "note"), func(r client.Record) bool { return r.To[0] == "root" })
	if len(attached) != 1 || attached[0].Re != flakes[0].Seq || !strings.Contains(attached[0].Text, "FLAKE TestRace again") {
		t.Fatalf("notes = %+v, want the repeat attached to %s", attached, flakes[0].Name())
	}
}

func TestAFlakeClosedWithNoMergedPRReopensAndOneClosedByAMergedPRStaysClosed(t *testing.T) {
	for name, merged := range map[string]string{"unmerged": "\n", "merged": "2026-10-30T22:00:00Z\n"} {
		t.Run(name, func(t *testing.T) {
			f := newTrunkWatch(t)
			f.watch.Config.Channel = "C1"
			f.pass(t, trunkHistory(), flakyNotices(t0.Unix()-5))
			flake := trunkRecords(t, "defect")[0]
			if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "decide", Resolves: flake.Seq, PRs: []string{"48100"}, Text: "closed: owner named"}); err != nil {
				t.Fatal(err)
			}

			f.pass(t, trunkHistory(), func(fake *shelltest.Fake) {
				flakyNotices(t0.Unix() - 5)(fake)
				fake.Reply(merged, "ccx", "vcs", "gh", "--max-age", "5m", "--", "pr", "view", "48100")
			})

			reopened := slices.DeleteFunc(trunkRecords(t, "defect"), func(r client.Record) bool { return r.Seq == flake.Seq })
			if name == "merged" && len(reopened) != 0 {
				t.Fatalf("reopened = %+v, want a merged fix to close it", reopened)
			}
			if name == "unmerged" && (len(reopened) != 1 || reopened[0].Re != flake.Seq || !strings.HasPrefix(reopened[0].Text, "REOPENED "+flake.Name()) || reopened[0].To[0] != flake.To[0]) {
				t.Fatalf("reopened = %+v, want %s reopened to its owner", reopened, flake.Name())
			}
		})
	}
}

func TestAJobThatFailedOnAGitHubQuotaIsNamedInfrastructureNotAFlakyTest(t *testing.T) {
	f := newTrunkWatch(t)
	f.pass(t, trunkHistory(trunkCommit(trunkOID, "FAILURE", redBuild)), func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105354", failedRead)
		bkReply(fake, "/pipelines/test/builds?per_page=30", `[]`)
	})

	f.pass(t, trunkHistory(trunkCommit(trunkOID, "SUCCESS", greenBuild)), func(fake *shelltest.Fake) {
		bkReply(fake, buildPath+"105354", failedRead)
		bkReply(fake, buildPath+"105354/jobs/job-1/log", `{"content":"fetching modules\ngh: HTTP 403: API rate limit exceeded for installation ID 42\n"}`)
	})

	flakes := trunkRecords(t, "defect")
	if len(flakes) != 1 || !strings.Contains(flakes[0].Text, "INFRA job go-test failed on infrastructure, not a test (gh: HTTP 403: API rate limit exceeded") || strings.Contains(flakes[0].Text, "FLAKE") {
		t.Fatalf("flakes = %+v, want the quota failure named as infrastructure", flakes)
	}
}
