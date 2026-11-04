package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const monitorLink = "more <https://app.datadoghq.com/monitors/338724738?group=plat|monitor>"

func slackHistory(fired time.Time, text string) string {
	return fmt.Sprintf(`{"messages":[{"ts":"%.6f","text":%q,"permalink":"https://slack/p1"}]}`, float64(fired.UnixNano())/float64(time.Second), text)
}

func TestAnAlertNobodyAnsweredThreeMinutesOnLaunchesItsLanes(t *testing.T) {
	triggered := "Triggered: runtime-api-next logging errors — plat\n" + monitorLink
	cases := map[string]struct {
		text     string
		minutes  int
		incident string
		launched bool
	}{
		"unopened":     {triggered, 4, "", true},
		"too-old":      {triggered, 61, "", false},
		"opened":       {triggered, 4, "INCIDENT OPEN: Datadog 338724738", false},
		"recovered":    {"Recovered: runtime-api-next logging errors — plat\n" + monitorLink, 4, "", false},
		"not-an-alert": {"the deploy finished", 4, "", false},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			bus := &fakeBus{}
			if c.incident != "" {
				bus.tail = []client.Record{{Seq: 7, Text: c.incident, At: t0}}
			}
			env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(func([]string) (shell.Result, error) {
				return shell.Result{Stdout: slackHistory(t0.Add(-mins(c.minutes)), c.text)}, nil
			})}
			var launched []Fired
			check := Alerts{Env: env, Store: s, Channels: []string{"C1"}, Log: slog.Default(), Launch: func(_ context.Context, fired Fired) (string, error) {
				launched = append(launched, fired)
				return "/state/lanes/dd-338724738-fix.brief.md", nil
			}}

			if err := check.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}
			if err := check.Check(context.Background(), t0.Add(mins(1))); err != nil {
				t.Fatal(err)
			}

			equal(t, len(launched) == 1, c.launched)
			equal(t, len(bus.posted) == 1, c.launched)
			if c.launched {
				equal(t, []string{launched[0].Slug, launched[0].What, launched[0].Link}, []string{"dd-338724738", "Triggered: runtime-api-next logging errors — plat", "https://slack/p1"})
				posted := bus.posted[0]
				equal(t, []any{posted.Kind, posted.To, posted.LaneRefs, posted.Attrs["brief"]}, []any{kindSpawn, []string{Root}, []string{"dd-338724738-fix"}, "/state/lanes/dd-338724738-fix.brief.md"})
				contains(t, posted.Text, "ALERT-LAUNCHED Triggered: runtime-api-next logging errors — plat: no incident record named 338724738 4 min after it fired")
			}
		})
	}
}

func noMonitorLane(context.Context, string, string) (string, error) { return "", nil }

func TestAnAlertWhoseMonitorALiveLaneHoldsLaunchesNothing(t *testing.T) {
	s := memory(t)
	bus := &fakeBus{}
	var asked []string
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: slackHistory(t0.Add(-mins(4)), "Triggered: x\n"+monitorLink)}, nil
	}), MonitorLane: func(_ context.Context, monitor, lane string) (string, error) {
		asked = append(asked, monitor, lane)
		return "journal-open-fix", nil
	}}
	check := Alerts{Env: env, Store: s, Channels: []string{"C1"}, Log: slog.Default(), Launch: func(context.Context, Fired) (string, error) {
		t.Fatal("launched")
		return "", nil
	}}

	for range 2 {
		if err := check.Check(context.Background(), t0); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, strings.Join(asked, " "), "338724738 dd-338724738-fix")
	equal(t, len(bus.posted), 0)
}

func TestAnAlertWhoseMonitorCheckFailsRaisesABlockerAndLaunchesNothing(t *testing.T) {
	s := memory(t)
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: slackHistory(t0.Add(-mins(4)), "Triggered: x\n"+monitorLink)}, nil
	}), MonitorLane: func(context.Context, string, string) (string, error) {
		return "", errors.New("orca unreachable")
	}}
	check := Alerts{Env: env, Store: s, Channels: []string{"C1"}, Log: slog.Default(), Launch: func(context.Context, Fired) (string, error) {
		t.Fatal("launched")
		return "", nil
	}}

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 1)
	equal(t, strings.Contains(bus.posted[0].Text, "ALERT-UNOPENED"), true)
	equal(t, strings.Contains(bus.posted[0].Text, "orca unreachable"), true)
}

