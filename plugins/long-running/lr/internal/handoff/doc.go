package handoff

import (
	"fmt"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
)

const (
	findingsHeading  = "## Lint findings"
	deliveredHeading = "## Delivered since the last progress record"
	generatedMark    = "(generated)"
	generatedLine    = "Generated from sources by the long-running compaction hook at "
	generatedStem    = "-generated"
	readFirstPrefix  = "- Hand-written handoff, read it in full before `" + narrativeHeading + "`: "
	carriedSuffix    = ", carried forward"
	catchUp          = "Catch up with `cci digest --drive %[1]s`, then `cci tail --drive %[1]s`; never tail, sed, or grep a whole inbox."
	running          = "running"
	inProgress       = "in_progress"
	headerLines      = 3
	taskChars        = 120
	rulingChars      = 400
	retiredChars     = 200
	shownLimit       = 10
	incidentWindow   = 6 * time.Hour
	asksBudget       = 6_000
	registryBudget   = 12_000
	updateBudget     = 8_000
	driveEnv         = "CLAUDE_LONG_RUNNING_DRIVE"
	secondsStamp     = "2006-01-02T15:04:05Z"
)

var (
	generationStamp = regexp.MustCompile(`(?m)^` + regexp.QuoteMeta(generatedLine) + `(\S+?)\.`)
	provenance      = regexp.MustCompile(`^_From (.+?)(?:, carried forward)?\._\n\n`)
	narrativeMarker = "\n" + narrativeHeading + "\n"
)

type inbox struct {
	name   string
	head   string
	cursor string
	last   string
	tail   string
	count  int
}

func (i inbox) readThrough() bool {
	if i.cursor == "" {
		return false
	}
	if position, err := strconv.Atoi(i.cursor); err == nil {
		return position >= i.count
	}
	return i.cursor == i.tail
}

type progressRecord struct {
	source string
	ref    string
	title  string
	body   string
	edit   string
}

type readFirst struct {
	ref   string
	title string
}

type sessionTask struct {
	ID      string `json:"id"`
	Status  string `json:"status"`
	Subject string `json:"subject"`
	Owner   string `json:"owner"`
}

type backgroundTask struct {
	Type        string `json:"type"`
	Status      string `json:"status"`
	Description string `json:"description"`
}

type session struct {
	SessionID  string           `json:"session_id"`
	Tasks      []sessionTask    `json:"tasks"`
	Background []backgroundTask `json:"background"`
}

type driveEntry struct {
	Drive     string   `json:"drive"`
	Ledger    string   `json:"ledger"`
	Checkout  string   `json:"checkout"`
	OrcaRun   string   `json:"orca_run"`
	StateDir  string   `json:"state_dir"`
	StartedAt string   `json:"started_at"`
	Program   string   `json:"program"`
	CCIDrive  string   `json:"cci_drive"`
	Sessions  []string `json:"sessions"`
}

func (d driveEntry) programName() string {
	if d.Program != "" {
		return d.Program
	}
	return filepath.Base(d.StateDir)
}

func (d driveEntry) cciName() string {
	if d.CCIDrive != "" {
		return d.CCIDrive
	}
	return d.programName()
}

func (d *progressDoc) busName() string {
	if d.registry != nil {
		return d.registry.cciName()
	}
	return d.program
}

type progressDoc struct {
	program       string
	plan          string
	at            time.Time
	registry      *driveEntry
	reg           *register
	rows          *rowSet
	standing      standingRules
	retired       []string
	asks          []string
	tasks         []sessionTask
	planProgress  []string
	lanes         []backgroundTask
	monitors      []backgroundTask
	incidentLanes []string
	laneRegistry  []string
	aliases       lanes.Aliases
	registered    int
	ownerUpdate   string
	delivered     []string
	inboxes       []inbox
	inboxDir      string
	findings      []string
	planFindings  []string
	narrative     string
	narrativeFrom string
	records       []progressRecord
	readFirst     []readFirst
	record        string
	generated     string
	retitle       bool
	fresh         bool
	stale         []string
	cleared       []string
}

