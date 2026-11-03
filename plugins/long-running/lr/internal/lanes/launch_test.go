package lanes

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var startSequence = []struct {
	session, lane, model, effort string
}{
	{"coordinator", "root", "opus", "xhigh"},
	{"meta", "meta", "opus", "xhigh"},
	{"signal", "signal", "sonnet", "low"},
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

			equal(t, ran, []string{"/claude/long-running/current/bin/desk-launch.py", "lane", want.lane, want.model, want.effort, brief, "--drive", testDrive, "--lane", "owner-relay"})
		})
	}
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
