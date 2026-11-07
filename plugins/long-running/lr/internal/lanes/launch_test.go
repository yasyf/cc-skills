package lanes

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var startSequence = []struct {
	session, lane, model, effort string
	role                         []string
}{
	{"coordinator", "root", "fable", "high", nil},
	{"meta", "meta", "opus", "medium", []string{"--role", "meta"}},
	{"signal", "signal", "sonnet", "low", nil},
	{"chores", "chores", "sonnet", "low", nil},
}

func sessionBrief(t *testing.T, state, lane, role string) string {
	t.Helper()
	path := filepath.Join(state, lane, "brief.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	ccx := "ccx: place=local why=test"
	if role != "" {
		ccx = "ccx: role=" + role + " place=local why=test"
	}
	if err := os.WriteFile(path, []byte(fmt.Sprintf("# %s\nRegister slice: lane=%s step=all\nStanding: the %s duty\n%s\n", lane, lane, lane, ccx)), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func startEnv(bus *fakeBus, ran *[]string) Env {
	return Env{Bus: bus, Config: config.Config{Drive: testDrive}, Claude: "/claude", Now: func() time.Time { return t0 }, Runner: scripted(func(argv []string) (shell.Result, error) {
		*ran = argv
		return shell.Result{Stdout: "launched\n"}, nil
	})}
}

func TestBeforeAnyRunnerDriveStartLaunchesEachSessionFromTheCommunicatorsTerminalBoundToItsOwnLane(t *testing.T) {
	for _, want := range startSequence {
		t.Run(want.session, func(t *testing.T) {
			var ran []string
			bus := &fakeBus{tail: []client.Record{
				{Lane: orcaRunner, Topic: "landing", Text: "landing alive 7:00 PM pass 3"},
				{Lane: "other", Topic: orcaTopic, Text: "orca alive 7:00 PM pass 3"},
			}}
			brief := filepath.Join("/state", want.lane, "brief.md")

			out, err := startEnv(bus, &ran).launchStanding(context.Background(), standingSessions[want.session], brief)
			if err != nil {
				t.Fatal(err)
			}

			equal(t, ran, []string{"/claude/long-running/current/bin/orca-launch.sh", want.lane, want.model, want.effort, brief})
			equal(t, out, "launched\n")
			equal(t, bus.query.Since, t0.Add(-runnerStale))
		})
	}
}

func TestWithARunnerUpDriveStartPostsEachSessionsLaunchToItAsTheCommunicator(t *testing.T) {
	for _, want := range startSequence {
		t.Run(want.session, func(t *testing.T) {
			var ran []string
			bus := &fakeBus{tail: []client.Record{{Lane: orcaRunner, Topic: orcaTopic, Text: "orca alive 7:00 PM pass 3"}}}
			brief := filepath.Join("/state", want.lane, "brief.md")

			if _, err := startEnv(bus, &ran).launchStanding(context.Background(), standingSessions[want.session], brief); err != nil {
				t.Fatal(err)
			}

			equal(t, ran, append([]string{"/claude/long-running/current/bin/lr", "launch", "lane", want.lane, want.model, want.effort, brief, "--drive", testDrive, "--lane", "owner-relay"}, want.role...))
		})
	}
}

func TestOnlyTheCoordinatorLaunchesOnFable(t *testing.T) {
	for _, name := range []string{"coordinator", "meta", "chores"} {
		t.Run(name, func(t *testing.T) {
			var ran []string
			bus := &fakeBus{tail: []client.Record{{Lane: orcaRunner, Topic: orcaTopic, Text: "orca alive 7:00 PM pass 3"}}}
			session := standingSessions[name]

			if _, err := startEnv(bus, &ran).launchStanding(context.Background(), session, filepath.Join("/state", session.lane, "brief.md")); err != nil {
				t.Fatal(err)
			}

			equal(t, slices.Contains(ran, "fable"), name == "coordinator")
		})
	}
}

func TestASubDrivesCoordinatorLaunchesOnOpus(t *testing.T) {
	var ran []string
	env := startEnv(&fakeBus{tail: []client.Record{{Lane: orcaRunner, Topic: orcaTopic, Text: "orca alive 7:00 PM pass 3"}}}, &ran)
	env.Config.Parent = "brook"

	if _, err := env.LaunchCoordinator(context.Background(), filepath.Join("/state", Root, "brief.md")); err != nil {
		t.Fatal(err)
	}

	equal(t, ran[4:6], []string{"opus", "high"})
}

func TestASubDrivesCoordinatorLaunchedBeforeItCouldRegisterIsAdoptedOnOpus(t *testing.T) {
	s, state := memory(t), t.TempDir()
	sessionBrief(t, state, Root, "")
	f := newFake(t, nil, []Row{who(Root, true, t0)})
	f.deps.State, f.deps.Parent = state, "brook"

	tick(t, s, f, t0)

	equal(t, row(t, s, Root).Model, "opus")
}

func TestABriefInAnotherLanesDirectoryLaunchesNothing(t *testing.T) {
	var ran []string
	_, err := startEnv(&fakeBus{}, &ran).launchStanding(context.Background(), standingSessions["meta"], "/state/root/brief.md")
	if err == nil {
		t.Fatal("launched the meta lane from the coordinator's brief")
	}
	contains(t, err.Error(), "<state dir>/meta/brief.md")
	equal(t, len(ran), 0)
}

func TestAnUnknownSessionIsRefused(t *testing.T) {
	_, err := execute(t, LaunchCommand(&app.App{}), "landing", "/state/landing/brief.md")
	if err == nil {
		t.Fatal("launched a session the communicator does not start")
	}
	contains(t, err.Error(), "coordinator|meta|signal")
}

func TestASupervisorStartedFromAnotherLanesSessionLaunchesUnderItsOwnName(t *testing.T) {
	for _, name := range append([]string{LaneEnv, lrLaneEnv}, sessionIdentity...) {
		t.Setenv(name, "pack-install-1")
	}
	claude, seen := t.TempDir(), filepath.Join(t.TempDir(), "seen")
	launcher := filepath.Join(claude, "long-running", "current", "bin", "lr")
	if err := os.MkdirAll(filepath.Dir(launcher), 0o750); err != nil {
		t.Fatal(err)
	}
	script := `#!/bin/sh
printf '%s|%s|%s|%s|%s|%s' "$CLAUDE_LONG_RUNNING_LANE" "$LR_LANE" "$CCI_AGENT_ID" "$CCI_LANE" "$CLAUDE_CODE_SESSION_ID" "$CODEX_THREAD_ID" > "` + seen + `.part" && mv "` + seen + `.part" "` + seen + `"
`
	if err := os.WriteFile(launcher, []byte(script), 0o700); err != nil { //nolint:gosec // the stub has to execute
		t.Fatal(err)
	}

	if err := Adopt(Self); err != nil {
		t.Fatal(err)
	}
	deps, err := Env{Bus: &fakeBus{}, Config: config.Config{Drive: testDrive}, State: t.TempDir(), Claude: claude, Now: func() time.Time { return t0 }}.Deps(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if err := deps.Launch("lane-a", "opus", "high", "/briefs/lane-a.md", "supervisor-launch:lane-a"); err != nil {
		t.Fatal(err)
	}

	deadline := time.Now().Add(10 * time.Second)
	for {
		got, err := os.ReadFile(seen)
		if err == nil {
			equal(t, string(got), "supervisor|supervisor||||")
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("the launcher never ran: %v", err)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func TestEachStandingSessionsLaunchRegistersItsLaneWhateverItsBriefsRole(t *testing.T) {
	roles := map[string]string{"coordinator": "", "meta": "desk", "signal": "watch", "chores": "helper"}
	for _, want := range startSequence {
		t.Run(want.session, func(t *testing.T) {
			s := memory(t)
			role := roles[want.session]
			brief := sessionBrief(t, t.TempDir(), want.lane, role)
			attrs := map[string]string{"model": want.model, "effort": want.effort, "placement": "orca"}
			if role != "" {
				attrs["role"] = role
			}
			f := newFake(t, []Record{spawned(1, want.lane, brief, t0.Add(-time.Minute), attrs)}, []Row{who(want.lane, true, t0)})

			tick(t, s, f, t0)

			lane := row(t, s, want.lane)
			equal(t, []string{lane.Brief, lane.Model, lane.Effort}, []string{brief, want.model, want.effort})
		})
	}
}

func TestAStandingLaneLaunchedBeforeItCouldRegisterIsAdoptedAndListedByItsLaneName(t *testing.T) {
	s, state := memory(t), t.TempDir()
	brief := sessionBrief(t, state, "chores", "helper")
	sessionBrief(t, state, "signal", "watch")
	f := newFake(t, nil, []Row{who("chores", true, t0), who("meta", true, t0), who("signal", false, t0.Add(-time.Hour))})
	f.deps.State = state

	out := tick(t, s, f, t0)

	equal(t, out.Adopted, []string{"chores"})
	equal(t, len(f.launches), 0)
	equal(t, row(t, s, "chores").Brief, brief)
	lines, err := RegistryLines(s, []string{"chores", "meta", "signal"}, t0)
	if err != nil {
		t.Fatal(err)
	}
	contains(t, lines[0], "chores desired live")
	contains(t, lines[0], "standing: the chores duty")
	equal(t, lines[1:], []string{"meta unregistered", "signal unregistered"})
}
