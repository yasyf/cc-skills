package orca

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

type stageBus struct {
	Bus
	drafts []client.Draft
}

func (b *stageBus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	b.drafts = append(b.drafts, d)
	return client.Record{At: time.Now()}, nil
}

const prepareLog = `time=2026-11-04T18:02:46.025-08:00 level=INFO msg="claimed a warm workspace" lane=a-1 workspace=pool-dead ref=dev
time=2026-11-04T18:03:46.341-08:00 level=WARN msg="a claimed warm workspace failed before its worker started, so the lane tries the next" lane=a-1 workspace=pool-dead failure="exit unknown"
time=2026-11-04T18:03:46.675-08:00 level=INFO msg="claimed a warm workspace" lane=a-1 workspace=pool-good ref=dev
time=2026-11-04T18:03:56.528-08:00 level=INFO msg="the tools are ready at the current stamp" machine=pool-good stamp=6c433b6dce15
time=2026-11-04T18:03:58.668-08:00 level=INFO msg=phase phase=checkout lane=checkout machine=pool-good start=19.908 end=21.991 seconds=2.083 ok=true
time=2026-11-04T18:03:59.668-08:00 level=INFO msg="the tailnet no longer holds this workspace's node; enrolling again" resource=pool-good node=n1
time=2026-11-04T18:04:08.643-08:00 level=INFO msg="the Orca runtime is ready" machine=pool-good runtime=718b6a59 service=cc-remote-orca
time=2026-11-04T18:04:30.871-08:00 level=INFO msg=timing operation=startup.config seconds=0.004 ok=true
time=2026-11-04T18:04:50.867-08:00 level=INFO msg="started the worker" terminal=term_a agent=claude model=claude-opus-5-5
`

func TestPrepareOutputReadsAsStagesThatNameTheSpriteTheLaunchWaitsOn(t *testing.T) {
	bus := &stageBus{}
	stages := launchStages{bus: bus, drive: "prog", key: "R7", lane: "a-1"}
	writer := &lineWriter{line: func(line string) {
		if stage := prepareStage(line); stage != "" {
			stages.say(context.Background(), stage)
		}
	}}
	cut := len(prepareLog) / 2
	for _, chunk := range []string{prepareLog[:cut], prepareLog[cut:]} {
		if _, err := writer.Write([]byte(chunk)); err != nil {
			t.Fatal(err)
		}
	}
	said := make([]string, 0, len(bus.drafts))
	for _, draft := range bus.drafts {
		if draft.Lane != deskRunner || draft.Kind != kindState || draft.Topic != "R7" || draft.Drive != "prog" || len(draft.To) != 0 {
			t.Fatalf("%+v", draft)
		}
		said = append(said, draft.Text)
	}
	want := []string{
		"STAGE R7 a-1: claimed warm Sprite pool-dead",
		"STAGE R7 a-1: warm Sprite pool-dead failed; trying the next",
		"STAGE R7 a-1: claimed warm Sprite pool-good",
		"STAGE R7 a-1: tools ready on pool-good",
		"STAGE R7 a-1: pool-good rejoining the tailnet",
		"STAGE R7 a-1: Orca runtime ready on pool-good",
		"STAGE R7 a-1: agent started",
	}
	if !slices.Equal(said, want) {
		t.Fatalf("said:\n%s", strings.Join(said, "\n"))
	}
}

func TestALaunchRunByHandPostsNoStage(t *testing.T) {
	bus := &stageBus{}
	stagesOf(func(string) string { return "" }, bus, "a-1", nil).say(context.Background(), "preparing a Sprite")
	if len(bus.drafts) != 0 {
		t.Fatalf("%+v", bus.drafts)
	}
}

func TestACallerSeesEachStageWithItsSecondsAndALaunchPastItsTimeoutNamesTheLast(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "b.md", "work\n"+briefLines)
	now := time.Now()
	f.poster.Now = func() time.Time { return now }
	step := 0
	f.poster.Sleep = func(time.Duration) {
		step++
		now = now.Add(30 * time.Second)
		if step != 1 {
			return
		}
		f.bus.mu.Lock()
		defer f.bus.mu.Unlock()
		last := f.bus.records[len(f.bus.records)-1]
		for i, text := range []string{"STAGE R102 slow-1: claimed warm Sprite pool-dead", "STAGE R999 other-1: claimed warm Sprite pool-other"} {
			f.bus.records = append(f.bus.records, client.Record{Seq: last.Seq + 1 + int64(i), Lane: deskRunner, Kind: kindState, At: last.At.Add(95 * time.Second), Text: text})
		}
	}
	label, err := f.poster.Launch(context.Background(), Order{Lane: "slow-1", Model: "sol", Effort: "high", Brief: brief}, time.Minute)
	if err != nil || label != verdictPosted {
		t.Fatal(label, err)
	}
	out := f.out.String()
	if !strings.Contains(out, "slow-1 R102 at 95 s: claimed warm Sprite pool-dead\n") {
		t.Fatal(out)
	}
	if strings.Contains(out, "pool-other") {
		t.Fatalf("another launch's stage printed: %s", out)
	}
	if want := fmt.Sprintf("slow-1 R102 is waiting on the stage after claimed warm Sprite pool-dead, reached at 95 s; read its next with `cci state --drive prog --lane %s --topic R102`\n", deskRunner); !strings.Contains(out, want) {
		t.Fatal(out)
	}
}

func TestALaunchNoRunnerStartedSaysSoPastItsTimeout(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "b.md", "work\n"+briefLines)
	now := time.Now()
	f.poster.Now = func() time.Time { return now }
	f.poster.Sleep = func(time.Duration) { now = now.Add(30 * time.Second) }
	if _, err := f.poster.Launch(context.Background(), Order{Lane: "slow-1", Model: "sol", Effort: "high", Brief: brief}, time.Minute); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(f.out.String(), "slow-1 R102 has reached no stage, so the runner has not started it;") {
		t.Fatal(f.out)
	}
}