func TestAFailedAlertLaunchRaisesOneBlockerToRoot(t *testing.T) {
	s := memory(t)
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: slackHistory(t0.Add(-mins(4)), "Triggered: x\n"+monitorLink)}, nil
	})}
	check := Alerts{Env: env, Store: s, Channels: []string{"C1"}, Log: slog.Default(), Launch: func(context.Context, Fired) (string, error) {
		return "", errors.New(strings.Repeat("--flag ", 40) + "desk-launch exited 2")
	}}

	for range 2 {
		if err := check.Check(context.Background(), t0); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, len(bus.posted), 1)
	equal(t, bus.posted[0].Kind, blockerKind)
	contains(t, bus.posted[0].Text, "ALERT-UNOPENED Triggered: x: no incident record names 338724738 4 min after it fired, and the supervisor's launch of dd-338724738-fix failed: ")
	contains(t, bus.posted[0].Text, "desk-launch exited 2")
}

func TestAnAlertWithAFixLaneNamingItJoinsThatLaneAndLaunchesNothing(t *testing.T) {
	s := memory(t)
	if _, err := s.WantLane(storeLane("dd-338724738-fix"), t0); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: slackHistory(t0.Add(-mins(4)), "Triggered: x\n"+monitorLink)}, nil
	})}
	check := Alerts{Env: env, Store: s, Channels: []string{"C1"}, Log: slog.Default(), Launch: func(context.Context, Fired) (string, error) {
		t.Fatal("launched")
		return "", nil
	}}

	for range 2 {
		if err := check.Check(context.Background(), t0); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, len(bus.posted), 1)
	equal(t, bus.posted[0].To, []string{"dd-338724738-fix"})
	contains(t, bus.posted[0].Text, "ALERT-AGAIN Triggered: x: 338724738 fired again 4 min ago")
}

func storeLane(name string) store.Lane {
	return store.Lane{Name: name, Brief: "/briefs/" + name + ".md", Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired}
}

func TestAnIncidentBriefFillsEveryPlaceholderAndFallsBackToTheDrivesCopy(t *testing.T) {
	claude, state := t.TempDir(), t.TempDir()
	env := Env{Config: config.Config{Drive: testDrive, Checkout: "/checkout"}, Claude: claude, State: state, Now: func() time.Time { return t0 }}
	template := filepath.Join(env.plugin(), "skills", "long-running", "reference", "alert-fix-brief.md")
	if err := os.MkdirAll(filepath.Dir(template), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(template, []byte("{lane} {incident} {slug} {link} {what} {onset} {reported} {due} {repo} {drive} {facts} {{literal}}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, "alert-evidence-brief.md"), []byte("evidence {lane}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fired := Fired{"dd-1", "it broke", "https://slack/p1", t0.Add(-mins(4)), nil}

	fix, err := env.incidentBrief("dd-1-fix", "alert-fix-brief.md", fired)
	if err != nil {
		t.Fatal(err)
	}
	evidence, err := env.incidentBrief("dd-1-evidence", "alert-evidence-brief.md", fired)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := env.incidentBrief("dd-1-x", "no-such-brief.md", fired); !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("err %v", err)
	}

	text, err := os.ReadFile(fix)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, string(text), "dd-1-fix dd-1 dd-1 https://slack/p1 it broke 9:56 AM 10:00 10:20 /checkout d1 drive d1 {literal}\n")
	said, err := os.ReadFile(evidence)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, string(said), "evidence dd-1-evidence\n")
}

func TestAnAlertChannelThatDoesNotReadPostsNothingAndTheTickGoesOn(t *testing.T) {
	bus := &fakeBus{}
	env := Env{Bus: bus, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Code: 1}, &shell.ExitError{Argv: []string{"cc-slack"}, Code: 1}
	})}
	check := Alerts{Env: env, Channels: []string{"C1"}, Log: slog.Default()}

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 0)
}

func TestAnOrcaFixLaneIdleThirtyMinutesPastFixLiveIsReclaimedWithOneReportToRoot(t *testing.T) {
	cases := map[string]struct {
		fixed, quiet int
		reclaimed    bool
	}{
		"idle":      {31, 31, true},
		"lane-busy": {31, 5, false},
		"too-soon":  {12, 31, false},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			local := t.TempDir()
			fix := "rapi-next-errors-fix"
			fixLive := client.Record{Seq: 341700, Lane: Root, Kind: "decide", At: t0.Add(-mins(c.fixed)), To: []string{fix, "alerts-desk"}, Text: "FIX-LIVE rapi-next-errors"}
			newest := client.Record{Seq: 341710, Lane: fix, Kind: "state", At: t0.Add(-mins(c.quiet)), Text: "done"}
			bus := &fakeBus{tail: []client.Record{fixLive, newest}}
			var gcs [][]string
			env := Env{Bus: bus, Config: config.Config{Drive: testDrive, OrcaRun: "run_1"}, Local: local, Claude: t.TempDir(), Runner: scripted(func(argv []string) (shell.Result, error) {
				gcs = append(gcs, argv)
				return shell.Result{Stdout: "stopped ctx_fix1\nclosed=1 removed=1\n"}, nil
			})}
			writeJSON(t, filepath.Join(env.receipts(), fix+".json"), map[string]any{"result": map[string]string{"dispatchId": "ctx_fix1"}})
			gc := &FixLiveGC{Env: env, Log: slog.Default()}

			if err := gc.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}
			if err := gc.Check(context.Background(), t0.Add(mins(10))); err != nil {
				t.Fatal(err)
			}

			equal(t, len(gcs) == 1, c.reclaimed)
			equal(t, len(bus.posted) == 1, c.reclaimed)
			if c.reclaimed {
				equal(t, gcs[0][len(gcs[0])-4:], []string{"--run", "run_1", "--done", "ctx_fix1"})
				if !strings.HasPrefix(bus.posted[0].Text, "GC rapi-next-errors-fix: FIX-LIVE #341700 is 30 minutes old") {
					t.Fatal(bus.posted[0].Text)
				}
			}
		})
	}
}

