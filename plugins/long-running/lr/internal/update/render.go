package update

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
)

const (
	nothing        = "- nothing new"
	sinceTitle     = "Since you last looked"
	fyiTitle       = "Decisions made for you (FYI)"
	wordsTitle     = "Your words since the last update"
	unownedTitle   = "Unowned work"
	pinnedTitle    = "Pinned for you"
	dutiesTitle    = "Standing duties"
	noStepTitle    = "Lanes on no plan step"
	asksTitle      = "Owner asks"
	incidentsTitle = "Incidents today"
	releasesTitle  = "Releases today"
	openPRsTitle   = "Open PRs"
	gapsTitle      = "Gaps in this update"
	workstream     = "Workstream "
	firstUpdate    = "- The first update from this generator: every row below is new."
	closedToday    = "Closed today: "
	quietNow       = "Quiet over 2 h, nobody on them now: "
	updateBytes    = 40_000
	columns        = "| Row | What it delivers | State | Who is on it right now | What blocks it | What we are doing to unblock it, and by when | What went wrong or slowed it |"
	divider        = "|---|---|---|---|---|---|---|"
	subjectShare   = 0.6
	subjectWord    = 4
	whoColumn      = 3
)

var (
	deliveredKinds = []string{"report", "done", "fix-live", "handoff"}
	subjectWords   = regexp.MustCompile(`[a-z0-9]+`)
)

type Inputs struct {
	Drive     string
	Now       time.Time
	Lanes     []Lane
	Records   []Record
	All       []Record
	Waits     []Record
	Plans     []*plans.Plan
	Book      asks.Book
	Held      map[string]int64
	Incidents []Record
	Releases  []Record
	Board     []string
	Before    Snapshot
	Retro     []Retro
	Pins      []drive.Pin
}

type Need struct {
	ID      string
	Subject string
}

type Report struct {
	Text      string
	Needs     []Need
	Gaps      []string
	Published Published
	Snapshot  Snapshot
}

type section struct {
	title string
	rows  []Row
	step  bool
}

func (s section) lanes() []Row {
	switch {
	case s.step:
		return s.rows[1:]
	case s.title == noStepTitle, strings.HasPrefix(s.title, workstream):
		return s.rows
	}
	return nil
}

func (s section) lines() []string {
	if s.title == openPRsTitle {
		return prLines(s.rows)
	}
	lanes := s.lanes()
	var full []Row
	var brief, quiet []string
	for _, row := range s.rows {
		isLane := slices.Contains(lanes, row)
		switch {
		case (isLane && row.Who == nobody) || (s.title == incidentsTitle && row.Done):
			quiet = append(quiet, fmt.Sprintf("%s (%s)", row.Name, opening(row.State)))
		case !isLane, row.Blocks != nothingBlocks, row.Wrong != noneRecorded:
			full = append(full, row)
		case !s.step:
			brief = append(brief, fmt.Sprintf("- %s: %s", row.Name, cell(row.Unblock)))
		}
	}
	var lines []string
	if len(full) > 0 {
		lines = table(full)
	}
	lines = append(lines, brief...)
	if len(quiet) == 0 {
		return lines
	}
	label := quietNow
	if s.title == incidentsTitle {
		label = closedToday
	}
	return append(lines, "", label+strings.Join(quiet, "; "))
}

func prLines(rows []Row) []string {
	var states []string
	byState := map[string][]string{}
	for _, row := range rows {
		if _, seen := byState[row.State]; !seen {
			states = append(states, row.State)
		}
		words := row.Name + " " + opening(row.Delivers)
		if row.Who != nobody {
			words += " [" + row.Who + "]"
		}
		byState[row.State] = append(byState[row.State], words)
	}
	lines := make([]string, len(states))
	for i, state := range states {
		lines[i] = fmt.Sprintf("- %s (%d): %s", state, len(byState[state]), strings.Join(byState[state], "; "))
	}
	return lines
}

