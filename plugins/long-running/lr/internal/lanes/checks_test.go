package lanes

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
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
		"in-grace":     {triggered, 1, "", false},
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
				bus.tail = []client.Record{{Seq: 7, Text: c.incident}}
			}
			env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, Runner: scripted(func([]string) (shell.Result, error) {
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
				equal(t, []any{posted.Kind, posted.To, posted.LaneRefs, posted.Attrs["brief"]}, []any{kindSpawn, []string{Root}, []string{"dd-338724738-fix", "dd-338724738-evidence"}, "/state/lanes/dd-338724738-fix.brief.md"})
				contains(t, posted.Text, "ALERT-LAUNCHED Triggered: runtime-api-next logging errors — plat: no incident record named 338724738 4 min after it fired")
			}
		})
	}
}

func TestAFailedAlertLaunchRaisesOneBlockerToRoot(t *testing.T) {
	s := memory(t)
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: slackHistory(t0.Add(-mins(4)), "Triggered: x\n"+monitorLink)}, nil
	})}
	check := Alerts{Env: env, Store: s, Channels: []string{"C1"}, Log: slog.Default(), Launch: func(context.Context, Fired) (string, error) { return "", errors.New("desk-launch exited 2") }}

	for range 2 {
		if err := check.Check(context.Background(), t0); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, len(bus.posted), 1)
	equal(t, bus.posted[0].Kind, blockerKind)
	contains(t, bus.posted[0].Text, "ALERT-UNOPENED Triggered: x: no incident record names 338724738 4 min after it fired, and the supervisor's launch of dd-338724738-fix failed: desk-launch exited 2")
}

func TestAnAlertWithAFixLaneNamingItIsNotLaunchedAgain(t *testing.T) {
	s := memory(t)
	if _, err := s.WantLane(storeLane("dd-338724738-fix"), t0); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: slackHistory(t0.Add(-mins(4)), "Triggered: x\n"+monitorLink)}, nil
	})}
	check := Alerts{Env: env, Store: s, Channels: []string{"C1"}, Log: slog.Default(), Launch: func(context.Context, Fired) (string, error) {
		t.Fatal("launched")
		return "", nil
	}}

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 0)
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
	if err := os.WriteFile(template, []byte("{lane} {incident} {slug} {link} {what} {onset} {reported} {due} {repo} {log} {facts} {{literal}}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, "alert-evidence-brief.md"), []byte("evidence {lane}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fired := Fired{"dd-1", "it broke", "https://slack/p1", t0.Add(-mins(4))}

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
	equal(t, string(text), "dd-1-fix dd-1 dd-1 https://slack/p1 it broke 9:56 AM 10:00 10:20 /checkout incident-dd-1 drive d1 {literal}\n")
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

func notLive(t *testing.T, ledger string, live string, ancestor bool) (*LandedNotLive, *fakeBus) {
	t.Helper()
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive, Checkout: "/checkout"}, Claude: t.TempDir(), Runner: scripted(func(argv []string) (shell.Result, error) {
		switch {
		case strings.HasSuffix(argv[0], "ledger.py"):
			return shell.Result{Stdout: ledger}, nil
		case argv[0] == "git":
			if ancestor {
				return shell.Result{}, nil
			}
			return shell.Result{Code: 1}, &shell.ExitError{Argv: argv, Code: 1}
		default:
			return shell.Result{Stdout: live}, nil
		}
	})}
	return &LandedNotLive{Env: env, ReleaseCLI: []string{"tools/ci", "release"}, Log: slog.Default()}, bus
}

func TestALandingATargetDoesNotRunAfterAnHourDrawsOneBlockerNamingItsRelease(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"Sand: fix it","landed_sha":"abc","landed_at":%q},{"pr":"47800","state":"landed","title":"sand: too new","landed_sha":"def","landed_at":%q},{"pr":"47801","state":"open","title":"sand: open"}]`, t0.Add(-2*time.Hour).Format(time.RFC3339), t0.Add(-mins(10)).Format(time.RFC3339))
	check, bus := notLive(t, ledger, `[{"target":"sand","commit":"c1"}]`, false)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	if err := check.Check(context.Background(), t0.Add(notLiveEvery)); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 2)
	state, blocker := bus.posted[0], bus.posted[1]
	equal(t, []string{state.Kind, state.Topic, state.Text}, []string{"state", notLiveTopic, "LANDED-NOT-LIVE sand: #47799 (8:00 AM)"})
	equal(t, blocker.Kind, blockerKind)
	equal(t, blocker.Text, "LANDED-NOT-LIVE sand: #47799 landed, the oldest 120 minutes ago, and sand runs none of them; start its release: `tools/ci release start --only sand --manual`")
}

func TestALandingItsTargetRunsOwesNoRelease(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"sand: fix it","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, `[{"target":"sand","commit":"c1"}]`, true)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 0)
}

func TestATargetTheReleaseDoesNotListOwesNoRelease(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"docs: words","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, `[{"target":"sand","commit":"c1"}]`, false)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 0)
}

func TestAReleaseThatShipsALandingResolvesItsBlocker(t *testing.T) {
	ledger := fmt.Sprintf(`[{"pr":"47799","state":"landed","title":"sand: fix it","landed_sha":"abc","landed_at":%q}]`, t0.Add(-2*time.Hour).Format(time.RFC3339))
	check, bus := notLive(t, ledger, `[{"target":"sand","commit":"c1"}]`, false)
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
