package plans_test

import (
	"context"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans/planstest"
)

type fields = planstest.Fields

func read(t *testing.T, fake *planstest.Fake, pinned ...string) *plans.Progress {
	t.Helper()
	progress, err := plans.Read(context.Background(), fake.Ccn, planstest.Program, pinned...)
	if err != nil {
		t.Fatal(err)
	}
	return progress
}

func byStep(progress *plans.Progress) map[string][]string {
	found := map[string][]string{}
	for plan, step := range progress.Steps() {
		owner := "-"
		if plan != nil {
			owner = plan.ID[:7]
		}
		items := []string{}
		for _, task := range step.Tasks {
			items = append(items, task.Title+"="+task.State)
		}
		found[owner+":"+step.Number] = items
	}
	return found
}

func TestTasksJoinTheStepTheirLabelNamesElseTheirPlansUnmappedStepElseUnassigned(t *testing.T) {
	fake := &planstest.Fake{
		Tasks: []fields{
			planstest.Task("a", "Ship the reader", fields{"plan": planstest.PlanID, "labels": []string{"program:brook", "step:1"}, "status": "done", "closed_at": "2026-10-02T00:00:00Z"}),
			planstest.Task("b", "Backfill rows", fields{"plan": planstest.PlanID, "labels": []string{"step:2"}, "status": "in_progress", "assignee": "backfill lane <lane@x>", "started_at": "2026-10-03T00:00:00Z"}),
			planstest.Task("c", "No step yet", fields{"plan": planstest.PlanID, "labels": []string{"program:brook", "step:9"}}),
			planstest.Task("d", "Loose end", nil),
			planstest.Task("e", "Someone else's", fields{"labels": []string{"program:other"}}),
		},
		Plans: []fields{planstest.Plan(planstest.PlanID, "Migration")},
	}

	progress := read(t, fake)

	want := map[string][]string{
		"fc8714e:1":        {"Ship the reader=done"},
		"fc8714e:2":        {"Backfill rows=in_progress"},
		"fc8714e:unmapped": {"No step yet=open"},
		"-:unassigned":     {"Loose end=open"},
	}
	if got := byStep(progress); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	titles := []string{}
	for _, step := range progress.Plans[0].Steps {
		titles = append(titles, step.Title)
	}
	if want := []string{"1. Readers first", "2. Delete the old store", "No step label"}; !reflect.DeepEqual(titles, want) {
		t.Fatalf("got %v, want %v", titles, want)
	}
	if want := map[string]int{"done": 1, "in_progress": 1, "blocked": 0, "open": 2}; !reflect.DeepEqual(progress.Counts(), want) {
		t.Fatalf("got %v, want %v", progress.Counts(), want)
	}
	backfill := progress.Plans[0].Steps[1].Tasks[0]
	if backfill.Owner != "backfill lane" || backfill.Since != "2026-10-03T00:00:00Z" {
		t.Fatalf("got %+v", backfill)
	}
}

func TestAnUnfinishedBlockerOrAFailedCriterionBlocksATaskAndSaysWhatItWaitsOn(t *testing.T) {
	fake := &planstest.Fake{Tasks: []fields{
		planstest.Task("a", "Backfill rows", fields{"criteria": []fields{{"id": "1", "text": "every row copied", "status": "met"}, {"id": "2", "text": "readers switched", "status": "pending"}}}),
		planstest.Task("b", "Drop the column", fields{"blocked_by": []string{strings.Repeat("a", 40)}}),
		planstest.Task("c", "Delete the reader", fields{"blocked_by": []string{strings.Repeat("f", 40)}}),
		planstest.Task("f", "Landed already", fields{"status": "done", "labels": []string{}}),
		planstest.Task("g", "Prove the floor", fields{"criteria": []fields{{"id": "3", "text": "sandbox proof passes", "status": "failed"}}}),
		planstest.Task("h", "Dropped", fields{"status": "cancelled"}),
	}}

	progress := read(t, fake)

	waiting := map[string][2]string{}
	states := []string{}
	for _, item := range progress.Unassigned.Tasks {
		waiting[item.Title] = [2]string{item.State, item.Waiting}
		states = append(states, item.State)
	}
	want := map[string][2]string{
		"Backfill rows":     {"open", "readers switched"},
		"Drop the column":   {"blocked", "blocked by Backfill rows (aaaaaaa)"},
		"Delete the reader": {"open", ""},
		"Prove the floor":   {"blocked", "failed: sandbox proof passes"},
	}
	if !reflect.DeepEqual(waiting, want) {
		t.Fatalf("got %v, want %v", waiting, want)
	}
	if want := []string{"blocked", "blocked", "open", "open"}; !reflect.DeepEqual(states, want) {
		t.Fatalf("got %v, want %v", states, want)
	}
	if progress.Cancelled != 1 {
		t.Fatalf("cancelled %d", progress.Cancelled)
	}
}

