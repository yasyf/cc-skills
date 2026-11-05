package update

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func onStepLane(name, state, step string, minutes int) Lane {
	lane := heard(name, state, minutes)
	lane.Step = step
	return lane
}

func rebuild(numbers ...string) *plans.Plan {
	plan := &plans.Plan{ID: "9fba4d899896", Title: "Durable agents rebuild: the plan", Status: "approved"}
	for _, number := range numbers {
		plan.Steps = append(plan.Steps, &plans.Step{Number: number, Title: number + ". Telemetry and the watch", Tasks: []plans.Task{{Title: "Watch", State: plans.InProgress}}})
	}
	return plan
}

func TestAStepsOwnersAreTheUnretiredLanesItsBriefNamesNeverALaneThatOnlyMentionsIt(t *testing.T) {
	owner := onStepLane("telemetry-3", "desired", "4", 5)
	quiet := onStepLane("telemetry-2", "held", "4", 3*60)
	gone := onStepLane("telemetry-1", "retired", "4", 9*60)
	desk := onStepLane("meta-desk-3", "desired", "4", 5)
	desk.Standing = "drive tooling"
	chatty := heard("ship-2", "desired", 5)
	step := rebuild("4").Steps[0]
	if got := namesOf(stepOwners([]Lane{owner, quiet, gone, desk, chatty}, step)); strings.Join(got, ",") != "telemetry-3,telemetry-2" {
		t.Fatalf("owners = %v, want the unretired lanes its brief names", got)
	}
	in := inputs(t)
	in.Plans = []*plans.Plan{rebuild("4")}
	in.Records = append(in.Records, record(9, "ship-2", "report", "step 4 half done", 5))
	text := Render(in).Text
	_, row, found := strings.Cut(text, "\n| Step 4 | ")
	if !found {
		t.Fatalf("no step 4 row in\n%s", text)
	}
	row, _, _ = strings.Cut(row, "\n")
	if who := strings.Split(row, " | ")[whoColumn-1]; who != nobody {
		t.Fatalf("who = %q, want nobody: a lane naming the step in a record does not own it", who)
	}
}

func TestTheUpdateLeadsWithEveryOpenStepOfAnApprovedPlanThatNobodyOwns(t *testing.T) {
	in := inputs(t)
	plan := rebuild("4", "5")
	plan.Steps = append(plan.Steps, &plans.Step{Number: "6", Title: "6. Done", Tasks: []plans.Task{{Title: "Done", State: plans.Done}}})
	draft := rebuild("9")
	draft.Status = planDraft
	in.Plans = []*plans.Plan{plan, draft}
	in.Lanes = append(in.Lanes, onStepLane("telemetry-2", "desired", "5", 5), onStepLane("telemetry-1", "retired", "4", 300))
	report := Render(in)
	section := sectionOf(t, report.Text, unownedStepsTitle)
	if !strings.Contains(section, "step 4 (Telemetry and the watch): no lane holds it; the last lane on it was telemetry-1, retired, heard ") {
		t.Fatalf("the lead lacks step 4:\n%s", section)
	}
	for _, owned := range []string{"step 5", "step 6", "step 9"} {
		if strings.Contains(section, owned) {
			t.Errorf("the lead names %s, which is owned, done or a draft's:\n%s", owned, section)
		}
	}
	if strings.Index(report.Text, "## "+unownedStepsTitle) > strings.Index(report.Text, "## "+workstreamsTitle) {
		t.Fatalf("the lead is not above the workstreams:\n%s", report.Text)
	}
}

type stepBus struct {
	posted []client.Draft
	plans  int
	lanes  int
}

func (b *stepBus) watch(s *store.Store, list []*plans.Plan, registry *[]Lane, at *time.Time) *StepWatch {
	return &StepWatch{
		Store: s,
		Lanes: func(context.Context) ([]Lane, error) { b.lanes++; return *registry, nil },
		Plans: func(context.Context) ([]*plans.Plan, error) { b.plans++; return list, nil },
		Post: func(_ context.Context, draft client.Draft) (client.Record, error) {
			b.posted = append(b.posted, draft)
			return client.Record{Seq: int64(100 + len(b.posted))}, nil
		},
		Drive: testDrive,
		Now:   func() time.Time { return *at },
	}
}

