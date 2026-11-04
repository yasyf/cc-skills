package board

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var mergeCard = regexp.MustCompile(`^merge:(\d+):([0-9a-f]+)$`)

type DroppedTap struct {
	Block string
	PR    int
	Head  string
	Lane  string
	Why   string
}

type StaleTaps struct {
	Runner  shell.Runner
	Session string
	Say     func(context.Context, DroppedTap) error
}

func staleWhy(shown string, f machine.Facts) string {
	if !namesHead(shown, f.Head) && f.CarriedBy == "" {
		return fmt.Sprintf("its head moved from %s to %s, so his tap no longer covers it", abbreviate(shown), abbreviate(f.Head))
	}
	if f.Graded() && !f.Cleared() {
		return fmt.Sprintf("a grade at %s went major or failed and needs a verdict", abbreviate(f.Head))
	}
	return ""
}

func withoutStale(block map[string]any, facts map[int]machine.Facts) []DroppedTap {
	var dropped []DroppedTap
	groups := []any{}
	for _, group := range listOf(block, "groups") {
		var kept []any
		for _, pr := range listOf(group, "prs") {
			number, _ := strconv.Atoi(prNumber(pr))
			f, open := facts[number]
			why := ""
			if open {
				why = staleWhy(text(pr, "head"), f)
			}
			if why == "" {
				kept = append(kept, pr)
				continue
			}
			dropped = append(dropped, DroppedTap{text(block, "id"), number, text(pr, "head"), f.Lane, why})
		}
		if len(kept) > 0 {
			group["prs"] = kept
			groups = append(groups, group)
		}
	}
	block["groups"] = groups
	return dropped
}

func (t StaleTaps) write(ctx context.Context, rev int64, argv []string, stdin string) (int64, bool, error) {
	done, err := t.Runner.Run(ctx, shell.Command{Argv: append(argv, "--session", t.Session, "--rev", strconv.FormatInt(rev, 10)), Stdin: stdin})
	if writeRefused(err) {
		return rev, false, nil
	}
	if err != nil {
		return rev, false, err
	}
	return landedRev(done.Stdout, rev), true, nil
}

func (t StaleTaps) say(ctx context.Context, dropped []DroppedTap) error {
	for _, tap := range dropped {
		if err := t.Say(ctx, tap); err != nil {
			return err
		}
	}
	return nil
}

func (t StaleTaps) Pass(ctx context.Context, snapshot []machine.Facts) error {
	outcomes, err := ReadOutcomes(ctx, t.Runner, t.Session)
	if err != nil {
		return err
	}
	facts := make(map[int]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		facts[f.Number] = f
	}
	rev := outcomes.Rev
	for _, block := range typed(outcomes.Doc, "pr-groups") {
		dropped := withoutStale(block, facts)
		if len(dropped) == 0 {
			continue
		}
		encoded, err := json.Marshal(block)
		if err != nil {
			return err
		}
		next, wrote, err := t.write(ctx, rev, []string{"cc-present", "update-block", "-"}, string(encoded))
		if err != nil {
			return err
		}
		if wrote {
			rev = next
			if err := t.say(ctx, dropped); err != nil {
				return err
			}
		}
	}
	for _, card := range typed(outcomes.Doc, "card") {
		named := mergeCard.FindStringSubmatch(text(card, "id"))
		if named == nil || !asksOpen(card, outcomes) {
			continue
		}
		number, _ := strconv.Atoi(named[1])
		f, open := facts[number]
		if !open {
			continue
		}
		why := staleWhy(named[2], f)
		if why == "" {
			continue
		}
		next, wrote, err := t.write(ctx, rev, []string{"cc-present", "remove-block", text(card, "id")}, "")
		if err != nil {
			return err
		}
		if wrote {
			rev = next
			if err := t.say(ctx, []DroppedTap{{text(card, "id"), number, named[2], f.Lane, why}}); err != nil {
				return err
			}
		}
	}
	return nil
}