func unownedLines(sections []section) []string {
	var lines []string
	count := 0
	for _, found := range sections {
		var names []string
		for _, row := range found.rows {
			if row.unowned() {
				names = append(names, row.Name)
			}
		}
		if len(names) > 0 {
			count += len(names)
			lines = append(lines, fmt.Sprintf("- %s (%d): %s", found.title, len(names), strings.Join(names, ", ")))
		}
	}
	if count == 0 {
		return nil
	}
	return slices.Insert(lines, 0, fmt.Sprintf("%d open rows have nobody on them; each also sits in its own section below.", count))
}

func table(rows []Row) []string {
	if len(rows) == 0 {
		return []string{nothing}
	}
	lines := []string{columns, divider}
	for _, row := range rows {
		cells := []string{row.Name, row.Delivers, row.State, row.Who, row.Blocks, row.Unblock, row.Wrong}
		for i, text := range cells {
			cells[i] = cell(text)
		}
		cells[whoColumn] = row.Who
		lines = append(lines, "| "+strings.Join(cells, " | ")+" |")
	}
	return lines
}

func titled(title string, lines []string) []string {
	if len(lines) == 0 {
		lines = []string{nothing}
	}
	return append([]string{"", "## " + title}, lines...)
}

func namesOf(lanes []Lane) []string {
	names := make([]string, len(lanes))
	for i, lane := range lanes {
		names[i] = lane.Name
	}
	return names
}

func onWords(lane Lane, words []string) bool {
	return slices.ContainsFunc(words, func(word string) bool {
		word = strings.ToLower(word)
		return slices.ContainsFunc([]string{lane.Name, lane.Step, lane.Deliverable}, func(text string) bool {
			return strings.Contains(strings.ToLower(text), word)
		})
	})
}

func (v view) pinnedRows(pins []drive.Pin, lanes []Lane) ([]Row, []Step) {
	rows := make([]Row, len(pins))
	steps := make([]Step, len(pins))
	for i, pin := range pins {
		mine := lanesWhere(lanes, func(lane Lane) bool { return onWords(lane, pin.Words) })
		rows[i] = v.stepRow(pin.Name, pin.Delivers, &plans.Step{}, namesOf(mine))
		steps[i] = v.stepEntry(pin.Name, pinnedTitle, rows[i], mine)
	}
	return rows, steps
}

type planned struct {
	sections  []section
	folded    []section
	doneLines []string
	placed    map[string]bool
	steps     []Step
}

func (p *planned) add(v view, title, stepTitle string, row Row, lanes []Lane) {
	place(p.placed, lanes)
	p.sections = append(p.sections, section{title, slices.Insert(v.laneRows(lanes), 0, row), true})
	p.steps = append(p.steps, v.stepEntry(stepTitle, title, row, lanes))
}

func (v view) planSections(list []*plans.Plan, lanes []Lane) planned {
	out := planned{placed: map[string]bool{}}
	for _, plan := range list {
		var doneSteps []string
		for _, step := range plan.Steps {
			if step.Number == plans.Unmapped {
				for _, piece := range pieces(plan) {
					mine := lanesWhere(lanes, func(lane Lane) bool { return onPiece(lane, piece) })
					row := v.stepRow(piece, fmt.Sprintf("the %s piece of %s", piece, clip(plan.Title)), &plans.Step{}, namesOf(mine))
					out.add(v, fmt.Sprintf("%s, %s", clip(plan.Title), piece), piece, row, mine)
				}
				continue
			}
			mine := lanesWhere(lanes, func(lane Lane) bool { return onStep(lane, step) })
			title := fmt.Sprintf("%s, step %s", clip(plan.Title), clip(step.Title))
			if stepIsDone(step) && len(mine) == 0 {
				row := v.stepRow("Step "+step.Number, step.Title, step, nil)
				doneSteps = append(doneSteps, clip(step.Title))
				out.folded = append(out.folded, section{title, []Row{row}, true})
				out.steps = append(out.steps, v.stepEntry(step.Title, title, row, nil))
				continue
			}
			names := namesOf(mine)
			if owner := v.ownerOfStep(step.Number); owner != "" {
				names = append(names, owner)
			}
			out.add(v, title, step.Title, v.stepRow("Step "+step.Number, step.Title, step, names), mine)
		}
		if len(doneSteps) > 0 {
			out.doneLines = append(out.doneLines, fmt.Sprintf("- %s: %s", clip(plan.Title), strings.Join(doneSteps, "; ")))
		}
	}
	return out
}

