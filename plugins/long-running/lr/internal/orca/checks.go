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

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/carry"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	namesNothing   = "none"
	roleFix        = "fix"
	briefFile      = "brief.md"
	sliceLine      = "Register slice:"
	stepAll        = "all"
	stepIncident   = "incident"
	handoffLane    = "-handoff"
	whyPredecessor = "predecessor-worked-here"
	whySentry      = "sentry"
)

var (
	scaleRule     = time.Date(2026, 10, 30, 13, 11, 35, 0, time.UTC)
	briefSlice    = regexp.MustCompile(`(?m)^` + sliceLine)
	briefScale    = regexp.MustCompile(`(?m)^[ \t>*_-]*Scale:`)
	briefCCX      = regexp.MustCompile(`(?m)^ccx:.*$`)
	briefIncident = regexp.MustCompile(`(?m)^ccx:.*\bincident=([\w.-]+)`)
	briefStanding = regexp.MustCompile(`(?m)^Standing: *\S`)
	briefSlack    = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?mcp=slack(?:\s|$)`)
	briefMonitor  = regexp.MustCompile(`(?m)^ccx:.*\bmonitor=(\d{4,})`)
	briefPlace    = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?place=(remote|local)(?:\s|$)`)
	briefWhy      = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?why=(\S+)`)
	briefRead     = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?role=read(?:\s|$)`)
	briefReader   = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?role=reader(?:\s|$)`)
	incidentNamed = regexp.MustCompile(`^(?:incident|outage)-`)
	fableModel    = regexp.MustCompile(`(?i)(^|[-_.])fable([-_.0-9]|$)`)
	successorLane = regexp.MustCompile(`^.+-(\d+)$`)
	notFixRoles   = []string{"evidence", "proof", "closeout", "desk", "audit"}
	fixRoles      = []string{roleFix, agentIncident}
	efforts       = []string{"low", "medium", "high", "xhigh", "max"}
)

type Order struct {
	Lane, Model, Effort, Role, Brief, ID string
	Now                                  bool
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

func (o Order) incidentLane(text string) bool {
	return o.Model == agentIncident || o.Model == modelIncidentOpus || o.Role == agentIncident || o.incidentFix(text)
}

func (o Order) sliceStep(text string) string {
	if o.incidentLane(text) {
		return stepIncident
	}
	return stepAll
}

func (o Order) sliceWarning(text string) string {
	if briefSlice.MatchString(text) || o.incidentLane(text) {
		return ""
	}
	return fmt.Sprintf("brief %s has no `%s lane=<name> step=<n>` line; %s launches anyway with `step=%s` written for it, so name its plan step if it has one", o.Brief, sliceLine, o.Lane, stepAll)
}

func (o Order) launchFaults(text string, readable bool) []string {
	var faults []string
	if _, _, known := modelRoute(o.Model); !known {
		faults = append(faults, o.Model+" is not a model lr launch starts: opus, sonnet, fable, claude-*, sol, codex, incident, incident-opus, astra, or gpt-*")
	}
	if !validEffort(o.Effort) {
		faults = append(faults, o.Effort+" is not an effort: "+strings.Join(efforts, ", "))
	}
	if !readable {
		return append(faults, "brief "+o.Brief+" is not a file")
	}
	faults = append(faults, o.costFaults(text)...)
	if unneeded := o.unneededLocal(text); unneeded != "" {
		faults = append(faults, unneeded)
	}
	role := o.Lane
	if numbered := successorLane.FindStringSubmatch(o.Lane); numbered != nil {
		role = strings.TrimSuffix(o.Lane, "-"+numbered[1])
	}
	ownMailbox := regexp.MustCompile(`(?m)\blr wait\b.*\bmailbox:(` + regexp.QuoteMeta(role) + `(?:-\d+)?)=`)
	if waits := ownMailbox.FindStringSubmatch(text); waits != nil {
		faults = append(faults, fmt.Sprintf("brief %s waits on `mailbox:%s`, and no team roster lists an lr-launched lane, so that lr wait exits 1; wait on `cci:<drive>:%s`", o.Brief, waits[1], o.Lane))
	}
	return faults
}

func (o Order) wrongLaneFault() string {
	owner := filepath.Base(filepath.Dir(o.Brief))
	if filepath.Base(o.Brief) != briefFile || owner == o.Lane || owner == client.Stem(o.Lane) {
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
	if o.incidentLane(text) || !owesScale(o.Lane, text, written) {
		return ""
	}
	return "brief " + o.Brief + " has no `Scale:` line; add one line to it: `Scale: no N`, or the N its paths run at, such as `Scale: chats on one Mac, up to 1,000`"
}

func macPinned(lane, model, text string) bool {
	return macRequired(lane, model, text) || macPreferred(text)
}

func macRequired(lane, model, text string) bool {
	return alwaysOnMac(lane, model) || placedLocal(text) && namesMacNeed(text)
}

func namesMacNeed(text string) bool {
	return slices.ContainsFunc(submatches(briefWhy, text), func(why string) bool { return slices.Contains(lanes.MacNeeds, why) })
}

func alwaysOnMac(lane, model string) bool {
	return lane == coordinatorLane || fableModel.MatchString(model)
}

func unreasonedLocal(lane, model, text string) string {
	if !placedLocal(text) || len(submatches(briefWhy, text)) > 0 || macRequired(lane, model, text) || macPreferred(text) {
		return ""
	}
	return fmt.Sprintf("%s's brief says place=local but names no why=<need>, so it launches on a Sprite; a lane that needs the Mac names its need on its ccx line as %s", lane, lanes.MacNeedsLine)
}

func (o Order) unneededLocal(text string) string {
	whys := submatches(briefWhy, text)
	if !placedLocal(text) || len(whys) == 0 || macRequired(o.Lane, o.Model, text) || macPreferred(text) || incidentLaunch(o, text) {
		return ""
	}
	return fmt.Sprintf("%s's brief says place=local why=%s, which is no need only this Mac meets; name its need on its ccx line as %s, or drop place=local so it starts on a Sprite", o.Lane, strings.Join(whys, ","), lanes.MacNeedsLine)
}

func claudeWithoutSentry(lane, model, text string) string {
	if agent, _, _ := modelRoute(model); agent != agentClaude || !slices.Contains(submatches(briefWhy, text), whySentry) {
		return ""
	}
	return fmt.Sprintf("%s's brief says why=%s, but a Claude lane has no Sentry tools; launch it with --model sol", lane, whySentry)
}

func macPreferred(text string) bool {
	return placedLocal(text) && (briefRead.MatchString(text) || slices.Contains(submatches(briefWhy, text), whyPredecessor))
}

func capFallsBack(o Order, text string) bool {
	if !canLeaveMac(o, text) {
		return false
	}
	return !placedLocal(text) || !namesMacNeed(text) || incidentLaunch(o, text)
}

func failureFallsBack(o Order, text string) bool {
	return canLeaveMac(o, text) && incidentLaunch(o, text)
}

func canLeaveMac(o Order, text string) bool {
	return o.Lane != coordinatorLane && !fableModel.MatchString(o.Model) && !slices.Contains(submatches(briefWhy, text), whySentry)
}

func incidentLaunch(o Order, text string) bool {
	return o.incidentLane(text) || incidentBrief(o.Lane, text)
}

func incidentBrief(lane, text string) bool {
	named := slices.ContainsFunc(submatches(briefIncident, text), func(incident string) bool { return incident != namesNothing })
	return incidentNamed.MatchString(lane) || named || strings.HasSuffix(lane, "-"+roleFix) && regexp.MustCompile(`(?m)^`+sliceLine+` lane=`+regexp.QuoteMeta(lane)+` step=`+stepIncident+`\b`).MatchString(text)
}

func placedLocal(text string) bool {
	placed := briefPlace.FindStringSubmatch(text)
	return placed != nil && placed[1] == "local"
}

func carryWarnings(ctx context.Context, runner shell.Runner, home, repo, driveDir, brief, text string) ([]string, error) {
	into, err := os.MkdirTemp("", "lr-carry")
	if err != nil {
		return nil, err
	}
	defer func() { _ = os.RemoveAll(into) }()
	_, left := (carry.Carrier{Runner: runner, Home: home, Repo: repo, Into: into, Box: into, Vars: map[string]string{carry.DriveDir: driveDir}}).Carry(ctx, text)
	warnings := make([]string, 0, len(left))
	for _, behind := range left {
		warnings = append(warnings, fmt.Sprintf("brief %s names what stays behind on a Sprite: %s", brief, behind))
	}
	return warnings, nil
}

func (o Order) faults(ctx context.Context, runner shell.Runner, home, repo, driveDir string) ([]string, []string, error) {
	raw, readErr := os.ReadFile(o.Brief)
	text := string(raw)
	faults := o.launchFaults(text, readErr == nil)
	if wrong := o.wrongLaneFault(); wrong != "" {
		faults = append(faults, wrong)
	}
	if readErr != nil {
		return faults, nil, nil
	}
	if fields := lanes.UnfilledFields(text); len(fields) > 0 {
		faults = append(faults, fmt.Sprintf("brief %s still holds the template fields %s, which nothing filled; fill them, launch an incident template with `lr launch incident --alert`, or write a field the brief quotes as text doubled, as {{lane}}", o.Brief, strings.Join(fields, ", ")))
	}
	info, err := os.Stat(o.Brief)
	if err != nil {
		return nil, nil, err
	}
	if scale := o.scaleFault(text, info.ModTime()); scale != "" {
		faults = append(faults, scale)
	}
	if macPinned(o.Lane, o.Model, text) {
		return faults, nil, nil
	}
	warnings, err := carryWarnings(ctx, runner, home, repo, driveDir, o.Brief, text)
	if err != nil {
		return nil, nil, err
	}
	return faults, warnings, nil
}
