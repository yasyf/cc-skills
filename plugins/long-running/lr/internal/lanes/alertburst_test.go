package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var (
	releaseApplied = time.Unix(1794046170, 0).UTC()
	sandsqlChanged = []struct{ ts, title, monitor string }{
		{"1794046138.792139", "SandSQL Primary Endpoint Availability", "325143412"},
		{"1794046139.655109", "SandSQL API unready", "315028137"},
		{"1794046140.567129", "SandSQL primary unready", "317526841"},
	}
)

type alertChannel struct{ messages []slackMessage }

func (c *alertChannel) say(ts, text string) {
	c.messages = append([]slackMessage{{TS: ts, Text: text, Permalink: "https://poetic-technology.slack.com/archives/C0822AHFY3G/p" + strings.ReplaceAll(ts, ".", "")}}, c.messages...)
}

func (c *alertChannel) run([]string) (shell.Result, error) {
	history, err := json.Marshal(map[string]any{"messages": c.messages})
	return shell.Result{Stdout: string(history)}, err
}

func datadogMessage(tag, title, monitor string) string {
	return fmt.Sprintf("%s %s — {{forge_cluster.name}}\n<https://app.datadoghq.com/monitors/%s?group=plat|monitor>", tag, title, monitor)
}

func alertWatch(t *testing.T, channel *alertChannel, bus *fakeBus, launched *[]Fired) *Alerts {
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(channel.run)}
	return &Alerts{Env: env, Store: memory(t), Channels: []string{"C0822AHFY3G"}, Log: slog.Default(), Launch: func(_ context.Context, fired Fired) (string, error) {
		*launched = append(*launched, fired)
		return "/state/lanes/" + fired.Slug + "-fix.brief.md", nil
	}}
}

func TestThreeMonitorModifiedNoticesInOneSecondLaunchNothingAndPostOneRecord(t *testing.T) {
	channel, bus := &alertChannel{}, &fakeBus{}
	for _, changed := range sandsqlChanged {
		channel.say(changed.ts, datadogMessage("[Monitor Modified]", changed.title, changed.monitor))
	}
	var launched []Fired
	check := alertWatch(t, channel, bus, &launched)

	for tick := range 2 {
		if err := check.Check(context.Background(), releaseApplied.Add(time.Duration(tick)*30*time.Second)); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, len(launched), 0)
	equal(t, len(bus.posted), 1)
	posted := bus.posted[0]
	equal(t, []any{posted.Kind, posted.Topic, posted.To, len(posted.LaneRefs)}, []any{"note", "dd-325143412", []string{Root}, 0})
	contains(t, posted.Text, "ALERT-CHANGED [Monitor Modified] SandSQL Primary Endpoint Availability — {{forge_cluster.name}}: Datadog changed the definition of https://app.datadoghq.com/monitors/325143412, https://app.datadoghq.com/monitors/315028137, https://app.datadoghq.com/monitors/317526841")
}

func TestAlertsOnOneComponentWithinAMinuteShareOneLaneSetNamingEveryMonitor(t *testing.T) {
	for _, n := range []int{len(sandsqlChanged), scaletest.N()} {
		t.Run(fmt.Sprint(n), func(t *testing.T) {
			channel, bus := &alertChannel{}, &fakeBus{}
			var monitors []string
			for i := range n {
				known := sandsqlChanged[i%len(sandsqlChanged)]
				monitor := known.monitor
				if i >= len(sandsqlChanged) {
					monitor = fmt.Sprint(400000000 + i)
				}
				monitors = append(monitors, "https://app.datadoghq.com/monitors/"+monitor)
				channel.say(fmt.Sprintf("%.6f", 1794046138.792139+float64(i)*0.05), datadogMessage("Triggered:", known.title, monitor))
			}
			var launched []Fired
			check := alertWatch(t, channel, bus, &launched)

			if err := check.Check(context.Background(), releaseApplied.Add(time.Minute)); err != nil {
				t.Fatal(err)
			}
			channel.say("1794046195.000100", datadogMessage("Triggered:", "SandSQL replica unready", "315028145"))
			if err := check.Check(context.Background(), releaseApplied.Add(90*time.Second)); err != nil {
				t.Fatal(err)
			}

			equal(t, len(launched), 1)
			equal(t, launched[0].Slug, "dd-325143412")
			equal(t, len(launched[0].Facts), n-1)
			equal(t, len(bus.posted), 2)
			launch, joined := bus.posted[0], bus.posted[1]
			equal(t, []any{launch.Kind, launch.LaneRefs}, []any{kindSpawn, []string{"dd-325143412-fix"}})
			contains(t, launch.Text, "no incident record named "+strings.Join(monitors, ", ")+" ")
			equal(t, []any{joined.Kind, joined.To}, []any{"note", []string{"dd-325143412-fix"}})
			contains(t, joined.Text, "ALERT-JOINED Triggered: SandSQL replica unready — {{forge_cluster.name}}: https://app.datadoghq.com/monitors/315028145 fired 1 min ago on sandsql within a minute of https://app.datadoghq.com/monitors/325143412")
		})
	}
}

func TestAlertsOnTwoComponentsInOneSecondLaunchTwoLaneSets(t *testing.T) {
	channel, bus := &alertChannel{}, &fakeBus{}
	channel.say("1794046138.792139", datadogMessage("Triggered:", "SandSQL primary unready", "317526841"))
	channel.say("1794046139.100000", datadogMessage("Triggered:", "runtime-api-next logging errors", "338724738"))
	var launched []Fired
	check := alertWatch(t, channel, bus, &launched)

	if err := check.Check(context.Background(), releaseApplied); err != nil {
		t.Fatal(err)
	}

	equal(t, len(launched), 2)
	equal(t, []string{launched[0].Slug, launched[1].Slug}, []string{"dd-338724738", "dd-317526841"})
	equal(t, len(bus.posted), 2)
}
