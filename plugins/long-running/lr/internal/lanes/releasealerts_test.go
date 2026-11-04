package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/yasyf/cc-inbox/client"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var releaseNow = time.Unix(1793742600, 0).UTC()

const (
	executorSlug = "watch-executor-plat-467149"
	redSlug      = "red-executor"
	sandsqlSlug  = "watch-sandsql-sofi-53c9f4"
	smokeSlug    = "smoke-tenant-smoke-test-aig"
)

type releaseChannel struct {
	history string
	threads map[string]string
	reads   []string
}

func realReleaseChannel(t *testing.T) *releaseChannel {
	c := &releaseChannel{history: fixture(t, "history.json"), threads: map[string]string{}}
	for _, ts := range []string{"1793740168.770509", "1793742359.647299"} {
		c.threads["https://forge.slack.com/archives/C0B75AL4XEW/p"+strings.ReplaceAll(ts, ".", "")] = fixture(t, "thread-"+ts+".json")
	}
	return c
}

func fixture(t *testing.T, name string) string {
	text, err := os.ReadFile(filepath.Join("testdata", "release-alerts", name))
	if err != nil {
		t.Fatal(err)
	}
	return string(text)
}

func (c *releaseChannel) run(argv []string) (shell.Result, error) {
	c.reads = append(c.reads, argv[1])
	if argv[1] == "history" {
		return shell.Result{Stdout: c.history}, nil
	}
	return shell.Result{Stdout: c.threads[argv[slices.Index(argv, "--url")+1]]}, nil
}

func (c *releaseChannel) reply(t *testing.T, thread, ts, text string) {
	var parsed map[string]any
	if err := json.Unmarshal([]byte(c.threads[thread]), &parsed); err != nil {
		t.Fatal(err)
	}
	parsed["messages"] = append(parsed["messages"].([]any), map[string]any{"ts": ts, "text": text})
	threaded, err := json.Marshal(parsed)
	if err != nil {
		t.Fatal(err)
	}
	c.threads[thread] = string(threaded)
	c.history = strings.Replace(c.history, `"reply_count":2,"latest_reply":"1793740290.000200"`, `"reply_count":3,"latest_reply":"`+ts+`"`, 1)
}

func releaseAlerts(t *testing.T, channel *releaseChannel, bus *fakeBus, launch func(context.Context, Fired) (string, error)) *Alerts {
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(channel.run)}
	return &Alerts{Env: env, Store: memory(t), Channels: []string{"C0B75AL4XEW"}, Log: slog.Default(), Launch: launch}
}

func TestEachRedWatchAndRedSmokeInTheReleasesChannelLaunchesOnceAcrossTicks(t *testing.T) {
	channel, bus := realReleaseChannel(t), &fakeBus{}
	var launched []Fired
	check := releaseAlerts(t, channel, bus, func(_ context.Context, fired Fired) (string, error) {
		launched = append(launched, fired)
		return "/state/lanes/" + fired.Slug + "-fix.brief.md", nil
	})

	for tick := range 3 {
		if err := check.Check(context.Background(), releaseNow.Add(time.Duration(tick)*30*time.Second)); err != nil {
			t.Fatal(err)
		}
	}

	slugs := make([]string, 0, len(launched))
	for _, fired := range launched {
		slugs = append(slugs, fired.Slug)
	}
	equal(t, slugs, []string{smokeSlug, sandsqlSlug, redSlug})
	equal(t, len(bus.posted), 3)
	for _, posted := range bus.posted {
		equal(t, posted.Kind, kindSpawn)
	}
	executor := launched[2]
	equal(t, executor.Link, "https://forge.slack.com/archives/C0B75AL4XEW/p1793740168770509")
	equal(t, executor.Facts, []string{"component Executor", "env plat", "released by U05FTPXU8PP"})
	equal(t, launched[0].Facts, []string{"component Tenant smoke test", "env AIG", "monitor Tenant smoke test", "release ultraviolet-wainaina", "released by U05FTPXU8PP"})
	equal(t, strings.Join(channel.reads, " "), "history thread thread history history")
}

