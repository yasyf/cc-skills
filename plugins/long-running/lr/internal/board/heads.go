package board

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strconv"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type askedPR struct {
	block  map[string]any
	repo   string
	number int
}

func asksOpen(card map[string]any, outcomes Outcomes) bool {
	for _, choice := range typed(card, "choice") {
		answer := outcomes.Interactions.Choices[text(choice, "id")]
		if len(answer.OptionIDs) > 0 || answer.Other != "" {
			return false
		}
	}
	return true
}

func (p Pass) redrawMoved(ctx context.Context, outcomes Outcomes) error {
	var asked []askedPR
	byRepo := map[string][]int{}
	for _, card := range typed(outcomes.Doc, "card") {
		if !asksOpen(card, outcomes) {
			continue
		}
		for _, pr := range typed(card, "pr") {
			if text(pr, "head") == "" {
				continue
			}
			number, _ := strconv.Atoi(prNumber(pr))
			repo := text(pr, "repo")
			asked = append(asked, askedPR{pr, repo, number})
			byRepo[repo] = append(byRepo[repo], number)
		}
	}
	live := map[string]string{}
	for repo, numbers := range byRepo {
		slices.Sort(numbers)
		rows, err := p.CCX.PRStatus(ctx, repo, slices.Compact(numbers))
		if err != nil {
			return err
		}
		for _, row := range rows {
			live[fmt.Sprintf("%s#%d", repo, row.Number)] = row.Head
		}
	}
	for _, pr := range asked {
		now := live[fmt.Sprintf("%s#%d", pr.repo, pr.number)]
		if now == "" || namesHead(text(pr.block, "head"), now) {
			continue
		}
		pr.block["head"] = short(now)
		encoded, err := json.Marshal(pr.block)
		if err != nil {
			return err
		}
		if _, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "update-block", "-", "--session", p.Session}, Stdin: string(encoded)}); err != nil {
			return err
		}
	}
	return nil
}