func TestOnlyARecordAnnouncingFixLiveCounts(t *testing.T) {
	cases := map[string]struct {
		record client.Record
		want   bool
	}{
		"kind":            {client.Record{Kind: "fix-live", Text: "retro/000003 read by retro.py"}, true},
		"opening line":    {client.Record{Kind: "decide", Text: "FIX-LIVE rapi-next-errors"}, true},
		"stamped line":    {client.Record{Kind: "decide", Text: "9:14 AM FIX-LIVE rapi-next-errors"}, true},
		"handoff denying": {client.Record{Kind: "ask", Text: "HANDOFF ASK: repair incomplete and no READY/FIX-LIVE"}, false},
		"quoted rule":     {client.Record{Kind: "blocker", Text: "post refused: a resolution naming no FIX-LIVE"}, false},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			equal(t, announcesFixLive(c.record), c.want)
		})
	}
}

type commanded func(c shell.Command) (shell.Result, error)

func (f commanded) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	return f(c)
}

var releaseComponents = map[string]string{"api/": "api", "common/": "api", "go/sand/": "sand", "go/llm-gateway/": "llm-gateway", "tools/remote-builder/": "sanddb", "rust/crates/": "sand-next"}

func notLive(t *testing.T, ledger string, files map[string]string, live string, ancestor bool) (*LandedNotLive, *fakeBus) {
	t.Helper()
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive, Checkout: "/checkout"}, Claude: t.TempDir(), Runner: commanded(func(c shell.Command) (shell.Result, error) {
		argv := c.Argv
		switch {
		case strings.HasSuffix(argv[0], "ledger.py"):
			return shell.Result{Stdout: ledger}, nil
		case argv[0] == "git" && argv[3] == "diff-tree":
			return shell.Result{Stdout: files[argv[len(argv)-1]]}, nil
		case argv[0] == "git":
			if ancestor {
				return shell.Result{}, nil
			}
			return shell.Result{Code: 1}, &shell.ExitError{Argv: argv, Code: 1}
		case argv[0] == tokenTool:
			return shell.Result{Stdout: "read-token\n"}, nil
		case slices.Equal(argv[len(argv)-2:], []string{"status", "--json"}):
			return shell.Result{Stdout: "[]"}, nil
		case slices.Equal(argv[len(argv)-2:], []string{"select", "--pr"}):
			var request struct {
				Paths []string `json:"paths"`
			}
			if err := json.Unmarshal([]byte(c.Stdin), &request); err != nil {
				t.Fatal(err)
			}
			touched := []string{}
			for prefix, component := range releaseComponents {
				if !slices.Contains(touched, component) && slices.ContainsFunc(request.Paths, func(path string) bool { return strings.HasPrefix(path, prefix) }) {
					touched = append(touched, component)
				}
			}
			answer, err := json.Marshal(map[string][]string{"touched": touched})
			return shell.Result{Stdout: string(answer)}, err
		default:
			return shell.Result{Stdout: live}, nil
		}
	})}
	s, err := store.Open(filepath.Join(t.TempDir(), StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return &LandedNotLive{Env: env, Store: s, ReleaseCLI: []string{"tools/ci", "release"}, Token: bkread.Token{Command: []string{tokenTool}}, Log: slog.Default()}, bus
}

func TestARestartedCheckKeepsTheTargetsItAlreadyResolved(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"Sand: fix it","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	files := map[string]string{"abc": "go/sand/internal/vm/vm.go\n"}
	first, _ := notLive(t, ledger, files, `[{"target":"sand","commit":"c1"}]`, false)
	if err := first.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	restarted, _ := notLive(t, ledger, files, `[{"target":"sand","commit":"c1"}]`, false)
	restarted.Store = first.Store
	answers := restarted.Env.Runner
	restarted.Env.Runner = commanded(func(c shell.Command) (shell.Result, error) {
		if slices.Contains(c.Argv, "diff-tree") || slices.Contains(c.Argv, "select") {
			t.Fatalf("a restart resolved a landing again: %v", c.Argv)
		}
		return answers.Run(context.Background(), c)
	})
	landed, err := restarted.landings(context.Background(), t0)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, landed[47799].targets, []string{"sand"})

	later := t0.Add(notLiveSpan + time.Hour)
	if _, err := restarted.landings(context.Background(), later); err != nil {
		t.Fatal(err)
	}
	kept, err := restarted.Store.ReleaseTargets()
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(kept), 0)
}