func TestAWatchPostNamesItsMonitorReleaseAndJob(t *testing.T) {
	var thread struct {
		Messages []slackMessage `json:"messages"`
	}
	if err := json.Unmarshal([]byte(realReleaseChannel(t).threads["https://forge.slack.com/archives/C0B75AL4XEW/p1793740168770509"]), &thread); err != nil {
		t.Fatal(err)
	}
	found, ok := alertOf(thread.Messages[0], thread.Messages[2])
	equal(t, ok, true)
	equal(t, found.slug, executorSlug)
	equal(t, found.red, redSlug)
	equal(t, found.facts, []string{
		"component Executor",
		"env plat",
		"monitor Runner refusing runs whose journal cannot open on",
		"release pensive-vallespir",
		"released by U05FTPXU8PP",
		"job https://buildkite.com/forge/release/builds/12404#01a1a349-af4e-4b67-b4e8-893cbba19b3c",
	})
}

func TestARedPostOnAThreadWithAnIncidentOpensNothing(t *testing.T) {
	thread := "https://forge.slack.com/archives/C0B75AL4XEW/p1793740168770509"
	for name, opened := range map[string]client.Record{
		"launch record on the thread": {Kind: kindSpawn, Text: "ALERT-LAUNCHED something " + thread},
		"incident naming the thread":  {Kind: "incident", Text: "incident declared, see " + thread},
	} {
		t.Run(name, func(t *testing.T) {
			channel, bus := realReleaseChannel(t), &fakeBus{tail: []client.Record{opened}}
			var slugs []string
			check := releaseAlerts(t, channel, bus, func(_ context.Context, fired Fired) (string, error) {
				slugs = append(slugs, fired.Slug)
				return "/state/lanes/" + fired.Slug + "-fix.brief.md", nil
			})

			if err := check.Check(context.Background(), releaseNow); err != nil {
				t.Fatal(err)
			}

			equal(t, slugs, []string{smokeSlug, sandsqlSlug})
		})
	}
}

func TestTheReleaseBotsRedPostsEachOpenAnAlert(t *testing.T) {
	for _, c := range []struct{ text, slug, monitor string }{
		{":rotating_light: API went red after 1 min: its Unstarted Run Claim Count check expected at most 2 but got 4. :rotating_light: Executor went red on plat after 3 min.", "red-api", ""},
		{":rotating_light: Executor went red on plat after 3 min.", redSlug, ""},
		{":rotating_light: Restate worker data plane went red on SoFi and AIG after 1 min.", "red-restate-worker-data-plane", ""},
		{`:x: The Watch failed for Restate worker data plane on SoFi: "Unstarted Run Claim Count", "Unstarted Run Claim Total" crossed its monitor's bound`, "", "monitor Unstarted Run Claim Count, Unstarted Run Claim Total"},
	} {
		found, ok := alertOf(slackMessage{}, slackMessage{Text: c.text})
		if !ok {
			t.Fatalf("%q opened nothing", c.text)
		}
		if c.slug != "" {
			equal(t, found.slug, c.slug)
		}
		if c.monitor != "" && !slices.Contains(found.facts, c.monitor) {
			t.Fatalf("%q facts = %v, want %q", c.text, found.facts, c.monitor)
		}
	}
	for _, passed := range []string{
		":white_check_mark: Sand CLI passed its 5-min watch after going live: no monitor fired, nothing rolled back.",
		":white_check_mark: hushed-carrington is live",
	} {
		if found, ok := alertOf(slackMessage{}, slackMessage{Text: passed}); ok {
			t.Fatalf("%q opened %v", passed, found)
		}
	}
}

