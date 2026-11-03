package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	promotionURL = "https://buildkite.com/org/release/builds/12272"
	tokenTool    = "print-read-token"
)

var promotionStarted = time.Date(2026, 11, 2, 23, 30, 0, 0, pacificZone)

func buildFeed(state, jobs string) string {
	return fmt.Sprintf(`[{"number":12272,"state":%q,"web_url":%q,"started_at":%q,"jobs":[%s]}]`, state, promotionURL, promotionStarted.Format(time.RFC3339), jobs)
}

func namingRecord(t *testing.T, seq int64, lane, text string) client.Record {
	t.Helper()
	var row client.Record
	body := fmt.Sprintf(`{"seq":%d,"lane":%q,"kind":"release","at":%q,"text":%q}`, seq, lane, promotionStarted.Add(time.Minute).Format(time.RFC3339), text)
	if err := json.Unmarshal([]byte(body), &row); err != nil {
		t.Fatal(err)
	}
	return row
}

func releaseBuilds(t *testing.T, feed *string, rows ...client.Record) (*ReleaseBuilds, *fakeBus) {
	t.Helper()
	return releaseBuildsIn(t, memory(t), feed, rows...)
}

func releaseBuildsIn(t *testing.T, s *store.Store, feed *string, rows ...client.Record) (*ReleaseBuilds, *fakeBus) {
	t.Helper()
	bus := &fakeBus{tail: rows}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive, Checkout: "/checkout"}, Runner: scripted(func(argv []string) (shell.Result, error) {
		if argv[0] == tokenTool {
			return shell.Result{Stdout: "read-token\n"}, nil
		}
		contains(t, strings.Join(argv, " "), "tools/ci bk api /organizations/org/pipelines/release/builds?per_page=20")
		return shell.Result{Stdout: *feed}, nil
	})}
	return &ReleaseBuilds{Env: env, Store: s, Pipelines: []string{"org/release"}, CLI: []string{"tools/ci", "bk"}, Token: bkread.Token{Command: []string{tokenTool}}, Log: slog.Default()}, bus
}

func watchBuilds(t *testing.T, check *ReleaseBuilds, at time.Time) {
	t.Helper()
	if err := check.Check(context.Background(), at); err != nil {
		t.Fatal(err)
	}
}

func TestARunningReleaseBuildNoRecordNamesPostsOneSeenToTheCoordinatorAtFiveMinutes(t *testing.T) {
	feed := buildFeed("running", "")
	check, bus := releaseBuilds(t, &feed, namingRecord(t, 1, "release-5", "12204 started https://buildkite.com/org/release/builds/12204"))

	watchBuilds(t, check, promotionStarted.Add(seenGrace-time.Minute))
	equal(t, len(bus.posted), 0)

	watchBuilds(t, check, promotionStarted.Add(seenGrace))
	equal(t, len(bus.posted), 1)
	seen := bus.posted[0]
	equal(t, seen.Kind, blockerKind)
	equal(t, seen.To, []string{Root})
	equal(t, seen.ID, "release-seen:buildkite.com/org/release/builds/12272")
	contains(t, seen.Text, "RELEASE-SEEN 12272 org/release running: no record names "+promotionURL+" 5 minutes after it started; claim it")

	feed = buildFeed("blocked", "")
	watchBuilds(t, check, promotionStarted.Add(seenGrace+time.Minute))
	equal(t, len(bus.posted), 1)
}

func TestAReleaseBuildThatAlreadyPassedNeedsNoClaim(t *testing.T) {
	feed := buildFeed("passed", "")
	check, bus := releaseBuilds(t, &feed)

	watchBuilds(t, check, promotionStarted.Add(seenGrace))
	equal(t, len(bus.posted), 0)
}

func TestARunningReleaseBuildARecordNamesPostsNothing(t *testing.T) {
	feed := buildFeed("running", "")
	check, bus := releaseBuilds(t, &feed, namingRecord(t, 1, "release-5", "promotion started "+promotionURL))

	watchBuilds(t, check, promotionStarted.Add(seenGrace))
	watchBuilds(t, check, promotionStarted.Add(2*seenGrace))
	equal(t, len(bus.posted), 0)
}

