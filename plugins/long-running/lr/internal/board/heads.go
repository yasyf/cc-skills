package board

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type askedPR struct {
	block  map[string]any
	card   string
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
			asked = append(asked, askedPR{pr, text(card, "id"), repo, number})
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
	revs := map[string]int64{}
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
		rev, wrote := revs[pr.card]
		if !wrote {
			rev = outcomes.Rev
		}
		done, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "update-block", "-", "--session", p.Session, "--rev", strconv.FormatInt(rev, 10)}, Stdin: string(encoded)})
		if writeRefused(err) {
			continue
		}
		if err != nil {
			return err
		}
		revs[pr.card] = landedRev(done.Stdout, rev)
	}
	return nil
}

var landedLine = regexp.MustCompile(`(?m)^rev: (\d+)$`)

// landedRev is the rev a cc-present write printed last, or read when it printed
// none. A write that also posts its card prints one rev per step.
func landedRev(stdout string, read int64) int64 {
	lines := landedLine.FindAllStringSubmatch(stdout, -1)
	if len(lines) == 0 {
		return read
	}
	rev, err := strconv.ParseInt(lines[len(lines)-1][1], 10, 64)
	if err != nil {
		panic(err)
	}
	return rev
}

// writeRefused reports cc-present refusing a write because the board moved past
// the read it was built from or a lane owns the card. The next pass reads again.
func writeRefused(err error) bool {
	var exit *shell.ExitError
	return errors.As(err, &exit) && strings.Contains(exit.Stderr, "refusing to write")
}
