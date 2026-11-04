package lanes

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type macRig struct {
	jobs  string
	boxes string
	ran   []string
	bus   *fakeBus
	check *MacWaits
}

func macJobs(runnable ...time.Time) string {
	var edges []string
	for i, at := range runnable {
		edges = append(edges, fmt.Sprintf(`{"node":{"label":"mac job %d","runnableAt":%q,"build":{"url":"https://buildkite.com/org/test/builds/%d"}}}`, i+1, at.Format(time.RFC3339), i+1))
	}
	return `{"organization":{"jobs":{"edges":[` + strings.Join(edges, ",") + `]}}}`
}

func macBox(id, lane, os string, created time.Time) string {
	labels := "{}"
	if lane != "" {
		labels = fmt.Sprintf(`{"lane":%q}`, lane)
	}
	return fmt.Sprintf(`{"cluster_id":%q,"created_at":%q,"labels":%s,"shape":{"virtual_cpu":6,"os":%q}}`, id, created.Format(time.RFC3339), labels, os)
}

func macWatch(t *testing.T, jobs string, boxes ...string) *macRig {
	t.Helper()
	rig := &macRig{jobs: jobs, boxes: "[" + strings.Join(boxes, ",") + "]", bus: &fakeBus{}}
	env := Env{Bus: rig.bus, Config: config.Config{Drive: testDrive, Checkout: "/checkout"}, Runner: scripted(func(argv []string) (shell.Result, error) {
		rig.ran = append(rig.ran, argv[0])
		switch argv[0] {
		case tokenTool:
			return shell.Result{Stdout: "read-token\n"}, nil
		case "nsc":
			equal(t, argv, []string{"nsc", "list", "-o", "json"})
			return shell.Result{Stdout: rig.boxes}, nil
		}
		contains(t, strings.Join(argv, " "), `tools/ci bk graphql query { organization(slug: "org") { jobs(first: 50, state: [SCHEDULED], type: [COMMAND], agentQueryRules: ["queue=mac"])`)
		return shell.Result{Stdout: rig.jobs}, nil
	})}
	rig.check = &MacWaits{Env: env, Queue: MacQueue{Org: "org", Queue: "mac", LimitVCPU: 48}, CLI: []string{"tools/ci", "bk"}, Token: bkread.Token{Command: []string{tokenTool}}, Log: slog.Default()}
	return rig
}

func (r *macRig) checkAt(t *testing.T, at time.Time) {
	t.Helper()
	if err := r.check.Check(context.Background(), at); err != nil {
		t.Fatal(err)
	}
}

func TestMacJobsWaitingOverTenMinutesNoteTheHoldersOfTheLongestHeldBoxesOnePerJob(t *testing.T) {
	rig := macWatch(t, macJobs(t0.Add(-mins(25)), t0.Add(-mins(12)), t0.Add(-mins(3))),
		macBox("newest", "lane-c", macOS, t0.Add(-time.Hour)),
		macBox("oldest", "lane-a", macOS, t0.Add(-5*time.Hour-30*time.Minute)),
		macBox("linux", "lane-d", "linux", t0.Add(-9*time.Hour)),
		macBox("middle", "lane-b", macOS, t0.Add(-3*time.Hour)))

	rig.checkAt(t, t0)

	equal(t, len(rig.bus.posted), 2)
	first, second := rig.bus.posted[0], rig.bus.posted[1]
	equal(t, []string{first.Kind, first.Lane, first.To[0], first.ID}, []string{nscRenewed, Self, "lane-a", fmt.Sprintf("mac-wait:oldest:%d", t0.Add(-mins(25)).Unix())})
	equal(t, second.To, []string{"lane-b"})
	contains(t, first.Text, `MAC-WAIT: 2 CI Mac jobs have waited over 10 minutes for an agent; the longest, "mac job 1" in https://buildkite.com/org/test/builds/1, has waited 25 minutes, and a rerun makes Namespace try a fresh create: cancel that job and retry it, never inside a trunk-merge build without asking.`)
	contains(t, first.Text, "Lanes hold 3 macOS boxes, 18 of the 48 vCPU the workspace allows")
	contains(t, first.Text, "Your box oldest, up 5h30m, is number 1 of 3 by age. If its work is done, destroy it with `nsc destroy oldest`; a builder that must stay up belongs on `tools/ci mac-box`.")
	contains(t, second.Text, "Your box middle, up 3h00m, is number 2 of 3 by age.")
}

func TestNoMacJobWaitingTenMinutesListsNoBoxesAndNotesNoOne(t *testing.T) {
	rig := macWatch(t, macJobs(t0.Add(-mins(9))), macBox("a", "lane-a", macOS, t0.Add(-time.Hour)))

	rig.checkAt(t, t0)

	equal(t, len(rig.bus.posted), 0)
	equal(t, rig.ran, []string{tokenTool, "tools/ci"})
}

func TestAHolderIsNotedOncePerWaitAndAgainWhenTheQueueClearsAndWaitsAgain(t *testing.T) {
	rig := macWatch(t, macJobs(t0.Add(-mins(20))), macBox("a", "lane-a", macOS, t0.Add(-time.Hour)))

	rig.checkAt(t, t0)
	rig.checkAt(t, t0.Add(mins(1)))
	equal(t, len(rig.bus.posted), 1)

	rig.jobs = macJobs()
	rig.checkAt(t, t0.Add(mins(2)))
	rig.jobs = macJobs(t0.Add(-mins(10)))
	rig.checkAt(t, t0.Add(mins(3)))

	equal(t, len(rig.bus.posted), 2)
}

func TestAnUnlabeledMacBoxNotesTheCoordinator(t *testing.T) {
	rig := macWatch(t, macJobs(t0.Add(-mins(20))), macBox("a", "", macOS, t0.Add(-time.Hour)))

	rig.checkAt(t, t0)

	equal(t, rig.bus.posted[0].To, []string{Root})
}

func TestACreateOfAMacShapeNamesWhatLanesHoldAndTheLimit(t *testing.T) {
	equal(t, asksForMac([]string{"--duration", "8h", "--machine_type", "macos/arm64:6x14"}), true)
	equal(t, asksForMac([]string{"--machine_type=macos/arm64:6x14"}), true)
	equal(t, asksForMac([]string{"--duration", "8h", "--machine_type", "linux/amd64:4x8"}), false)

	boxes := macHolders([]nscBox{
		{ID: "a", Created: t0, Shape: nscShape{VCPU: 6, OS: macOS}},
		{ID: "b", Created: t0, Shape: nscShape{VCPU: 4, OS: "linux"}},
	})

	equal(t, macHeldWarning(boxes, 48), "Warning, nothing was blocked: lanes already hold 1 macOS boxes, 6 of the 48 vCPU the workspace allows, and CI's mac queue runs on the rest, so each box you add makes a release's Mac build wait longer. Destroy this box when its run ends; a builder that must stay up belongs on `tools/ci mac-box`.")
}