func (d *progressDoc) stamp() string {
	return d.at.UTC().Format("2006-01-02T1504Z")
}

func (d *progressDoc) title() string {
	return fmt.Sprintf("%s: progress %s %s", d.program, d.stamp(), generatedMark)
}

func clip(text string, limit int) string {
	return plans.Clip(text, limit)
}

func cut(id string, length int) string {
	return id[:min(length, len(id))]
}

func narrativeOf(body string) string {
	_, rest, found := strings.Cut(body, narrativeMarker)
	if !found {
		return strings.TrimSpace(body)
	}
	return provenance.ReplaceAllString(strings.TrimSpace(rest), "")
}

func carriedFrom(body, source string) string {
	if _, rest, found := strings.Cut(body, narrativeMarker); found {
		if match := provenance.FindStringSubmatch(strings.TrimSpace(rest)); match != nil {
			source = match[1]
		}
	}
	return source + carriedSuffix
}

func isGenerated(body string) bool {
	lines := splitLines(body)
	return slices.ContainsFunc(lines[:min(headerLines, len(lines))], func(line string) bool { return strings.HasPrefix(line, generatedLine) })
}

func readFirstOf(body string) []readFirst {
	head, _, _ := strings.Cut(body, narrativeMarker)
	found := []readFirst{}
	for _, line := range splitLines(head) {
		rest, ok := strings.CutPrefix(line, readFirstPrefix)
		if !ok {
			continue
		}
		parts := strings.SplitN(rest, "`", 3)
		found = append(found, readFirst{parts[1], strings.TrimPrefix(parts[2], ": ")})
	}
	return found
}

func (d *progressDoc) narrate(records []progressRecord, previous, previousSource string) {
	d.records = records
	switch {
	case len(records) > 0:
		last := records[len(records)-1]
		d.narrative, d.narrativeFrom = narrativeOf(last.body), last.source
		for _, record := range records[:len(records)-1] {
			d.readFirst = append(d.readFirst, readFirst{record.ref, record.title})
		}
	case previous != "":
		d.narrative, d.narrativeFrom = narrativeOf(previous), carriedFrom(previous, previousSource)
		d.readFirst = readFirstOf(previous)
	}
}

func (d *progressDoc) narrativeSource() *string {
	if d.narrative == "" {
		return nil
	}
	_, source, _ := strings.Cut(strings.TrimSuffix(d.narrativeFrom, carriedSuffix), " ")
	return &source
}

func shown(lines []string, more string) []string {
	if len(lines) <= shownLimit {
		return lines
	}
	return append(slices.Clone(lines[:shownLimit]), fmt.Sprintf("- %d more %s", len(lines)-shownLimit, more))
}

func bullets(lines []string) []string {
	out := make([]string, 0, len(lines))
	for _, line := range lines {
		out = append(out, "- "+line)
	}
	return out
}

func (d *progressDoc) ruleLines() []string {
	lines := make([]string, 0, len(d.standing.rules)+len(d.retired))
	for _, rule := range d.standing.rules {
		lines = append(lines, fmt.Sprintf("- %s [%s]", rule.id, rule.source))
	}
	return append(lines, bullets(d.retired)...)
}

func (d *progressDoc) standingSection() string {
	return sectionOf(d.reg, d.ruleLines(), d.narrative, d.rows)
}

func orDefault(lines []string, fallback string) []string {
	if len(lines) == 0 {
		return []string{fallback}
	}
	return lines
}

func (d *progressDoc) taskLines() []string {
	out := []string{}
	for _, task := range d.tasks {
		if task.Status == inProgress {
			out = append(out, fmt.Sprintf("- #%s %s", task.ID, clip(task.Subject, taskChars)))
		}
	}
	active := len(out)
	out = orDefault(out, "- none in progress")
	if pending := len(d.tasks) - active; pending > 0 {
		out = append(out, fmt.Sprintf("- %d pending in `TaskList`", pending))
	}
	return append(out, d.planProgress...)
}

