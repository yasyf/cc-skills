package update

import (
	"context"
	"fmt"
	"iter"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	unownedStepsTitle = "Plan steps nobody owns"
	stepUnowned       = "step-unowned"
	stepPlansEvery    = 15 * time.Minute
	planDraft         = "draft"
	planIDChars       = 7
)

func heardLive(lane Lane, now time.Time) bool {
	return !slices.Contains(selves, lane.Name) && slices.Contains(posting, lane.State) && !lane.Heard.IsZero() && now.Sub(lane.Heard) < heardWithin
}

func holdsStep(lane Lane, step *plans.Step) bool {
	return lane.Standing == "" && onStep(lane, step)
}

func stepOwners(lanes []Lane, step *plans.Step) []Lane {
	return lanesWhere(lanes, func(lane Lane) bool {
		return holdsStep(lane, step) && !slices.Contains(selves, lane.Name) && slices.Contains(posting, lane.State)
	})
}

func lastHolder(lanes []Lane, step *plans.Step) string {
	var last Lane
	for _, lane := range lanes {
		if holdsStep(lane, step) && (last.Name == "" || lane.Heard.After(last.Heard)) {
			last = lane
		}
	}
	if last.Name == "" {
		return "no lane's brief has named it"
	}
	return fmt.Sprintf("the last lane on it was %s, %s, heard %s", last.Name, last.State, pacificText(last.Heard))
}

func ownedSteps(list []*plans.Plan) iter.Seq2[*plans.Plan, *plans.Step] {
	return func(yield func(*plans.Plan, *plans.Step) bool) {
		for _, plan := range list {
			if plan.Status == planDraft {
				continue
			}
			for _, step := range plan.Steps {
				if step.Number != plans.Unmapped && !stepIsDone(step) && !yield(plan, step) {
					return
				}
			}
		}
	}
}

func stepName(plan *plans.Plan, step *plans.Step) string {
	return fmt.Sprintf("%s, step %s (%s)", plainTitle(plan.Title), step.Number, plainTitle(step.Title))
}

func unownedStepLines(list []*plans.Plan, lanes []Lane) []string {
	var lines []string
	for plan, step := range ownedSteps(list) {
		if len(stepOwners(lanes, step)) == 0 {
			lines = append(lines, fmt.Sprintf("- %s: no lane holds it; %s", stepName(plan, step), lastHolder(lanes, step)))
		}
	}
	return lines
}

func unownedStepsSection(lines []string) []string {
	if len(lines) == 0 {
		return nil
	}
	return titled(unownedStepsTitle, lines)
}

func stepKey(plan *plans.Plan, step *plans.Step) string {
	return fmt.Sprintf("%s:%s:%s", stepUnowned, plan.ID[:min(planIDChars, len(plan.ID))], step.Number)
}

type StepWatch struct {
	Store *store.Store
	Lanes func(ctx context.Context) ([]Lane, error)
	Plans func(ctx context.Context) ([]*plans.Plan, error)
	Post  func(ctx context.Context, draft client.Draft) (client.Record, error)
	Drive string
	Now   func() time.Time

	read  time.Time
	plans []*plans.Plan
}

func (w *StepWatch) Pass(ctx context.Context) error {
	now := w.Now()
	refreshed := w.read.IsZero() || now.Sub(w.read) >= stepPlansEvery
	if refreshed {
		list, err := w.Plans(ctx)
		if err != nil {
			return err
		}
		w.plans, w.read = list, now
	}
	registry, err := w.Lanes(ctx)
	if err != nil {
		return err
	}
	open := map[string]bool{}
	for plan, step := range ownedSteps(w.plans) {
		key := stepKey(plan, step)
		open[key] = true
		if owners := stepOwners(registry, step); len(owners) > 0 {
			err = w.close(ctx, key, fmt.Sprintf("STEP-OWNED %s: %s holds it", stepName(plan, step), strings.Join(namesOf(owners), ", ")))
		} else {
			err = w.raise(ctx, key, fmt.Sprintf("STEP-UNOWNED %s: no lane holds it; %s. | coordinator: launch its owner with `lr launch lane <lane> --step %s --title <title> --body <file>`, or relaunch the last lane on it", stepName(plan, step), lastHolder(registry, step), step.Number), now)
		}
		if err != nil {
			return err
		}
	}
	if !refreshed {
		return nil
	}
	raised, err := w.Store.Escalations("")
	if err != nil {
		return err
	}
	for _, each := range raised {
		if strings.HasPrefix(each.Subject, stepUnowned+":") && !open[each.Subject] {
			if err := w.close(ctx, each.Subject, fmt.Sprintf("STEP-CLOSED %s: the step is done, or no open plan has it now", each.Subject)); err != nil {
				return err
			}
		}
	}
	return nil
}

func (w *StepWatch) episode(key string) (string, int64, error) {
	raised, err := w.Store.Escalations(key)
	if err != nil || len(raised) == 0 {
		return "", 0, err
	}
	id := fmt.Sprintf("%s:%d", key, raised[0].Raised.Unix())
	seq, err := w.Store.Cursor(id)
	return id, seq, err
}

func (w *StepWatch) raise(ctx context.Context, key, text string, now time.Time) error {
	id, seq, err := w.episode(key)
	if err != nil || seq != 0 {
		return err
	}
	if id == "" {
		if _, err := w.Store.Escalate(key, stepUnowned, root, now); err != nil {
			return err
		}
		if id, _, err = w.episode(key); err != nil {
			return err
		}
	}
	posted, err := w.Post(ctx, client.Draft{Drive: w.Drive, Lane: lanes.Self, Kind: "blocker", To: []string{root}, Topic: stepUnowned, ID: id, Text: text})
	if err != nil {
		return err
	}
	return w.Store.Advance(id, posted.Seq)
}

func (w *StepWatch) close(ctx context.Context, key, text string) error {
	id, seq, err := w.episode(key)
	if err != nil || id == "" {
		return err
	}
	if seq != 0 {
		if _, err := w.Post(ctx, client.Draft{Drive: w.Drive, Lane: lanes.Self, Kind: "note", To: []string{root}, Topic: stepUnowned, ID: id + ":closed", Resolves: seq, Text: text}); err != nil {
			return err
		}
	}
	return w.Store.ClearEscalation(key)
}
