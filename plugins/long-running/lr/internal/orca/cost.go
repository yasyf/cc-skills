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
)

var (
	astraModel   = regexp.MustCompile(`(?i)(^|[-_.])astra([-_.0-9]|$)`)
	laneCompact  = fmt.Sprintf("%s=%d", compactWindowEnv, laneCompactWindow)
	codexCompact = fmt.Sprintf(" -c %s=%d", codexCompactConfig, codexCompactLimit)
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
	if slices.Contains(lanes.AboveHigh, o.Effort) && !o.incidentLane(text) {
		faults = append(faults, fmt.Sprintf("effort %s is above high, which a lane runs only when the owner named it: %s", o.Effort, fix))
	}
	return faults
}

func codexCompactScript() string {
	return fmt.Sprintf("config=\"${CODEX_HOME:-$HOME/.codex}/config.toml\"\nmkdir -p \"${config%%/*}\"\n{ echo '%[1]s = %[2]d'; grep -v '^%[1]s' \"$config\" 2>/dev/null; } > \"$config.lr\" && mv \"$config.lr\" \"$config\"\n", codexCompactConfig, codexCompactLimit)
}