func (d *progressDoc) runningLines() []string {
	newestLanes := slices.Clone(d.lanes)
	slices.Reverse(newestLanes)
	lines := []string{}
	for _, task := range slices.Concat(d.monitors, newestLanes) {
		if task.Status == running {
			lines = append(lines, fmt.Sprintf("- %s: %s", task.Type, clip(task.Description, taskChars)))
		}
	}
	return orDefault(shown(lines, "running lanes"), "- none running at the last stop")
}

func (d *progressDoc) ownerUpdateLines() []string {
	if d.ownerUpdate == "" {
		return []string{"- no lane registry"}
	}
	lines := splitLines(d.ownerUpdate)
	out := make([]string, 0, len(lines))
	for _, line := range lines[1:] {
		if strings.HasPrefix(line, "## ") {
			line = "#" + line
		}
		out = append(out, line)
	}
	return out
}

func (d *progressDoc) inboxLines() []string {
	out := []string{}
	if d.inboxDir != "" {
		out = append(out, fmt.Sprintf(catchUp, filepath.Base(filepath.Dir(d.inboxDir))), "")
	}
	lines := make([]string, 0, len(d.inboxes))
	for _, item := range d.inboxes {
		line := fmt.Sprintf("- %s: head %s, cursor %s", item.name, dash(item.head), dash(item.cursor))
		if item.last != "" {
			line += ": " + clip(item.last, taskChars)
		}
		lines = append(lines, line)
	}
	out = append(out, orDefault(shown(lines, "inboxes"), "- no inbox directory")...)
	for _, cleared := range d.cleared {
		out = append(out, "- cleared stale cursor "+cleared)
	}
	return out
}

func dash(text string) string {
	if text == "" {
		return "-"
	}
	return text
}

func (d *progressDoc) render() string {
	out := []string{
		"# " + d.title(),
		"",
		fmt.Sprintf("%s%s. Every section above `%s` is rebuilt at each handoff; change the sources, never this doc.", generatedLine, d.at.UTC().Format(secondsStamp), narrativeHeading),
		"",
		d.standingSection(),
		"## Read first",
		"- Plan: `" + d.plan + "`",
	}
	for _, first := range d.readFirst {
		out = append(out, fmt.Sprintf("%s`%s`: %s", readFirstPrefix, first.ref, first.title))
	}
	if d.registry != nil {
		sessions := make([]string, 0, len(d.registry.Sessions))
		for _, id := range d.registry.Sessions {
			sessions = append(sessions, cut(id, 8))
		}
		out = append(out, fmt.Sprintf("- Drive `%s`: ledger `%s`, Orca run `%s`, checkout `%s`, coordinator sessions %s",
			d.registry.Drive, cut(d.registry.Ledger, shortID), dash(d.registry.OrcaRun), d.registry.Checkout, strings.Join(sessions, ", ")))
	}
	out = append(out, "", "## Open owner asks")
	out = append(out, withinBudget(noneIfEmpty(bullets(d.asks)), asksBudget, "ledger.py show --drive "+d.busName()+" --asks")...)
	out = append(out, "", "## Open tasks")
	out = append(out, d.aliases.AllIn(d.taskLines())...)
	out = append(out, "", deliveredHeading)
	out = append(out, d.aliases.AllIn(noneIfEmpty(bullets(d.delivered)))...)
	out = append(out, "", "## Lanes and monitors")
	out = append(out, d.aliases.AllIn(d.runningLines())...)
	out = append(out, "", "## Lane registry")
	out = append(out, d.aliases.AllIn(orDefault(d.laneRegistry, "- no lane registry"))...)
	out = append(out, "", "## Owner update")
	out = append(out, d.aliases.AllIn(d.ownerUpdateLines())...)
	out = append(out, "", "## Incident lanes")
	out = append(out, d.aliases.AllIn(orDefault(d.incidentLanes, fmt.Sprintf("- none posted in the last %d h", int(incidentWindow.Hours()))))...)
	out = append(out, "", "## Inboxes", "")
	out = append(out, d.inboxLines()...)
	out = append(out, "", findingsHeading)
	out = append(out, noneIfEmpty(bullets(slices.Concat(d.findings, d.planFindings)))...)
	out = append(out, "", narrativeHeading, "")
	if d.narrative == "" {
		out = append(out, "_The coordinator has written no narrative yet._")
	} else {
		out = append(out, fmt.Sprintf("_From %s._\n\n%s", d.narrativeFrom, d.narrative))
	}
	return strings.Join(out, "\n") + "\n"
}