func TestAFailedReleaseBuildPostsOneFailedNamingItsStepToTheCoordinatorAndTheReleaseLane(t *testing.T) {
	jobs := strings.Join([]string{
		`{"name":"plan","state":"passed","soft_failed":false,"step_key":"plan"}`,
		`{"name":"lint","state":"failed","soft_failed":true,"step_key":"lint"}`,
		`{"name":":rocket: apply api","state":"failed","soft_failed":false,"step_key":"apply-api--prod"}`,
		`{"name":"verify","state":"broken","soft_failed":false,"step_key":"verify"}`,
	}, ",")
	feed := buildFeed("failed", jobs)
	check, bus := releaseBuilds(t, &feed, namingRecord(t, 1, Root, "watching "+promotionURL), namingRecord(t, 2, "release-5", "promotion started "+promotionURL))

	watchBuilds(t, check, promotionStarted.Add(20*time.Minute))
	watchBuilds(t, check, promotionStarted.Add(21*time.Minute))

	equal(t, len(bus.posted), 1)
	failed := bus.posted[0]
	equal(t, failed.To, []string{Root, "release-5"})
	equal(t, failed.ID, "release-failed:buildkite.com/org/release/builds/12272:apply-api--prod")
	equal(t, failed.Text, "RELEASE-FAILED 12272 apply-api--prod "+promotionURL)
}

func TestAnAutomaticStagingPromotionNeedsNoClaim(t *testing.T) {
	feed := fmt.Sprintf(`[{"number":12272,"state":"running","web_url":%q,"source":"trigger_job","creator":null,"started_at":%q,"jobs":[]}]`, promotionURL, promotionStarted.Format(time.RFC3339))
	check, bus := releaseBuilds(t, &feed)

	watchBuilds(t, check, promotionStarted.Add(seenGrace))
	watchBuilds(t, check, promotionStarted.Add(2*seenGrace))
	equal(t, len(bus.posted), 0)
}

func TestADryRunNeedsNoClaimAndARealReleaseStillDoes(t *testing.T) {
	for mode, want := range map[string]int{"dry-run": 0, "release": 1} {
		t.Run(mode, func(t *testing.T) {
			start, err := json.Marshal(map[string]string{"mode": mode})
			if err != nil {
				t.Fatal(err)
			}
			feed := fmt.Sprintf(`[{"number":12272,"state":"running","web_url":%q,"message":"release check of dev","started_at":%q,"env":{"RELEASE_START":%q},"jobs":[]}]`, promotionURL, promotionStarted.Format(time.RFC3339), start)
			check, bus := releaseBuilds(t, &feed)

			watchBuilds(t, check, promotionStarted.Add(seenGrace))
			equal(t, len(bus.posted), want)
		})
	}
}

func TestAFailedReleaseBuildWaitsOnItsResumeAndPostsNothingWhenTheResumePasses(t *testing.T) {
	resume := func(state string) string {
		start, err := json.Marshal(map[string]string{"mode": "release", "replans": "releases/2026-11-02/3"})
		if err != nil {
			t.Fatal(err)
		}
		return fmt.Sprintf(`[
			{"number":12276,"state":%q,"web_url":"https://buildkite.com/org/release/builds/12276","branch":"releases/2026-11-02/4","started_at":%q,"env":{"RELEASE_START":%q},"jobs":[]},
			{"number":12272,"state":"failed","web_url":%q,"branch":"releases/2026-11-02/3","started_at":%q,"jobs":[{"name":"apply","state":"failed","soft_failed":false,"step_key":"apply-api--prod"}]}
		]`, state, promotionStarted.Add(10*time.Minute).Format(time.RFC3339), start, promotionURL, promotionStarted.Format(time.RFC3339))
	}
	feed := resume("running")
	check, bus := releaseBuilds(t, &feed, namingRecord(t, 1, "release-6", "resuming as https://buildkite.com/org/release/builds/12276"), namingRecord(t, 2, "release-6", "promotion started "+promotionURL))

	watchBuilds(t, check, promotionStarted.Add(20*time.Minute))
	feed = resume("passed")
	watchBuilds(t, check, promotionStarted.Add(21*time.Minute))

	equal(t, len(bus.posted), 0)
}

