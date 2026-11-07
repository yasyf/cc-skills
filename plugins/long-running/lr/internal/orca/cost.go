package orca

import (
	"fmt"
	"regexp"
	"slices"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	compactWindowEnv   = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
	laneCompactWindow  = 333000
	codexCompactLimit  = 300000
	codexCompactConfig = "model_auto_compact_token_limit"
	briefReadBudget    = 32000
	briefBytesPerToken = 3
	execOutputDefault  = 10000
	codexOutputConfig  = "tool_output_token_limit"
)

var (
	astraModel  = regexp.MustCompile(`(?i)(^|[-_.])astra([-_.0-9]|$)`)
	laneCompact = fmt.Sprintf("%s=%d", compactWindowEnv, laneCompactWindow)
)

func (o Order) costFaults(text string) []string {
	if row, desk := lanes.DeskCostOf(o.Lane); desk && row.Model == o.Model && row.Effort != o.Effort {
		return []string{fmt.Sprintf("%s is a desk, and the desk table runs it on %s at %s, not %s: launch `lr launch lane %s %s %s %s`", o.Lane, row.Model, row.Effort, o.Effort, o.Lane, row.Model, row.Effort, o.Brief)}
	}
	if lanes.BriefOwnerPick.MatchString(text) {
		return nil
	}
	standard := "opus"
	if astraModel.MatchString(o.Model) {
		standard = "sol"
	}
	fix := fmt.Sprintf("add the line `%s owner:m<row>` naming his words to brief %s, or launch `lr launch lane %s %s high %s`", lanes.OwnerPickLine, o.Brief, o.Lane, standard, o.Brief)
	var faults []string
	if o.Lane != coordinatorLane && (fableModel.MatchString(o.Model) || astraModel.MatchString(o.Model)) {
		faults = append(faults, fmt.Sprintf("%s starts only when the owner named it for this work: %s", o.Model, fix))
	}
	if slices.Contains(lanes.AboveHigh, o.Effort) {
		faults = append(faults, fmt.Sprintf("effort %s is above high, which a lane runs only when the owner named it: %s", o.Effort, fix))
	}
	return faults
}

func codexReadBudget(briefBytes int) int {
	return max(briefReadBudget, briefBytes/briefBytesPerToken)
}

func codexLimits(budget int) string {
	return fmt.Sprintf(" -c %s=%d -c %s=%d", codexCompactConfig, codexCompactLimit, codexOutputConfig, budget)
}

func codexLimitsScript(budget int) string {
	return fmt.Sprintf("config=\"${CODEX_HOME:-$HOME/.codex}/config.toml\"\nmkdir -p \"${config%%/*}\"\n{ echo '%[1]s = %[2]d'; echo '%[3]s = %[4]d'; grep -v -e '^%[1]s' -e '^%[3]s' \"$config\" 2>/dev/null; } > \"$config.lr\" && mv \"$config.lr\" \"$config\"\n", codexCompactConfig, codexCompactLimit, codexOutputConfig, budget)
}

func codexBriefRead(brief string, budget int) string {
	return fmt.Sprintf(" Print it whole in one call, `ccx code read --full --budget %[1]d %[2]s`, and give that call and any `exec` around it a `max_output_tokens` of %[1]d, as in a first line `// @exec: {\"max_output_tokens\": %[1]d}`, since an exec result stops at %[3]d tokens without it. Print the command's output text itself, never its result object, whose JSON escaping outgrows the budget. Never read it by section or in pieces.", budget, brief, execOutputDefault)
}
