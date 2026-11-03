package incident

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func TestBuildkiteReadsTheStoredConfigurationAndPagesBuilds(t *testing.T) {
	var page []json.RawMessage
	if err := json.Unmarshal([]byte(readFile(t, filepath.Join("testdata", "pr-review-builds.json"))), &page); err != nil {
		t.Fatal(err)
	}
	full, err := json.Marshal(repeated(page[0], 100))
	if err != nil {
		t.Fatal(err)
	}
	last, err := json.Marshal(page[:1])
	if err != nil {
		t.Fatal(err)
	}
	query := "state[]=failed&state[]=canceled&created_from=2026-10-01T16:45:36Z&created_to=2026-10-01T18:08:00Z"
	fake := &shelltest.Fake{}
	fake.Reply(string(full), "bk", "api", "/pipelines/pr-review/builds?"+query+"&per_page=100&page=1")
	fake.Reply(string(last), "bk", "api", "/pipelines/pr-review/builds?"+query+"&per_page=100&page=2")
	fake.Reply(readFile(t, filepath.Join("testdata", "pr-review-pipeline.json")), "bk", "api", "/pipelines/pr-review")
	buildkite := BuildkiteCLI{Runner: fake, Pipeline: "pr-review"}
	ctx := context.Background()

	configuration, err := buildkite.Configuration(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(configuration, "infra/generate.ts") {
		t.Fatalf("configuration %q", configuration)
	}
	builds, err := buildkite.Failed(ctx, "2026-10-01T16:45:36Z", "2026-10-01T18:08:00Z")
	if err != nil {
		t.Fatal(err)
	}
	if len(builds) != 101 || builds[0].RebuiltFrom == nil || builds[0].RebuiltFrom.Number != 72867 {
		t.Fatalf("%d builds, first %+v", len(builds), builds[0])
	}
	prepared := false
	for _, job := range builds[0].Jobs {
		if strings.Contains(job.Name, "Prepare PR evidence") {
			prepared = job.State == "passed"
		}
	}
	if !prepared {
		t.Fatalf("jobs %+v", builds[0].Jobs)
	}
}

func repeated(value json.RawMessage, count int) []json.RawMessage {
	out := make([]json.RawMessage, count)
	for i := range out {
		out[i] = value
	}
	return out
}

func TestBuildkiteAsksForEveryUnfinishedStateAndRebuildsWithPut(t *testing.T) {
	fake := &shelltest.Fake{}
	query := "state[]=creating&state[]=scheduled&state[]=running&state[]=failing&state[]=canceling&state[]=blocked&created_from=a&created_to=b"
	fake.Reply("[]", "bk", "api", "/pipelines/p/builds?"+query+"&per_page=100&page=1")
	fake.Reply(`{"number": 9, "web_url": "u"}`, "bk", "api", "-X", "PUT", "/pipelines/p/builds/7/rebuild")
	buildkite := BuildkiteCLI{Runner: fake, Pipeline: "p"}
	ctx := context.Background()

	builds, err := buildkite.Unfinished(ctx, "a", "b")
	if err != nil || len(builds) != 0 {
		t.Fatalf("builds %v, err %v", builds, err)
	}
	build, err := buildkite.Rebuild(ctx, 7)
	if err != nil || build.Number != 9 || build.WebURL != "u" {
		t.Fatalf("build %+v, err %v", build, err)
	}
}

func TestGitHubReadsPRHeadsInOneQueryAndFindsAQueueLanding(t *testing.T) {
	graph := `{"data": {"repository": {"p28345": {"number": 28345, "state": "OPEN", "headRefOid": "4813", "url": "u"}, "p28841": {"number": 28841, "state": "CLOSED", "headRefOid": "8152", "url": "v"}, "p1": null}}}`
	commits := `[{"sha": "aaaa", "commit": {"message": "other (#1)"}}, {"sha": "bbbb", "commit": {"message": "ci: pr-review generates the infra graphs (#28998)\n\nbody"}}]`
	gateway := []string{"ccx", "vcs", "gh", "--max-age", "5m", "--"}
	fake := &shelltest.Fake{}
	fake.Reply(graph, append(gateway, "api", "graphql")...)
	fake.Reply(`{"state": "CLOSED", "mergedAt": null, "mergeCommit": null, "closedAt": "2026-10-01T17:45:48Z"}`, append(gateway, "pr", "view")...)
	fake.Reply(commits, append(gateway, "api", "repos/Forge-AI/monorepo/commits?sha=dev&since=2026-10-01T17:35:48Z&per_page=100")...)
	github := GitHubCLI{Runner: fake, Repo: "Forge-AI/monorepo", Trunk: "dev"}
	ctx := context.Background()

	views, err := github.PRs(ctx, []int{28345, 28841, 1})
	if err != nil {
		t.Fatal(err)
	}
	want := map[int]PRView{28345: {"OPEN", "4813", "u"}, 28841: {"CLOSED", "8152", "v"}}
	if !reflect.DeepEqual(views, want) {
		t.Fatalf("views %+v", views)
	}
	if calls := fake.Calls(); len(calls) != 1 || !strings.Contains(calls[0].Argv[len(calls[0].Argv)-1], "p28345: pullRequest(number: 28345)") {
		t.Fatalf("calls %+v", calls)
	}
	sha, err := github.Landed(ctx, 28998)
	if err != nil || sha != "bbbb" {
		t.Fatalf("sha %q, err %v", sha, err)
	}
}

func TestGitHubReadsAMergeCommitAndAnOpenPRIsNotLanded(t *testing.T) {
	gateway := []string{"ccx", "vcs", "gh", "--max-age", "5m", "--"}
	fake := &shelltest.Fake{}
	fake.Reply(`{"state": "MERGED", "mergeCommit": {"oid": "cccc"}}`, append(gateway, "pr", "view", "1")...)
	fake.Reply(`{"state": "OPEN"}`, append(gateway, "pr", "view", "2")...)
	github := GitHubCLI{Runner: fake, Repo: "o/n", Trunk: "main"}
	ctx := context.Background()
	if sha, err := github.Landed(ctx, 1); err != nil || sha != "cccc" {
		t.Fatalf("sha %q, err %v", sha, err)
	}
	if sha, err := github.Landed(ctx, 2); err != nil || sha != "" {
		t.Fatalf("sha %q, err %v", sha, err)
	}
}

func TestOrcaParsesTheLaunchLine(t *testing.T) {
	ctx := context.Background()
	for _, state := range []string{"ready", "unsupervised"} {
		fake := &shelltest.Fake{}
		fake.Reply(fmt.Sprintf("booting\nincident-x-fix %s task=t1 dispatch=d1 terminal=term-1 worktree=/w/x\n", state), "orca-launch.sh")
		receipt, err := OrcaCLI{Runner: fake, Run: "run1", Repo: "repo1", Launcher: "orca-launch.sh"}.Launch(ctx, "incident-x-fix", "incident", "xhigh", "/b.md", false)
		if err != nil || receipt.State != state || receipt.Dispatch != "d1" {
			t.Fatalf("receipt %+v, err %v", receipt, err)
		}
	}
	fake := &shelltest.Fake{}
	fake.Reply("incident-x-fix failed worker-start terminal=: refused\n", "orca-launch.sh")
	var failed *EffectFailed
	if _, err := (OrcaCLI{Runner: fake, Run: "run1", Repo: "repo1", Launcher: "orca-launch.sh"}).Launch(ctx, "incident-x-fix", "incident", "xhigh", "/b.md", false); !errors.As(err, &failed) {
		t.Fatalf("got %v, want an effect failure", err)
	}
	if _, err := (OrcaCLI{Runner: fake, Launcher: "orca-launch.sh"}).Launch(ctx, "incident-x-fix", "incident", "xhigh", "/b.md", false); !errors.As(err, &failed) {
		t.Fatalf("got %v, want an effect failure with no run", err)
	}
}

func TestOrcaLaunchesTheBackupInClaudeFastMode(t *testing.T) {
	t.Setenv(claudeArgsEnv, "--verbose")
	fake := &shelltest.Fake{}
	fake.Reply("incident-x-backup ready task=t1 dispatch=d1 terminal=term-1 worktree=/w/x\n", "orca-launch.sh")
	orca := OrcaCLI{Runner: fake, Run: "run1", Repo: "repo1", Launcher: "orca-launch.sh"}
	ctx := context.Background()
	if _, err := orca.Launch(ctx, "incident-x-backup", "opus", "xhigh", "/b.md", true); err != nil {
		t.Fatal(err)
	}
	if _, err := orca.Launch(ctx, "incident-x-fix", "incident", "xhigh", "/b.md", false); err != nil {
		t.Fatal(err)
	}
	calls := fake.Calls()
	if want := claudeArgsEnv + `=--verbose --settings '{"fastMode":true}'`; !strings.Contains(strings.Join(calls[0].Env, "\n"), want) {
		t.Fatalf("env %v", calls[0].Env)
	}
	if strings.Contains(strings.Join(calls[1].Env, "\n"), claudeArgsEnv) {
		t.Fatalf("a fix lane launched with fast args: %v", calls[1].Env)
	}
}

func TestOrcaReceiptReadsAReceiptLeftBeforeItsRename(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("ORCA_LAUNCH_STATE", dir)
	if err := os.WriteFile(filepath.Join(dir, "incident-x-fix.json.new"), []byte(`{"result": {"taskId": "t1", "dispatchId": "d1", "state": "ready"}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	orca := OrcaCLI{Runner: &shelltest.Fake{}, Run: "run1", Repo: "repo1"}
	receipt, err := orca.Receipt("incident-x-fix")
	if err != nil || receipt == nil || receipt.Dispatch != "d1" {
		t.Fatalf("receipt %+v, err %v", receipt, err)
	}
	receipt, err = orca.Receipt("incident-x-evidence")
	if err != nil || receipt != nil {
		t.Fatalf("receipt %+v, err %v", receipt, err)
	}
}

func TestOrcaEngagedReadsALaunchInFlightOrALiveWorker(t *testing.T) {
	now := time.Date(2026, 10, 1, 17, 25, 46, 0, time.UTC)
	const never = -1 * time.Second
	cases := []struct {
		began, landed time.Duration
		status        string
		want          bool
	}{
		{never, never, "dispatched", false},
		{time.Minute, never, "dispatched", true},
		{callTimeout + time.Minute, never, "dispatched", false},
		{2 * time.Minute, time.Minute, "dispatched", true},
		{2 * time.Minute, time.Minute, "completed", false},
		{2 * time.Minute, time.Minute, "failed", false},
		{time.Minute, time.Hour, "completed", true},
	}
	for i, c := range cases {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			dir := t.TempDir()
			t.Setenv("ORCA_LAUNCH_STATE", dir)
			write := func(name string, age time.Duration, body string) {
				if age == never {
					return
				}
				path := filepath.Join(dir, name)
				if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
					t.Fatal(err)
				}
				at := now.Add(-age)
				if err := os.Chtimes(path, at, at); err != nil {
					t.Fatal(err)
				}
			}
			write("api-fix-opus.binding.json", c.began, "{}")
			write("api-fix-opus.json", c.landed, `{"result": {"taskId": "t1", "dispatchId": "d1", "state": "ready"}}`)
			fake := &shelltest.Fake{}
			fake.Reply(fmt.Sprintf(`{"ok": true, "result": {"dispatch": {"status": %q}}}`, c.status), "orca", "orchestration", "worker-show", "--dispatch", "d1")
			got, err := OrcaCLI{Runner: fake, Run: "run1", Repo: "repo1"}.Engaged(context.Background(), "api-fix-opus", now)
			if err != nil {
				t.Fatal(err)
			}
			if got != c.want {
				t.Fatalf("engaged %v, want %v", got, c.want)
			}
		})
	}
}

func TestActivationReadsTheSyncDryRun(t *testing.T) {
	ctx := context.Background()
	argv := []string{"bun", "infra/ci/src/cli.ts", "sync", "pr-review"}
	drifting := &shelltest.Fake{}
	drifting.Fail(1, "pr-review: patch\nci sync: pipelines drifted from their resources: pr-review\n", argv...)
	drifted, err := ActivationCLI{Runner: drifting, Pipeline: "pr-review"}.Drifted(ctx, "/w")
	if err != nil || !drifted {
		t.Fatalf("drifted %v, err %v", drifted, err)
	}
	if calls := drifting.Calls(); !reflect.DeepEqual(calls[0].Argv, argv) || calls[0].Dir != "/w" {
		t.Fatalf("calls %+v", calls)
	}
	matching := &shelltest.Fake{}
	matching.Reply("1 v2 pipeline resource(s) match Buildkite\n", argv...)
	drifted, err = ActivationCLI{Runner: matching, Pipeline: "pr-review"}.Drifted(ctx, "/w")
	if err != nil || drifted {
		t.Fatalf("drifted %v, err %v", drifted, err)
	}
}

func TestActivationRefusesAnUnreadableDryRun(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "ci sync: BUILDKITE_TOKEN is not set\n", "bun")
	var failed *EffectFailed
	if _, err := (ActivationCLI{Runner: fake, Pipeline: "pr-review"}).Drifted(context.Background(), "/w"); !errors.As(err, &failed) {
		t.Fatalf("got %v, want an effect failure", err)
	}
}

func TestActivationApplyReturnsTheAppliedLines(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("plan\n  applied patched pipeline pr-review\n", "bun", "infra/ci/src/cli.ts", "sync", "pr-review", "--apply")
	applied, err := ActivationCLI{Runner: fake, Pipeline: "pr-review"}.Apply(context.Background(), "/w")
	if err != nil || !reflect.DeepEqual(applied, []string{"applied patched pipeline pr-review"}) {
		t.Fatalf("applied %v, err %v", applied, err)
	}
	failing := &shelltest.Fake{}
	failing.Fail(2, "boom", "bun")
	var failed *EffectFailed
	if _, err := (ActivationCLI{Runner: failing, Pipeline: "pr-review"}).Apply(context.Background(), "/w"); !errors.As(err, &failed) {
		t.Fatalf("got %v, want an effect failure", err)
	}
}

func TestActivationPrepareRefusesAWorktreeAtAnotherCommit(t *testing.T) {
	dir := t.TempDir()
	fake := &shelltest.Fake{}
	fake.Reply("aaaa\n", "git", "-C", dir, "rev-parse", "HEAD")
	var failed *EffectFailed
	if _, err := (ActivationCLI{Runner: fake, Checkout: "/m", Worktree: dir}).Prepare(context.Background(), "bbbb"); !errors.As(err, &failed) {
		t.Fatalf("got %v, want an effect failure", err)
	}
	tree, err := ActivationCLI{Runner: fake, Checkout: "/m", Worktree: dir}.Prepare(context.Background(), "aaaa")
	if err != nil || tree != dir {
		t.Fatalf("tree %q, err %v", tree, err)
	}
}

func TestActivationPrepareAddsAMissingWorktree(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "tree")
	fake := &shelltest.Fake{}
	fake.Reply("", "git", "-C", "/m", "fetch", "origin", "aaaa")
	fake.Reply("", "git", "-C", "/m", "worktree", "add", "--detach", missing, "aaaa")
	fake.Reply("", "bun", "install", "--cwd", filepath.Join(missing, "infra", "ci"), "--frozen-lockfile")
	fake.Reply("aaaa\n", "git", "-C", missing, "rev-parse", "HEAD")
	if _, err := (ActivationCLI{Runner: fake, Checkout: "/m", Worktree: missing}).Prepare(context.Background(), "aaaa"); err != nil {
		t.Fatal(err)
	}
	if calls := fake.Calls(); len(calls) != 4 {
		t.Fatalf("calls %+v", calls)
	}
}

func TestRecordsReuseAnInvestigationAndLogAlreadyLabelledForTheIncident(t *testing.T) {
	listed := `[{"id": "abcdef0123", "title": "t"}]`
	fake := &shelltest.Fake{}
	fake.Reply(listed, "ccn", "-R", "/m", "investigation", "list")
	fake.Reply(listed, "ccn", "-R", "/m", "log", "list")
	records, err := RecordsCLI{Runner: fake, Repo: "/m"}.Open(context.Background(), "x1", "t", "p")
	if err != nil || records != (RecordIDs{Investigation: "abcdef0", Log: "abcdef0"}) {
		t.Fatalf("records %+v, err %v", records, err)
	}
	calls := fake.Calls()
	if len(calls) != 2 {
		t.Fatalf("calls %+v", calls)
	}
	for _, call := range calls {
		if !strings.Contains(strings.Join(call.Argv, " "), "incident:x1") {
			t.Fatalf("call %v does not name the label", call.Argv)
		}
	}
}

func TestRecordsOpenWhatIsMissingAndWriteEntriesAndVerdicts(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("[]", "ccn", "-R", "/m", "investigation", "list")
	fake.Reply(`{"id": "1111111aaa"}`, "ccn", "-R", "/m", "investigation", "open")
	fake.Reply("[]", "ccn", "-R", "/m", "log", "list")
	fake.Reply(`{"id": "2222222bbb"}`, "ccn", "-R", "/m", "log", "add")
	fake.Reply("{}", "ccn", "-R", "/m", "log", "append")
	fake.Reply("{}", "ccn", "-R", "/m", "investigation", "fix")
	records := RecordsCLI{Runner: fake, Repo: "/m"}
	ctx := context.Background()
	ids, err := records.Open(ctx, "x1", "t", "p")
	if err != nil || ids != (RecordIDs{Investigation: "1111111", Log: "2222222"}) {
		t.Fatalf("ids %+v, err %v", ids, err)
	}
	if err := records.Entry(ctx, "2222222", "text", "/b.md"); err != nil {
		t.Fatal(err)
	}
	if err := records.Verdict(ctx, "1111111", "fix", "text", "sha"); err != nil {
		t.Fatal(err)
	}
	calls := fake.Calls()
	entry := strings.Join(calls[len(calls)-2].Argv, " ")
	if !strings.Contains(entry, "--attach /b.md --replace --json") {
		t.Fatalf("entry %s", entry)
	}
	if verdict := strings.Join(calls[len(calls)-1].Argv, " "); !strings.Contains(verdict, "investigation fix 1111111 text --commit sha --json") {
		t.Fatalf("verdict %s", verdict)
	}
}

func TestRenderBriefFillsEveryPlaceholder(t *testing.T) {
	text, err := renderBrief(RoleFix, map[string]string{"fix lane name": "incident-x-fix", "incident id": "x", "break-glass skill": "break-glass"})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"  through the release skill or break-glass, never after a dry run",
		`env -u AWS_PROFILE tools/ci break-glass --reason "INCIDENT x: WHY" --stack DOMAIN/ENV`,
		"incident-x-fix",
		"lr incident note --incident x --pr <PR number>",
		"A regular release is the default. Use `--emergency` only when the regular path",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("brief lacks %q", want)
		}
	}
	if left := placeholder.FindString(text); left != "" {
		t.Fatalf("placeholder %s left", left)
	}
}

func TestWriteBriefWritesTheFixAndEvidenceBriefs(t *testing.T) {
	dir := t.TempDir()
	common := filepath.Join(dir, "common.md")
	if err := os.WriteFile(common, []byte("the drive's contract"), 0o600); err != nil {
		t.Fatal(err)
	}
	facts := Facts{Reported: reportedStamp, Adopted: map[string]string{RoleEvidence: "plat-evidence"}, Common: common}
	briefs := filepath.Join(dir, "x")
	fix, err := writeBrief("x", facts, RoleFix, briefs)
	if err != nil {
		t.Fatal(err)
	}
	if fix != filepath.Join(briefs, "incident-x-fix.full.md") {
		t.Fatalf("path %s", fix)
	}
	text := readFile(t, fix)
	if !strings.HasPrefix(text, "the drive's contract\n") || !strings.Contains(text, "incident-x-fix") || !strings.Contains(text, "plat-evidence") {
		t.Fatalf("brief:\n%s", text)
	}
	if left := placeholder.FindString(text); left != "" {
		t.Fatalf("placeholder %s left", left)
	}
	backup, err := writeBrief("x", facts, RoleBackup, briefs)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(readFile(t, backup), "ccx: role=fix") {
		t.Fatalf("the backup brief is not the fix brief")
	}
}

func TestOpenFillsDefaultsAndValidatesItsTimes(t *testing.T) {
	s := Store{Root: t.TempDir()}
	now := time.Date(2026, 10, 1, 17, 20, 0, 0, time.UTC)
	base := Opening{Kind: KindAlert, Target: "api", Thread: "t", Onset: "2026-10-01T16:45:36Z", Reported: reportedStamp, Bus: "b", CommsLane: "c", RootLane: "r", Checkout: "/m"}
	id, err := s.Open(base, now)
	if err != nil {
		t.Fatal(err)
	}
	if id != "10011720" {
		t.Fatalf("id %s", id)
	}
	facts, err := s.Facts(id, "alert", "code_path", "runbook", "adopted", "canary_job", "pipeline", "review_surface", "routes")
	if err != nil {
		t.Fatal(err)
	}
	if facts.Alert != "t" || facts.CodePath != "the api service" || facts.Runbook != "" || len(facts.Adopted) != 0 || facts.CanaryJob != "" || facts.Pipeline != "" || facts.ReviewSurface != "thread" {
		t.Fatalf("facts %+v", facts)
	}
	if !reflect.DeepEqual(facts.Routes[RoleBackup], []string{"opus", "xhigh"}) {
		t.Fatalf("routes %v", facts.Routes)
	}
	withLinks := base
	withLinks.ID, withLinks.Alert, withLinks.Runbook, withLinks.Adopted = "x2", "https://forge-rf.sentry.io/issues/7766636402/", "4950740", map[string]string{RoleFix: "api-1n85-fix"}
	if _, err := s.Open(withLinks, now); err != nil {
		t.Fatal(err)
	}
	if facts, err = s.Facts("x2", "alert", "runbook", "adopted"); err != nil {
		t.Fatal(err)
	}
	if facts.Alert != withLinks.Alert || facts.Runbook != "4950740" || facts.Adopted[RoleFix] != "api-1n85-fix" {
		t.Fatalf("facts %+v", facts)
	}
	noTarget := base
	noTarget.Target = ""
	if _, err := s.Open(noTarget, now); err == nil || !strings.Contains(err.Error(), "names no pipeline") {
		t.Fatalf("got %v", err)
	}
	badTime := base
	badTime.Onset = "yesterday"
	if _, err := s.Open(badTime, now); err == nil || !strings.Contains(err.Error(), "--onset") {
		t.Fatalf("got %v", err)
	}
	unknown := base
	unknown.Kind = "page"
	if _, err := s.Open(unknown, now); err == nil {
		t.Fatal("an unknown kind was opened")
	}
}

func TestLostResponsesAreTheFailuresThatMayHaveHappened(t *testing.T) {
	if !isLost(fmt.Errorf("x: %w", ErrResponseLost)) || !isLost(context.DeadlineExceeded) {
		t.Fatal("a lost response was not lost")
	}
	if isLost(errors.New("no reply scripted")) || isLost(nil) {
		t.Fatal("a plain failure read as lost")
	}
}
