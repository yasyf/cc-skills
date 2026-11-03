package orca

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/carry"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	namesNothing = "none"
	roleFix      = "fix"
	briefFile    = "brief.md"
	sliceLine    = "Register slice:"
	handoffLane  = "-handoff"
)

var (
	scaleRule     = time.Date(2026, 10, 30, 13, 11, 35, 0, time.UTC)
	briefSlice    = regexp.MustCompile(`(?m)^` + sliceLine)
	briefScale    = regexp.MustCompile(`(?m)^[ \t>*_-]*Scale:`)
	briefIncident = regexp.MustCompile(`(?m)^ccx:.*\bincident=([\w.-]+)`)
	briefPlace    = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?place=(remote|local)(?:\s|$)`)
	fableModel    = regexp.MustCompile(`(?i)(^|[-_.])fable([-_.0-9]|$)`)
	successorLane = regexp.MustCompile(`^.+-(\d+)$`)
	notFixRoles   = []string{"evidence", "proof", "closeout", "desk", "audit"}
	fixRoles      = []string{roleFix, agentIncident}
	efforts       = []string{"low", "medium", "high", "xhigh", "max"}
)

type Order struct {
	Lane, Model, Effort, Role, Brief string
	Now                              bool
}

func (o Order) Line() string {
	line := "launch " + o.Lane
	if o.Now {
		line += " NOW"
	}
	line += " " + o.Model + " " + o.Effort
	if o.Role != "" {
		line += " role=" + o.Role
	}
	return line + " brief=" + o.Brief
}

func submatches(pattern *regexp.Regexp, text string) []string {
	matches := pattern.FindAllStringSubmatch(text, -1)
	found := make([]string, len(matches))
	for i, match := range matches {
		found[i] = match[1]
	}
	return found
}

func (o Order) incidentFix(text string) bool {
	roles := submatches(briefRole, text)
	if o.Role != "" {
		roles = append(roles, o.Role)
	}
	for _, role := range roles {
		if !slices.Contains(fixRoles, role) {
			return false
		}
	}
	for part := range strings.SplitSeq(o.Lane, "-") {
		if slices.Contains(notFixRoles, part) {
			return false
		}
	}
	named := slices.ContainsFunc(submatches(briefIncident, text), func(incident string) bool { return incident != namesNothing })
	return o.Model == agentIncident || slices.Contains(roles, agentIncident) || named
}

func (o Order) launchFaults(text string, readable bool) []string {
	var faults []string
	if _, _, known := modelRoute(o.Model); !known {
		faults = append(faults, o.Model+" is not a model lr launch starts: opus, sonnet, fable, claude-*, sol, codex, incident, astra, or gpt-*")
	}
	if !validEffort(o.Effort) {
		faults = append(faults, o.Effort+" is not an effort: "+strings.Join(efforts, ", "))
	}
	if !readable {
		return append(faults, "brief "+o.Brief+" is not a file")
	}
	if !briefSlice.MatchString(text) && o.Model != agentIncident && !o.incidentFix(text) {
		faults = append(faults, fmt.Sprintf("brief %s has no `%s lane=<name> step=<n>` line; add one and run `rulings.py slice --refresh %s`", o.Brief, sliceLine, o.Brief))
	}
	return faults
}

func (o Order) wrongLaneFault() string {
	owner := filepath.Base(filepath.Dir(o.Brief))
	if filepath.Base(o.Brief) != briefFile || owner == o.Lane {
		return ""
	}
	order := o
	order.Lane = owner
	return fmt.Sprintf("brief %s is lane %s's, and a lane keeps one name from launch on; post `%s: %s`", o.Brief, owner, deskLane, order.Line())
}

func owesScale(lane, text string, written time.Time) bool {
	if briefScale.MatchString(text) || strings.HasSuffix(lane, handoffLane) || briefReplaces.MatchString(text) {
		return false
	}
	if numbered := successorLane.FindStringSubmatch(lane); numbered != nil {
		if n, err := strconv.Atoi(numbered[1]); err != nil || n >= 2 {
			return false
		}
	}
	return !written.Before(scaleRule)
}

func (o Order) scaleFault(text string, written time.Time) string {
	if o.Model == agentIncident || o.Role == agentIncident || o.incidentFix(text) || !owesScale(o.Lane, text, written) {
		return ""
	}
	return "brief " + o.Brief + " has no `Scale:` line; add one line to it: `Scale: no N`, or the N its paths run at, such as `Scale: chats on one Mac, up to 1,000`"
}

func macPinned(model, text string) bool {
	placed := briefPlace.FindStringSubmatch(text)
	return fableModel.MatchString(model) || model == agentIncident || (placed != nil && placed[1] == "local")
}

func carryFault(ctx context.Context, runner shell.Runner, home, repo, brief, text string) (string, error) {
	into, err := os.MkdirTemp("", "lr-carry")
	if err != nil {
		return "", err
	}
	defer func() { _ = os.RemoveAll(into) }()
	if _, err := (carry.Carrier{Runner: runner, Home: home, Repo: repo, Into: into, Box: into}).Carry(ctx, text); err != nil {
		return fmt.Sprintf("brief %s names what no Sprite can receive: %v", brief, err), nil
	}
	return "", nil
}

func (o Order) faults(ctx context.Context, runner shell.Runner, home, repo string) ([]string, error) {
	raw, readErr := os.ReadFile(o.Brief)
	text := string(raw)
	faults := o.launchFaults(text, readErr == nil)
	if wrong := o.wrongLaneFault(); wrong != "" {
		faults = append(faults, wrong)
	}
	if readErr != nil {
		return faults, nil
	}
	info, err := os.Stat(o.Brief)
	if err != nil {
		return nil, err
	}
	if scale := o.scaleFault(text, info.ModTime()); scale != "" {
		faults = append(faults, scale)
	}
	if macPinned(o.Model, text) {
		return faults, nil
	}
	carried, err := carryFault(ctx, runner, home, repo, o.Brief, text)
	if err != nil {
		return nil, err
	}
	if carried != "" {
		faults = append(faults, carried)
	}
	return faults, nil
}