func lanesWhere(lanes []Lane, keep func(Lane) bool) []Lane {
	var out []Lane
	for _, lane := range lanes {
		if keep(lane) {
			out = append(out, lane)
		}
	}
	return out
}

func place(placed map[string]bool, lanes []Lane) {
	for _, lane := range lanes {
		placed[lane.Name] = true
	}
}

func (v view) laneRows(lanes []Lane) []Row {
	rows := make([]Row, len(lanes))
	for i, lane := range lanes {
		rows[i] = v.laneRow(lane)
	}
	return rows
}

func Render(in Inputs) Report {
	lanes := working(in.Lanes, in.Now)
	v := newView(in.Lanes, in.Records, in.Waits, in.Now)
	v.retro = in.Retro
	pinned, pinnedSteps := v.pinnedRows(in.Pins, lanes)
	plan := v.planSections(in.Plans, lanes)
	sections := append([]section{{title: pinnedTitle, rows: pinned}, {title: dutiesTitle, rows: v.dutyRows()}}, plan.sections...)
	rest := lanesWhere(lanes, func(lane Lane) bool { return !plan.placed[lane.Name] })
	var workstreams []string
	for _, lane := range rest {
		if step := stepOf(lane); step != "" && !slices.Contains(workstreams, step) {
			workstreams = append(workstreams, step)
		}
	}
	slices.Sort(workstreams)
	for _, step := range workstreams {
		sections = append(sections, section{title: workstream + step, rows: v.laneRows(lanesWhere(rest, func(lane Lane) bool { return stepOf(lane) == step }))})
	}
	open := in.Book.Open(in.Now)
	askRows := make([]Row, len(open))
	for i, ask := range open {
		askRows[i] = v.askRow(ask)
	}
	prs := in.Book.OpenPRs()
	prRows := make([]Row, len(prs))
	for i, pr := range prs {
		prRows[i] = v.prRow(pr, in.Held)
	}
	releases := v.releaseRows(in.Releases, in.Drive)
	sections = append(sections,
		section{title: noStepTitle, rows: v.laneRows(lanesWhere(rest, func(lane Lane) bool { return stepOf(lane) == "" }))},
		section{title: asksTitle, rows: askRows},
		section{title: incidentsTitle, rows: v.incidentRows(in.Incidents, in.Drive)},
		section{title: releasesTitle, rows: releases},
		section{title: openPRsTitle, rows: prRows},
	)
	tracked := slices.Concat(sections, plan.folded)
	moved := []string{firstUpdate}
	if len(in.Before.Rows) > 0 {
		moved = sinceLines(tracked, in.Before.Rows)
	}
	feed := fyiEntries(recordsFrom(in.All, in.Now.Add(-feedWindow)))
	fresh := slices.DeleteFunc(slices.Clone(feed), func(entry FYI) bool { return !entry.At.After(in.Before.At) })
	words, gaps := ownerWords(recordsFrom(in.All, in.Before.At))
	out := []string{fmt.Sprintf("# Owner update: %s, %s", in.Drive, pacific(in.Now))}
	out = append(out, titled(fmt.Sprintf("%s (%s)", sinceTitle, pacificText(in.Before.At)), moved)...)
	out = append(out, titled(fyiTitle, fyiLines(fresh))...)
	out = append(out, titled(wordsTitle, words)...)
	out = append(out, titled(unownedTitle, unownedLines(sections))...)
	lists := v.listSections(in, lanes, plan.doneLines)
	needs := make([]Need, 0, 4+len(sections)+len(lists)+len(lanes)+len(open)+len(prs))
	needs = append(needs, Need{sinceTitle, sinceTitle}, Need{fyiTitle, fyiTitle}, Need{wordsTitle, wordsTitle}, Need{unownedTitle, unownedTitle})
	for _, found := range sections {
		out = append(out, titled(found.title, found.lines())...)
		needs = append(needs, Need{found.title, found.title})
	}
	for _, found := range lists {
		out = append(out, titled(found.title, found.lines)...)
		needs = append(needs, Need{found.title, found.title})
	}
	for _, lane := range lanes {
		needs = append(needs, Need{lane.Name, lane.Deliverable})
	}
	for _, ask := range open {
		needs = append(needs, Need{ask.Key, ask.Text})
	}
	for _, pr := range prs {
		needs = append(needs, Need{"#" + strconv.Itoa(pr.Number), pr.Title})
	}
	if size := len(strings.Join(out, "\n")); size > updateBytes {
		gaps = append(gaps, fmt.Sprintf("this update is %d KB, over the %d KB an update may run", size/1000, updateBytes/1000))
	}
	if len(gaps) > 0 {
		bulleted := make([]string, len(gaps))
		for i, gap := range gaps {
			bulleted[i] = "- " + gap
		}
		out = append(out, titled(gapsTitle, bulleted)...)
	}
	return Report{
		Text:      strings.Join(out, "\n") + "\n",
		Needs:     needs,
		Gaps:      gaps,
		Published: Published{Updated: in.Now, Steps: numbered(slices.Concat(pinnedSteps, plan.steps)), FYI: feed, Now: nowOf(releases)},
		Snapshot:  takeSnapshot(tracked, in.Now),
	}
}

