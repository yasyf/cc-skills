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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const promotionURL = "https://buildkite.com/org/release/builds/12272"

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
	bus := &fakeBus{tail: rows}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive, Checkout: "/checkout"}, Runner: scripted(func(argv []string) (shell.Result, error) {
		contains(t, strings.Join(argv, " "), "tools/ci bk api /organizations/org/pipelines/release/builds?per_page=20")
		return shell.Result{Stdout: *feed}, nil
	})}
	return &ReleaseBuilds{Env: env, Pipelines: []string{"org/release"}, CLI: []string{"tools/ci", "bk"}, Log: slog.Default()}, bus
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
	equal(t, failed.ID, "release-failed:buildkite.com/org/release/builds/12272:failed")
	equal(t, failed.Text, "RELEASE-FAILED 12272 apply-api--prod "+promotionURL)
}
