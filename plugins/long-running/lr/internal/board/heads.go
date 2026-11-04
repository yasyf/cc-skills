package board

import (
	"errors"
	"regexp"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func asksOpen(card map[string]any, outcomes Outcomes) bool {
	for _, choice := range typed(card, "choice") {
		answer := outcomes.Interactions.Choices[text(choice, "id")]
		if len(answer.OptionIDs) > 0 || answer.Other != "" {
			return false
		}
	}
	return true
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