func TestALandingATargetDoesNotRunAfterAnHourDrawsOneBlockerNamingItsRelease(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"Sand: fix it","landed_sha":"abc","landed_at":%q},{"pr":"47800","state":"landed","title":"sand: too new","landed_sha":"def","landed_at":%q},{"pr":"47801","state":"open","title":"sand: open"}]`, t0.Add(-2*time.Hour).Format(time.RFC3339), t0.Add(-mins(10)).Format(time.RFC3339))
	check, bus := notLive(t, ledger, map[string]string{"abc": "go/sand/internal/vm/vm.go\n"}, `[{"target":"sand","commit":"c1"}]`, false)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	if err := check.Check(context.Background(), t0.Add(notLiveEvery)); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 2)
	state, blocker := bus.posted[0], bus.posted[1]
	equal(t, []string{state.Kind, state.Topic, state.Text}, []string{"state", notLiveTopic, "LANDED-NOT-LIVE sand: #47799"})
	equal(t, blocker.Kind, blockerKind)
	equal(t, blocker.Text, "LANDED-NOT-LIVE sand: #47799. The oldest landed 120 minutes ago, and no live release, running release build or release that found its target unchanged carries them; each ships with the next release that picks its target, and the owner starts that release")
}

func TestARootHoldOnATargetKeepsItsLandingQuietUntilTheHoldEnds(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"sand: fix it","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, map[string]string{"abc": "go/sand/internal/vm/vm.go\n"}, `[{"target":"sand","commit":"c1"}]`, false)
	var hold client.Record
	body := fmt.Sprintf(`{"seq":9,"lane":"root","kind":"hold","status":"open","at":%q,"text":"routine cuts wait for the morning","refs":{"targets":["sand"]},"fields":{"attrs":{"until":%q}}}`, t0.Add(-time.Hour).Format(time.RFC3339), t0.Add(notLiveEvery).Format(time.RFC3339))
	if err := json.Unmarshal([]byte(body), &hold); err != nil {
		t.Fatal(err)
	}
	bus.open = []client.Record{hold}

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	equal(t, len(bus.posted), 0)

	if err := check.Check(context.Background(), t0.Add(notLiveEvery)); err != nil {
		t.Fatal(err)
	}
	equal(t, len(bus.posted), 2)
	equal(t, bus.posted[0].Text, "LANDED-NOT-LIVE sand: #47799")
	equal(t, bus.posted[1].Kind, blockerKind)
}

func TestALandingItsTargetRunsOwesNoRelease(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"sand: fix it","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, map[string]string{"abc": "go/sand/internal/vm/vm.go\n"}, `[{"target":"sand","commit":"c1"}]`, true)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 0)
}

func TestALandingWhoseFilesReachNoReleaseTargetOwesNoRelease(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"sand: words","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, map[string]string{"abc": "docs/slack.md\n"}, `[{"target":"sand","commit":"c1"}]`, false)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 0)
}

func TestALandingOwesTheTargetsItsFilesReachNeverItsTitleScope(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"48377","state":"landed","title":"llm-gateway: route the judge through the gateway","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, map[string]string{"abc": "api/src/modules/llmGateway.ts\ncommon/src/core/llm/client.ts\n"}, `[{"target":"api","commit":"c1"},{"target":"llm-gateway","commit":"c2"}]`, false)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 2)
	equal(t, bus.posted[0].Text, "LANDED-NOT-LIVE api: #48377")
	equal(t, bus.posted[1].Text, "LANDED-NOT-LIVE api: #48377. The oldest landed 120 minutes ago, and no live release, running release build or release that found its target unchanged carries them; each ships with the next release that picks its target, and the owner starts that release")
}

func TestAReleaseThatShipsALandingResolvesItsBlocker(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"sand: fix it","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, map[string]string{"abc": "go/sand/internal/vm/vm.go\n"}, `[{"target":"sand","commit":"c1"}]`, false)
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	check.holds = map[[2]string]bool{{"c1", "abc"}: true}

	if err := check.Check(context.Background(), t0.Add(notLiveEvery)); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 4)
	equal(t, bus.posted[2].Text, "LANDED-NOT-LIVE none")
	equal(t, bus.posted[3].Resolves, int64(2))
}

func releasingFeed(state string) string {
	return fmt.Sprintf(`[{"number":12383,"state":%q,"commit":"r1","jobs":[{"step_key":"bootstrap","state":"passed"},{"step_key":"apply-sand--plat-usw2-prod","state":"running"}]}]`, state)
}

func carriedByBuild(t *testing.T, feed *string) (*LandedNotLive, *fakeBus) {
	t.Helper()
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"sand: fix it","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, map[string]string{"abc": "go/sand/internal/vm/vm.go\n"}, `[{"target":"sand","commit":"c1"}]`, false)
	check.holds = map[[2]string]bool{{"c1", "abc"}: false, {"r1", "abc"}: true}
	check.Builds, _ = releaseBuildsIn(t, memory(t), feed)
	return check, bus
}

func TestALandingARunningReleaseBuildCarriesPostsNothing(t *testing.T) {
	feed := releasingFeed("running")
	check, bus := carriedByBuild(t, &feed)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 0)
}

func TestAFailedReleaseBuildPutsItsLandingBackInTheCount(t *testing.T) {
	feed := releasingFeed("running")
	check, bus := carriedByBuild(t, &feed)
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	feed = releasingFeed("failed")

	if err := check.Check(context.Background(), t0.Add(notLiveEvery)); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 2)
	equal(t, bus.posted[1].Kind, blockerKind)
	contains(t, bus.posted[1].Text, "LANDED-NOT-LIVE sand: #47799. ")
}

func TestANewLandingOnATargetAddsOneRecordNamingEveryTarget(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"sand: fix it","landed_sha":"abc","landed_at":%q},{"pr":"48377","state":"landed","title":"api: one","landed_sha":"def","landed_at":%q},{"pr":"48400","state":"landed","title":"api: two","landed_sha":"ghi","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339), t0.Add(-2*time.Hour).Format(time.RFC3339), t0.Add(notLiveEvery-notLiveOwed).Format(time.RFC3339))
	check, bus := notLive(t, ledger, map[string]string{"abc": "go/sand/internal/vm/vm.go\n", "def": "api/src/one.ts\n", "ghi": "api/src/two.ts\n"}, `[{"target":"sand","commit":"c1"},{"target":"api","commit":"c2"}]`, false)
	blockers := func() []client.Draft {
		return slices.DeleteFunc(slices.Clone(bus.posted), func(d client.Draft) bool { return d.Kind != blockerKind })
	}

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	equal(t, len(blockers()), 1)
	first := blockers()[0]
	equal(t, first.PRs, []string{"48377", "47799"})
	contains(t, first.Text, "LANDED-NOT-LIVE api: #48377; sand: #47799. ")

	if err := check.Check(context.Background(), t0.Add(notLiveEvery)); err != nil {
		t.Fatal(err)
	}
	equal(t, len(blockers()), 2)
	second := blockers()[1]
	equal(t, second.PRs, []string{"48377", "48400", "47799"})
	equal(t, second.Resolves, int64(2))
	contains(t, second.Text, "LANDED-NOT-LIVE api: #48377, #48400; sand: #47799. ")

	posted := len(bus.posted)
	if err := check.Check(context.Background(), t0.Add(2*notLiveEvery)); err != nil {
		t.Fatal(err)
	}
	equal(t, len(bus.posted), posted)
}

func releasedUnchanged(t *testing.T, releases string) (*LandedNotLive, *fakeBus) {
	t.Helper()
	landed := t0.Add(-2 * time.Hour).Format(time.RFC3339)
	ledger := fmt.Sprintf(`[{"pr":"49101","state":"landed","title":"remote-builder: start sccache first","landed_sha":"abc","landed_at":%q},{"pr":"48324","state":"landed","title":"sandsql-replication: one replicator task","landed_sha":"def","landed_at":%q}]`, landed, landed)
	files := map[string]string{"abc": "tools/remote-builder/README.md\n", "def": "rust/crates/sandsql-replica/src/subscribe.rs\n"}
	check, bus := notLive(t, ledger, files, `[{"target":"sanddb","commit":"s0"},{"target":"sand-next","commit":"n0"}]`, false)
	check.holds = map[[2]string]bool{{"s0", "abc"}: false, {"n0", "def"}: false, {"39ba620", "abc"}: true, {"39ba620", "def"}: true}
	answers := check.Env.Runner
	check.Env.Runner = commanded(func(c shell.Command) (shell.Result, error) {
		if slices.Contains(c.Argv, "status") {
			return shell.Result{Stdout: releases}, nil
		}
		return answers.Run(context.Background(), c)
	})
	return check, bus
}

func TestALandingAReleaseFoundUnchangedOnItsTargetOwesNothingThere(t *testing.T) {
	check, bus := releasedUnchanged(t, `[{"id":"12597","mode":"release","status":"cancelled","commit":"39ba620","unchanged":[{"component":"sanddb","env":"plat-usw2-prod"}]}]`)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 2)
	equal(t, bus.posted[0].Text, "LANDED-NOT-LIVE sand-next: #48324")
	equal(t, bus.posted[1].PRs, []string{"48324"})
	if strings.Contains(bus.posted[1].Text, "tools/ci") {
		t.Fatalf("the blocker printed a release command: %s", bus.posted[1].Text)
	}
}

func TestADryRunThatFoundATargetUnchangedLeavesItsLandingsOwed(t *testing.T) {
	check, bus := releasedUnchanged(t, `[{"id":"12590","mode":"dry-run","status":"converged","commit":"39ba620","unchanged":[{"component":"sanddb","env":"plat-usw2-prod"},{"component":"sand-next","env":"plat-usw2-prod"}]}]`)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 2)
	equal(t, bus.posted[0].Text, "LANDED-NOT-LIVE sand-next: #48324; sanddb: #49101")
}

type spriteRun struct {
	mu       sync.Mutex
	version  string
	statuses map[string]string
	fail     map[string]string
	stopped  []string
	sprites  []string
	listFail string
}

func (r *spriteRun) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if c.Argv[0] == "orca" {
		workers := make([]map[string]string, 0, len(r.statuses))
		for dispatch, status := range r.statuses {
			workers = append(workers, map[string]string{"dispatchId": dispatch, "dispatchStatus": status})
		}
		page, err := json.Marshal(map[string]any{"ok": true, "result": map[string]any{"workers": workers, "page": map[string]string{}}})
		return shell.Result{Stdout: string(page)}, err
	}
	if c.Argv[0] == "sprite" {
		if r.listFail != "" {
			return shell.Result{Code: 1, Stderr: r.listFail}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: r.listFail}
		}
		return shell.Result{Stdout: strings.Join(r.sprites, "\n") + "\n"}, nil
	}
	if c.Argv[1] == "version" {
		return shell.Result{Stderr: r.version + "\n"}, nil
	}
	lane := c.Argv[3]
	r.stopped = append(r.stopped, strings.Join(c.Argv, " "))
	if why, failing := r.fail[lane]; failing {
		return shell.Result{Code: 1, Stderr: why}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: why}
	}
	return shell.Result{Stdout: `{"lane":"` + lane + `"}`}, nil
}

func spriteStops(t *testing.T, run *spriteRun) (*SpriteStops, *fakeBus) {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive, OrcaRun: "run_1"}, Local: t.TempDir(), Claude: t.TempDir(), Runner: run}
	return &SpriteStops{Env: env, Store: s, CcRemote: CcRemote{Binary: "cc-remote", Config: "/drive/cc-remote.yaml"}, Log: slog.Default()}, bus
}

func (c *SpriteStops) lane(t *testing.T, name, placement string, state store.LaneState, by, dispatch string) {
	t.Helper()
	if _, err := c.Store.WantLane(store.Lane{Name: name, Brief: "/briefs/" + name + ".md", Model: "opus", Effort: "xhigh", State: store.LaneDesired, Placement: placement}, t0.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	if err := c.Store.SetLaneState(name, state, t0.Add(-mins(30)), by, ""); err != nil {
		t.Fatal(err)
	}
	if dispatch != "" {
		writeJSON(t, filepath.Join(c.Env.receipts(), name+".json"), map[string]any{"result": map[string]string{"dispatchId": dispatch}})
	}
}

func stops(lanes ...string) []string {
	argv := make([]string, 0, len(lanes))
	for _, lane := range lanes {
		argv = append(argv, "cc-remote orca stop "+lane+" --config /drive/cc-remote.yaml")
	}
	return argv
}

func TestAnEndedLanesSpriteIsStoppedOnceAndALiveOrUnsettledLanesIsNot(t *testing.T) {
	run := &spriteRun{version: "0.27.4", statuses: map[string]string{"ctx_done": "completed", "ctx_stood": "failed", "ctx_running": "dispatched", "ctx_live": "completed", "ctx_local": "completed"}}
	c, bus := spriteStops(t, run)
	c.lane(t, "done-settled", "sprite", store.LaneRetired, "done", "ctx_done")
	c.lane(t, "stood-failed", "sprite", store.LaneRetired, "stand-down", "ctx_stood")
	c.lane(t, "done-unlisted", "sprite", store.LaneRetired, "done", "ctx_gone")
	c.lane(t, "withdrawn", "sprite", store.LaneRetired, "withdrawn", "")
	c.lane(t, "done-running", "sprite", store.LaneRetired, "done", "ctx_running")
	c.lane(t, "live", "sprite", store.LaneDesired, "", "ctx_live")
	c.lane(t, "held", "sprite", store.LaneHeld, "", "ctx_live")
	c.lane(t, "relaunched", "sprite", store.LaneRetired, "relaunched-2", "")
	c.lane(t, "local", "orca", store.LaneRetired, "done", "ctx_local")

	if err := c.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	slices.Sort(run.stopped)
	equal(t, run.stopped, stops("done-settled", "done-unlisted", "stood-failed", "withdrawn"))

	run.statuses["ctx_running"] = "completed"
	if err := c.Check(context.Background(), t0.Add(mins(10))); err != nil {
		t.Fatal(err)
	}
	equal(t, run.stopped[4:], stops("done-running"))
	equal(t, len(bus.posted), 0)
}

func TestASpriteStopThatKeepsFailingPostsOneBlockerAfterItsLastTry(t *testing.T) {
	why := "cc-remote: suspending sprite stuck: its services stopped, but it still runs 2m0s later, so a session, request, or connection holds it"
	run := &spriteRun{version: "0.27.4", statuses: map[string]string{"ctx_stuck": "completed"}, fail: map[string]string{"stuck": why}, sprites: []string{"stuck"}}
	c, bus := spriteStops(t, run)
	c.lane(t, "stuck", "sprite", store.LaneRetired, "done", "ctx_stuck")

	for _, at := range []time.Time{t0, t0.Add(mins(10)), t0.Add(mins(20))} {
		if err := c.Check(context.Background(), at); err != nil {
			t.Fatal(err)
		}
	}
	equal(t, run.stopped, stops("stuck", "stuck", "stuck"))
	equal(t, len(bus.posted), 1)

	if err := c.Check(context.Background(), t0.Add(mins(30))); err != nil {
		t.Fatal(err)
	}
	equal(t, run.stopped, stops("stuck", "stuck", "stuck"))
	equal(t, len(bus.posted), 1)
	posted := bus.posted[0]
	equal(t, []any{string(posted.Kind), posted.To, posted.ID}, []any{blockerKind, []string{Root}, "sprite-stop:stuck"})
	contains(t, posted.Text, "SPRITE-STOP-FAILED stuck: its dispatch ctx_stuck is completed")
	contains(t, posted.Text, why)
	contains(t, posted.Text, "`sprite list` shows its Sprite stuck still there")
}

func (c *SpriteStops) claimed(t *testing.T, lane, sprite, dispatch string) {
	t.Helper()
	c.lane(t, lane, "sprite", store.LaneRetired, "done", "")
	writeJSON(t, filepath.Join(c.Env.receipts(), lane+".json"), map[string]any{"result": map[string]any{"dispatchId": dispatch, "server": map[string]string{"name": sprite}}})
}

func TestAFailedStopOfASpriteSpriteListNoLongerShowsClosesWithNoBlockerAndNoRetry(t *testing.T) {
	why := "cc-remote: no Orca task is recorded for pool-gone"
	run := &spriteRun{version: "0.27.4", statuses: map[string]string{"ctx_gone": "completed"}, fail: map[string]string{"gone": why}, sprites: []string{"gone", "pool-other"}}
	c, bus := spriteStops(t, run)
	c.claimed(t, "gone", "pool-gone", "ctx_gone")

	for _, at := range []time.Time{t0, t0.Add(mins(10)), t0.Add(mins(20)), t0.Add(mins(30))} {
		if err := c.Check(context.Background(), at); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, run.stopped, stops("gone"))
	equal(t, len(bus.posted), 0)
}

func TestAFailedStopFindsAClaimedSpriteByItsLaunchReceiptNotItsLaneName(t *testing.T) {
	why := "cc-remote: suspending sprite stuck: a connection holds it"
	run := &spriteRun{version: "0.27.4", statuses: map[string]string{"ctx_stuck": "completed"}, fail: map[string]string{"stuck": why}, sprites: []string{"pool-stuck"}}
	c, bus := spriteStops(t, run)
	c.claimed(t, "stuck", "pool-stuck", "ctx_stuck")

	for _, at := range []time.Time{t0, t0.Add(mins(10)), t0.Add(mins(20))} {
		if err := c.Check(context.Background(), at); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, run.stopped, stops("stuck", "stuck", "stuck"))
	equal(t, len(bus.posted), 1)
	contains(t, bus.posted[0].Text, "`sprite list` shows its Sprite pool-stuck still there")
}

func TestAFailedStopWhenSpriteListAlsoFailsNeverClaimsTheSpriteIsGoneOrRunning(t *testing.T) {
	why := "cc-remote: sprites api: 500"
	run := &spriteRun{version: "0.27.4", statuses: map[string]string{"ctx_down": "completed"}, fail: map[string]string{"down": why}, listFail: "sprite: not logged in"}
	c, bus := spriteStops(t, run)
	c.claimed(t, "down", "pool-down", "ctx_down")

	for _, at := range []time.Time{t0, t0.Add(mins(10)), t0.Add(mins(20))} {
		if err := c.Check(context.Background(), at); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, run.stopped, stops("down", "down", "down"))
	equal(t, len(bus.posted), 1)
	contains(t, bus.posted[0].Text, "`sprite list` failed too, so its Sprite pool-down may still run")
	contains(t, bus.posted[0].Text, "sprite: not logged in")
}

func TestASpriteStopThatFailsOnceIsTriedAgainOnTheNextPassAndPostsNothing(t *testing.T) {
	run := &spriteRun{version: "0.27.4", statuses: map[string]string{"ctx_flaky": "completed"}, fail: map[string]string{"flaky": "curl: (35) Recv failure: Operation timed out"}, sprites: []string{"flaky"}}
	c, bus := spriteStops(t, run)
	c.lane(t, "flaky", "sprite", store.LaneRetired, "done", "ctx_flaky")

	if err := c.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	run.fail = nil
	for _, at := range []time.Time{t0.Add(mins(10)), t0.Add(mins(20))} {
		if err := c.Check(context.Background(), at); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, run.stopped, stops("flaky", "flaky"))
	equal(t, len(bus.posted), 0)
}

func TestACcRemoteOlderThanOrcaStopStopsNothingAndPostsNothingUntilItsPinReachesIt(t *testing.T) {
	run := &spriteRun{version: "0.26.19", statuses: map[string]string{"ctx_done": "completed"}, fail: map[string]string{"done": "cc-remote: unknown flag: --config"}}
	c, bus := spriteStops(t, run)
	c.lane(t, "done", "sprite", store.LaneRetired, "done", "ctx_done")

	if err := c.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	equal(t, run.stopped, []string(nil))
	equal(t, len(bus.posted), 0)

	run.version, run.fail = "0.27.4", nil
	if err := c.Check(context.Background(), t0.Add(mins(10))); err != nil {
		t.Fatal(err)
	}
	equal(t, run.stopped, stops("done"))
	equal(t, len(bus.posted), 0)
}

func TestAnAlertLaunchStartsNoCommsLane(t *testing.T) {
	plugin, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	claude := t.TempDir()
	if err := os.MkdirAll(filepath.Join(claude, "long-running"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(plugin, filepath.Join(claude, "long-running", "current")); err != nil {
		t.Fatal(err)
	}
	var argv []string
	env := Env{Config: config.Config{Drive: testDrive, Checkout: "/checkout"}, Claude: claude, State: t.TempDir(), Now: func() time.Time { return t0 }, Runner: scripted(func(args []string) (shell.Result, error) {
		argv = args
		return shell.Result{}, nil
	})}

	if _, err := env.IncidentLaunch(context.Background(), Fired{"dd-1", "it broke", "https://slack/p1", t0.Add(-mins(4)), nil}); err != nil {
		t.Fatal(err)
	}

	equal(t, slices.Contains(argv, "--comms-brief"), false)
}

func TestTwoFiringsOfOneMonitorInAPassLaunchOnce(t *testing.T) {
	triggered := "Triggered: runtime-api-next logging errors — plat\n" + monitorLink
	bus := &fakeBus{}
	history := fmt.Sprintf(`{"messages":[{"ts":"%d.000001","text":%q,"permalink":"https://slack/p2"},{"ts":"%d.000001","text":%q,"permalink":"https://slack/p1"}]}`, t0.Add(-mins(2)).Unix(), triggered, t0.Add(-mins(30)).Unix(), triggered)
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: history}, nil
	})}
	launches := 0
	check := Alerts{Env: env, Store: memory(t), Channels: []string{"C1"}, Log: slog.Default(), Launch: func(context.Context, Fired) (string, error) {
		launches++
		return "/state/lanes/dd-338724738-fix.brief.md", nil
	}}

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, launches, 1)
	equal(t, len(bus.posted), 1)
}

func TestAPassReadsOpenIncidentsOnceAndARestartDoesNotCutALaunch(t *testing.T) {
	first, second := "Triggered: a\n"+monitorLink, "Triggered: b\nhttps://app.datadoghq.com/monitors/111"
	fired := t0.Add(-mins(2)).Unix()
	history := fmt.Sprintf(`{"messages":[{"ts":"%d.000002","text":%q,"permalink":"https://slack/p2"},{"ts":"%d.000001","text":%q,"permalink":"https://slack/p1"}]}`, fired, second, fired, first)
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: history}, nil
	})}
	ctx, cancel := context.WithCancel(context.Background())
	launches := 0
	check := Alerts{Env: env, Store: memory(t), Channels: []string{"C1"}, Log: slog.Default(), Launch: func(launch context.Context, _ Fired) (string, error) {
		launches++
		cancel()
		return "/state/lanes/fix.brief.md", launch.Err()
	}}

	if err := check.Check(ctx, t0); err != nil {
		t.Fatal(err)
	}

	equal(t, launches, 2)
	equal(t, bus.tails, 1)
	equal(t, len(bus.posted), 2)
}