func (d *progressDoc) retire(previous string) {
	now := carried(d.ruleLines())
	before, _ := section(previous)
	for _, rule := range carried(before) {
		if holdsRule(now, rule.id) || strings.Contains(rule.line, "superseded by") {
			continue
		}
		successor, ok := d.standing.superseded[rule.id]
		if !ok {
			successor = fmt.Sprintf("nothing: the sources dropped it after the previous handoff (was: %s)", clip(strings.TrimLeft(rule.line, "-* "), retiredChars))
		}
		d.retired = append(d.retired, rule.id+" superseded by "+successor)
	}
}

func (d *progressDoc) uncitedRules(live []string) []string {
	var lines []string
	for _, rule := range d.standing.rules {
		if gated(rule.text, live) {
			lines = append(lines, fmt.Sprintf(
				"%s: standing rule %s requires owner approval but cites no live answer id: %s; post a replacement rule with `cci post --drive %s --lane root --kind correction --re-seq %s --topic standing --ccn <answer id> --text \"<rule>\"`",
				rule.source, rule.id, clip(rule.text, rulingChars), d.program, strings.TrimLeft(rule.id, "#"),
			))
		}
	}
	return lines
}

func (d *progressDoc) uncitedNarrative(live []string) []string {
	edit := "in your next progress record"
	if len(d.records) > 0 {
		edit = d.records[len(d.records)-1].edit
	}
	var lines []string
	for at, line := range splitLines(d.narrative) {
		if gated(line, live) {
			lines = append(lines, fmt.Sprintf("narrative (%s) line %d: owner-gate line cites no live answer id: %s; end that line with `(answer <id>)` %s", d.narrativeFrom, at+1, clip(line, rulingChars), edit))
		}
	}
	return lines
}

func lintView(body string) string {
	if !strings.Contains(body, narrativeMarker) {
		return body
	}
	rules, _ := section(body)
	return fmt.Sprintf("%s\n%s\n\n%s\n\n%s\n", registerHeading, strings.Join(rules, "\n"), narrativeHeading, narrativeOf(body))
}

func (d *progressDoc) reading(doc string) string {
	parts := make([]string, 0, len(d.readFirst)+1)
	for _, first := range d.readFirst {
		parts = append(parts, fmt.Sprintf("the hand-written handoff `%s`", first.ref))
	}
	return strings.Join(append(parts, doc), ", then ")
}

func (d *progressDoc) digest(doc string) string {
	first := "read " + doc
	if d.reg != nil {
		first = fmt.Sprintf("read the standing rules register `ccn doc show %s`: it arrives verbatim with your next tool result, binds every lane brief, and outranks the summary. Then read %s", cut(d.reg.id, shortID), doc)
	}
	return strings.Join([]string{
		fmt.Sprintf("Compacted long-running drive `%s`. Before acting, %s, then `%s`. Reload Skill `long-running` if its rules are gone.", d.program, first, d.plan),
		fmt.Sprintf("Register: %d owner-approved rules, %d live standing rules.", ruleCount(d.reg), len(d.standing.rules)),
		fmt.Sprintf("Open: %d owner asks, %d tasks, %d lanes, %d monitors, %d lint findings.", len(d.asks), len(d.tasks), max(len(d.lanes), d.registered), len(d.monitors), len(d.findings)+len(d.planFindings)),
	}, "\n")
}