func memoryStore(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func TestTheStepWatchTellsRootOnceWhenAStepLosesItsOwnerAndClosesItWhenOneTakesItOn(t *testing.T) {
	at := now
	registry := make([]Lane, 0, 2)
	registry = append(registry, onStepLane("telemetry-1", "desired", "4", 5))
	list := []*plans.Plan{rebuild("4")}
	b := &stepBus{}
	s := memoryStore(t)
	w := b.watch(s, list, &registry, &at)
	pass := func() {
		t.Helper()
		if err := w.Pass(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	pass()
	if len(b.posted) != 0 {
		t.Fatalf("an owned step posted %+v", b.posted)
	}
	registry[0].State, registry[0].RetiredBy = retired, "done"
	pass()
	pass()
	w = b.watch(s, list, &registry, &at)
	pass()
	if len(b.posted) != 1 {
		t.Fatalf("posted = %+v, want one blocker for the loss, across a supervisor restart", b.posted)
	}
	lost := b.posted[0]
	if lost.Kind != "blocker" || lost.To[0] != root || !strings.HasPrefix(lost.Text, "STEP-UNOWNED Durable agents rebuild: the plan, step 4 (Telemetry and the watch): no lane holds it; the last lane on it was telemetry-1, retired") || !strings.Contains(lost.Text, "--step 4") {
		t.Fatalf("blocker = %+v", lost)
	}
	registry = append(registry, onStepLane("telemetry-2", "desired", "4", 0))
	pass()
	if len(b.posted) != 2 || b.posted[1].Resolves != 101 || b.posted[1].Text != "STEP-OWNED Durable agents rebuild: the plan, step 4 (Telemetry and the watch): telemetry-2 holds it" {
		t.Fatalf("posted = %+v, want a note closing #101", b.posted)
	}
	at = at.Add(3 * time.Hour)
	pass()
	if len(b.posted) != 2 {
		t.Fatalf("posted = %+v, want a quiet owner to keep the step", b.posted)
	}
	registry[1].State = retired
	pass()
	if len(b.posted) != 3 || b.posted[2].Kind != "blocker" || b.posted[2].ID == lost.ID {
		t.Fatalf("posted = %+v, want a fresh blocker for the second loss", b.posted)
	}
	list[0].Steps[0].Tasks[0].State = plans.Done
	at = at.Add(stepPlansEvery)
	pass()
	if len(b.posted) != 4 || b.posted[3].Resolves != 103 || !strings.HasPrefix(b.posted[3].Text, "STEP-CLOSED ") {
		t.Fatalf("posted = %+v, want a done step to close its open blocker #103", b.posted)
	}
}

func TestALaneAttachedToAStepAfterLaunchOwnsItAndClosesItsStepUnowned(t *testing.T) {
	s := memoryStore(t)
	brief := filepath.Join(t.TempDir(), "telemetry-1.brief.md")
	if err := os.WriteFile(brief, []byte("Register slice: lane=telemetry-1 step=all\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := s.WantLane(store.Lane{Name: "telemetry-1", Brief: brief, Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired}, now); err != nil {
		t.Fatal(err)
	}
	if err := s.Advance(lanes.CursorName, 1); err != nil {
		t.Fatal(err)
	}
	at := now
	b := &stepBus{}
	w := b.watch(s, []*plans.Plan{rebuild("4")}, nil, &at)
	w.Lanes = func(context.Context) ([]Lane, error) { return registry(s) }
	if err := w.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(b.posted) != 1 || !strings.HasPrefix(b.posted[0].Text, "STEP-UNOWNED ") || !strings.Contains(b.posted[0].Text, "lr request step <lane> --step 4") {
		t.Fatalf("posted = %+v, want one STEP-UNOWNED naming the attach verb for a lane launched without --step", b.posted)
	}
	attach := lanes.Request{Verb: "step", Lane: "telemetry-1", Step: "4", By: root}.Draft(testDrive)
	feed := []lanes.Record{
		{Seq: 2, Lane: "telemetry-1", Kind: "state", At: now, Text: "telemetry-1 running"},
		{Seq: 3, Lane: attach.Lane, Kind: attach.Kind, At: now, Text: attach.Text, To: attach.To, Fields: lanes.Fields{Attrs: attach.Attrs}},
	}
	deps := lanes.Deps{
		Drive: testDrive, Checkout: t.TempDir(), Briefs: t.TempDir(), RetireAfter: time.Hour,
		Records: func(since int64) ([]lanes.Record, error) {
			return slices.DeleteFunc(slices.Clone(feed), func(r lanes.Record) bool { return r.Seq <= since }), nil
		},
		Who:          func([]string) ([]lanes.Row, error) { return nil, nil },
		Tokens:       func(lanes.Row) int { return 0 },
		Transcript:   func(lanes.Row) string { return "" },
		Post:         func(lanes.Post) error { return nil },
		Launch:       func(string, string, string, string, string) error { return nil },
		Withdraw:     func(string) error { return nil },
		Wake:         func(string, lanes.Row, map[string]any) (bool, error) { return false, nil },
		PRStates:     func([]int) (map[int]string, error) { return map[int]string{}, nil },
		Held:         func() (map[int]bool, error) { return nil, nil },
		Desks:        func() (map[string]string, error) { return nil, nil },
		OpenBranches: func(*store.Store) (map[string][]int, error) { return nil, nil },
		OpenWork:     func() ([]lanes.Record, error) { return nil, nil },
		Owed:         func() ([]deletions.Owed, error) { return nil, nil },
		Present:      func(rows []deletions.Owed) ([]deletions.Owed, error) { return rows, nil },
		PublishNotes: func(string) error { return nil },
		Gaps:         func() ([]string, error) { return nil, nil },
		Orders:       func(*store.Store) ([]owner.Row, error) { return nil, nil },
		Undelivered:  func() ([]owner.Row, error) { return nil, nil },
		Promises:     func() ([]lanes.Promise, error) { return nil, nil },
	}
	if _, err := lanes.Tick(s, deps, now, 1); err != nil {
		t.Fatal(err)
	}
	at = at.Add(time.Minute)
	if err := w.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(b.posted) != 2 || b.posted[1].Resolves != 101 || !strings.HasPrefix(b.posted[1].Text, "STEP-OWNED Durable agents rebuild: the plan, step 4 (Telemetry and the watch): telemetry-") {
		t.Fatalf("posted = %+v, want the attached lane to close STEP-UNOWNED #101", b.posted)
	}
}

func TestTheStepWatchReadsThePlansAndTheRegistryAsOftenAtOneStepAsAtMany(t *testing.T) {
	reads := func(n int) [3]int {
		numbers := make([]string, n)
		registry := make([]Lane, n)
		for i := range n {
			numbers[i] = fmt.Sprint(i)
			registry[i] = onStepLane(fmt.Sprintf("step-%d-1", i), "desired", numbers[i], 5)
		}
		at := now
		b := &stepBus{}
		w := b.watch(memoryStore(t), []*plans.Plan{rebuild(numbers...)}, &registry, &at)
		for range 3 {
			if err := w.Pass(context.Background()); err != nil {
				t.Fatal(err)
			}
			at = at.Add(time.Minute)
		}
		return [3]int{b.plans, b.lanes, len(b.posted)}
	}
	many := scaletest.N()
	if one, at := reads(1), reads(many); one != at || one != [3]int{1, 3, 0} {
		t.Fatalf("plans, registry and posts read at 1 step = %v, at %d = %v; want 1 plan read, 3 registry reads and no post at both", one, many, at)
	}
}