type listSection struct {
	title string
	lines []string
}

func (v view) listSections(in Inputs, lanes []Lane, doneLines []string) []listSection {
	var quiet []string
	named := namesOf(lanes)
	for _, lane := range in.Lanes {
		if lane.State != retired && lane.State != unregistered && !slices.Contains(named, lane.Name) {
			quiet = append(quiet, lane.Name)
		}
	}
	slices.Sort(quiet)
	var quietLines []string
	if len(quiet) > 0 {
		quietLines = []string{"- " + strings.Join(quiet, ", ")}
	}
	waiting := make([]string, len(in.Waits))
	for i, wait := range in.Waits {
		waiting[i] = recordLine(wait)
	}
	board := make([]string, len(in.Board))
	for i, line := range in.Board {
		board[i] = "- " + line
	}
	newest := map[string]Record{}
	var delivered []string
	for _, record := range v.records {
		if slices.Contains(deliveredKinds, record.Kind) && in.Now.Sub(record.At) < heardWithin {
			if _, seen := newest[record.Lane]; !seen {
				delivered = append(delivered, record.Lane)
			}
			newest[record.Lane] = record
		}
	}
	deliveredLines := make([]string, len(delivered))
	for i, lane := range delivered {
		deliveredLines[i] = recordLine(newest[lane])
	}
	return []listSection{
		{fmt.Sprintf("Registry lanes quiet over %d h", int(quietAfter.Hours())), quietLines},
		{"Waiting on root", waiting},
		{"Board picks", board},
		{fmt.Sprintf("Delivered in the last %d h", int(heardWithin.Hours())), deliveredLines},
		{"Done plan steps", doneLines},
	}
}

func recordLine(record Record) string {
	return fmt.Sprintf("- %s #%d %s %s: %s", record.Kind, record.Seq, record.Lane, pacific(record.At), clip(record.Text))
}

func Missing(text string, needs []Need) []string {
	var left []string
	seen := map[string]bool{}
	folded := strings.ToLower(text)
	words := map[string]bool{}
	for _, word := range subjectWords.FindAllString(folded, -1) {
		words[word] = true
	}
	for _, need := range needs {
		if seen[need.ID] {
			continue
		}
		seen[need.ID] = true
		if !namesWhole(text, need.ID) && !coversSubject(words, need.Subject) {
			left = append(left, need.ID)
		}
	}
	return left
}

func namesWhole(text, id string) bool {
	return regexp.MustCompile(`(?:^|[^\w-])` + regexp.QuoteMeta(id) + `(?:$|[^\w-])`).MatchString(text)
}

func coversSubject(words map[string]bool, subject string) bool {
	total, hit := 0, 0
	for _, word := range subjectWords.FindAllString(strings.ToLower(subject), -1) {
		if len(word) < subjectWord {
			continue
		}
		total++
		if words[word] {
			hit++
		}
	}
	return total > 0 && float64(hit) >= subjectShare*float64(total)
}