func TestATaskBlockedByOneNoListNamesIsAnError(t *testing.T) {
	fake := &planstest.Fake{Tasks: []fields{planstest.Task("b", "Drop the column", fields{"blocked_by": []string{strings.Repeat("9", 40)}})}}

	_, err := plans.Read(context.Background(), fake.Ccn, planstest.Program)

	if err == nil || !strings.Contains(err.Error(), "9999999") {
		t.Fatalf("got %v", err)
	}
}

func TestOnlyPlansHoldingTasksOrNamedOutrightAreDrawn(t *testing.T) {
	fake := &planstest.Fake{
		Tasks: []fields{planstest.Task("a", "Ship the reader", fields{"plan": planstest.PlanID})},
		Plans: []fields{planstest.Plan(planstest.PlanID, "Migration"), planstest.Plan(planstest.OtherPlanID, "Idle plan")},
	}
	titles := func(progress *plans.Progress) []string {
		found := []string{}
		for _, plan := range progress.Plans {
			found = append(found, plan.Title)
		}
		return found
	}

	drawn := read(t, fake)
	if want := []string{"Migration"}; !reflect.DeepEqual(titles(drawn), want) {
		t.Fatalf("got %v", titles(drawn))
	}
	if len(drawn.Idle) != 1 || drawn.Idle[0].Title != "Idle plan" {
		t.Fatalf("idle %v", drawn.Idle)
	}
	if got, want := titles(read(t, fake, planstest.OtherPlanID[:7])), []string{"Idle plan", "Migration"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestSummaryLinesNameEachPlanAndItsOpenSteps(t *testing.T) {
	fake := &planstest.Fake{
		Tasks: []fields{
			planstest.Task("a", "Ship the reader", fields{"plan": planstest.PlanID, "labels": []string{"step:1"}, "status": "done"}),
			planstest.Task("b", "Backfill", fields{"plan": planstest.PlanID, "labels": []string{"step:2"}}),
			planstest.Task("d", "Loose end", nil),
		},
		Plans: []fields{planstest.Plan(planstest.PlanID, "Migration")},
	}

	want := []string{"- Plan `fc8714e` Migration: 1 of 2 tasks done, 0 blocked; steps open: 2 0/1", "- 0 of 1 drive tasks outside any plan done"}
	if got := plans.SummaryLines(read(t, fake)); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestAStepHeadingMayLeadWithTheWordStep(t *testing.T) {
	got := []string{}
	for _, step := range plans.StepsOf("### Step 11. Releases: R1a cloud\n### Streaming: no number\n### 2. Data\n") {
		got = append(got, fmt.Sprintf("%s|%s", step.Number, step.Title))
	}
	if want := []string{"11|11. Releases: R1a cloud", "2|2. Data"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestClipCountsCharactersAndCollapsesWhitespace(t *testing.T) {
	if got := plans.Clip("  héllo   wörld  ", 11); got != "héllo wörld" {
		t.Fatalf("got %q", got)
	}
	if got := plans.Clip("héllo wörld", 6); got != "héllo…" {
		t.Fatalf("got %q", got)
	}
}