func TestEveryWatchFailureAfterARedPostJoinsTheLaneItOpened(t *testing.T) {
	channel, bus := realReleaseChannel(t), &fakeBus{}
	var check *Alerts
	var launched []string
	check = releaseAlerts(t, channel, bus, func(_ context.Context, fired Fired) (string, error) {
		launched = append(launched, fired.Slug)
		_, err := check.Store.WantLane(storeLane(fired.Slug+"-fix"), releaseNow)
		return "/state/lanes/" + fired.Slug + "-fix.brief.md", err
	})
	if err := check.Check(context.Background(), releaseNow); err != nil {
		t.Fatal(err)
	}
	executorThread := "https://forge.slack.com/archives/C0B75AL4XEW/p1793740168770509"
	channel.reply(t, executorThread, "1793742610.000300", `:x: The Watch failed for Executor on plat: "Runner refusing runs whose journal cannot open on" crossed its monitor's bound since the release started. Nothing else in pensive-vallespir waits on it.`)

	for tick := 1; tick <= 3; tick++ {
		if err := check.Check(context.Background(), releaseNow.Add(time.Duration(tick)*30*time.Second)); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, len(launched), 3)
	joined := bus.posted[3:]
	equal(t, len(joined), 2)
	for _, note := range joined {
		equal(t, []any{note.Kind, note.To}, []any{"note", []string{redSlug + "-fix"}})
		contains(t, note.Text, "ALERT-AGAIN :x: The Watch failed for Executor on plat")
	}
}

func TestSlackReadsPerTickStayFlatAsTheReleasesChannelGrows(t *testing.T) {
	reads := func(n int) int {
		cards := make([]slackMessage, n)
		for i := range cards {
			ts := fmt.Sprintf("%d.%06d", releaseNow.Add(-mins(10)).Unix(), i)
			cards[i] = slackMessage{TS: ts, Text: ":white_check_mark: quiet-hopper is live", Permalink: "https://forge.slack.com/archives/C0B75AL4XEW/p" + strings.ReplaceAll(ts, ".", ""), ReplyCount: 1, LatestReply: ts}
		}
		history, err := json.Marshal(map[string]any{"messages": cards})
		if err != nil {
			t.Fatal(err)
		}
		channel := &releaseChannel{history: string(history), threads: map[string]string{}}
		for _, card := range cards {
			channel.threads[card.Permalink] = `{"messages":[]}`
		}
		check := releaseAlerts(t, channel, &fakeBus{}, func(context.Context, Fired) (string, error) {
			t.Fatal("launched")
			return "", nil
		})
		if err := check.Check(context.Background(), releaseNow); err != nil {
			t.Fatal(err)
		}
		channel.reads = nil
		if err := check.Check(context.Background(), releaseNow.Add(30*time.Second)); err != nil {
			t.Fatal(err)
		}
		return len(channel.reads)
	}

	equal(t, reads(1), reads(1000))
	equal(t, reads(1), 1)
}

func TestAFailedLaunchOfAReleaseAlertRaisesALERTUNOPENED(t *testing.T) {
	channel, bus := realReleaseChannel(t), &fakeBus{}
	check := releaseAlerts(t, channel, bus, func(context.Context, Fired) (string, error) { return "", errors.New("lr incident run exited 2") })

	for range 2 {
		if err := check.Check(context.Background(), releaseNow); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, len(bus.posted), 4)
	equal(t, bus.posted[0].Kind, blockerKind)
	contains(t, bus.posted[0].Text, "ALERT-UNOPENED :warning: ultraviolet-wainaina is live, but Tenant smoke test on AIG went red")
	contains(t, bus.posted[0].Text, "launch of "+smokeSlug+"-fix failed: lr incident run exited 2")
}

func TestReleaseChannelLinesThatAreNotARedWatchOrSmokeAreNotAlerts(t *testing.T) {
	for _, text := range []string{
		":white_check_mark: quiet-hopper is live · <@U01QUIET> via `claude` · `api`",
		":hourglass_flowing_sand: pensive-vallespir is running · <@U05FTPXU8PP> via `claude` · `executor`",
		":no_entry: brave-curie was cancelled by <@U02BRAVE>",
		":mag: Core database read by <@U03READER>: why the AIG smoke team has no runs",
		":lock: Break-glass held by <@U04HOLDER> on plat for 60 min",
		":x: calm-noether failed: the api typecheck gate failed · <@U05CALM> via `claude` · `api`",
		":warning: calm-noether partly released: the flaky router test failed twice",
		"is anyone looking at why the executor watch went red?",
	} {
		if _, ok := alertOf(slackMessage{Text: text}, slackMessage{Text: text}); ok {
			t.Errorf("%q is an alert", text)
		}
	}
}