func TestALiveReleaseLaneHearsReleaseAlertsInsteadOfTheCoordinator(t *testing.T) {
	s := memory(t)
	registered(t, s, store.Lane{Name: "release-5", Role: roleRelease, State: store.LaneRetired})
	registered(t, s, store.Lane{Name: "release-6", Role: roleRelease})
	feed := buildFeed("running", "")
	check, bus := releaseBuildsIn(t, s, &feed)

	watchBuilds(t, check, promotionStarted.Add(seenGrace))
	feed = buildFeed("failed", `{"name":"apply","state":"failed","soft_failed":false,"step_key":"apply-api--prod"}`)
	watchBuilds(t, check, promotionStarted.Add(seenGrace+time.Minute))

	equal(t, len(bus.posted), 2)
	contains(t, bus.posted[0].Text, "RELEASE-SEEN 12272")
	equal(t, bus.posted[0].To, []string{"release-6"})
	equal(t, bus.posted[1].Text, "RELEASE-FAILED 12272 apply-api--prod "+promotionURL)
	equal(t, bus.posted[1].To, []string{"release-6"})
}

func TestAFailedBuildAnotherLaneNamedReachesThatLaneBesideTheReleaseLane(t *testing.T) {
	s := memory(t)
	registered(t, s, store.Lane{Name: "release-6", Role: roleRelease})
	feed := buildFeed("failed", `{"name":"apply","state":"failed","soft_failed":false,"step_key":"apply-api--prod"}`)
	check, bus := releaseBuildsIn(t, s, &feed, namingRecord(t, 1, "hotfix-2", "hotfix started "+promotionURL))

	watchBuilds(t, check, promotionStarted.Add(seenGrace))

	equal(t, len(bus.posted), 1)
	equal(t, bus.posted[0].To, []string{"release-6", "hotfix-2"})
}

func TestReleaseAlertsGoToTheCoordinatorWhenNoReleaseLaneIsLive(t *testing.T) {
	s := memory(t)
	registered(t, s, store.Lane{Name: "release-6", Role: roleRelease, State: store.LaneRetired})
	feed := buildFeed("running", "")
	check, bus := releaseBuildsIn(t, s, &feed)

	watchBuilds(t, check, promotionStarted.Add(seenGrace))
	feed = buildFeed("failed", `{"name":"apply","state":"failed","soft_failed":false,"step_key":"apply-api--prod"}`)
	watchBuilds(t, check, promotionStarted.Add(seenGrace+time.Minute))

	equal(t, len(bus.posted), 2)
	equal(t, bus.posted[0].To, []string{Root})
	equal(t, bus.posted[1].To, []string{Root})
}

func TestOneFailingStepAlertsOnceAcrossFailingAndFailedAndASecondStepAlertsAgain(t *testing.T) {
	apply := `{"name":"apply","state":"failed","soft_failed":false,"step_key":"apply-api--prod"}`
	verify := `{"name":"verify","state":"timed_out","soft_failed":false,"step_key":"verify-api--prod"}`
	feed := buildFeed("failing", apply)
	check, bus := releaseBuilds(t, &feed, namingRecord(t, 1, "release-6", "promotion started "+promotionURL))

	watchBuilds(t, check, promotionStarted.Add(10*time.Minute))
	feed = buildFeed("failed", apply)
	watchBuilds(t, check, promotionStarted.Add(12*time.Minute))
	equal(t, len(bus.posted), 1)
	equal(t, bus.posted[0].Text, "RELEASE-FAILED 12272 apply-api--prod "+promotionURL)

	feed = buildFeed("failed", apply+","+verify)
	watchBuilds(t, check, promotionStarted.Add(14*time.Minute))
	equal(t, len(bus.posted), 2)
	equal(t, bus.posted[1].Text, "RELEASE-FAILED 12272 verify-api--prod "+promotionURL)
}
