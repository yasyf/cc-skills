package update

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
)

const (
	nothing      = "- nothing new"
	unownedTitle = "Unowned work"
	openPRsTitle = "Open PRs"
	columns      = "| Row | What it delivers | State | Who is on it right now | What blocks it | What we are doing to unblock it, and by when | What went wrong or slowed it |"
	divider      = "|---|---|---|---|---|---|---|"
	subjectShare = 0.6
	subjectWord  = 4
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
	Waits     []Record
	Plans     []*plans.Plan
	Book      asks.Book
	Held      map[string]int64
	Incidents []Record
	Releases  []Record
	Board     []string
}

type Need struct {
	ID      string
	Subject string
}

type Report struct {
	Text  string
	Needs []Need
}

type section struct {
	title string
	rows  []Row
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

func (v view) planSections(list []*plans.Plan, lanes []Lane) (sections []section, doneLines []string, placed map[string]bool) {
	placed = map[string]bool{}
	for _, plan := range list {
		var doneSteps []string
		for _, step := range plan.Steps {
			if step.Number == plans.Unmapped {
				for _, piece := range pieces(plan) {
					mine := lanesWhere(lanes, func(lane Lane) bool { return onPiece(lane, piece) })
					place(placed, mine)
					row := v.stepRow(piece, fmt.Sprintf("the %s piece of %s", piece, clip(plan.Title)), &plans.Step{}, namesOf(mine))
					sections = append(sections, section{fmt.Sprintf("%s, %s", clip(plan.Title), piece), slices.Insert(v.laneRows(mine), 0, row)})
				}
				continue
			}
			mine := lanesWhere(lanes, func(lane Lane) bool { return onStep(lane, step) })
			if stepIsDone(step) && len(mine) == 0 {
				doneSteps = append(doneSteps, clip(step.Title))
				continue
			}
			place(placed, mine)
			names := namesOf(mine)
			if owner := v.ownerOfStep(step.Number); owner != "" {
				names = append(names, owner)
			}
			row := v.stepRow("Step "+step.Number, step.Title, step, names)
			sections = append(sections, section{fmt.Sprintf("%s, step %s", clip(plan.Title), clip(step.Title)), slices.Insert(v.laneRows(mine), 0, row)})
		}
		if len(doneSteps) > 0 {
			doneLines = append(doneLines, fmt.Sprintf("- %s: %s", clip(plan.Title), strings.Join(doneSteps, "; ")))
		}
	}
	return sections, doneLines, placed
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
	sections, doneLines, placed := v.planSections(in.Plans, lanes)
	sections = append([]section{{"Standing duties", v.dutyRows()}}, sections...)
	rest := lanesWhere(lanes, func(lane Lane) bool { return !placed[lane.Name] })
	var workstreams []string
	for _, lane := range rest {
		if step := stepOf(lane); step != "" && !slices.Contains(workstreams, step) {
			workstreams = append(workstreams, step)
		}
	}
	slices.Sort(workstreams)
	for _, step := range workstreams {
		sections = append(sections, section{"Workstream " + step, v.laneRows(lanesWhere(rest, func(lane Lane) bool { return stepOf(lane) == step }))})
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
	sections = append(sections,
		section{"Lanes on no plan step", v.laneRows(lanesWhere(rest, func(lane Lane) bool { return stepOf(lane) == "" }))},
		section{"Owner asks", askRows},
		section{"Incidents today", v.incidentRows(in.Incidents, in.Drive)},
		section{"Releases today", v.releaseRows(in.Releases, in.Drive)},
		section{openPRsTitle, prRows},
	)
	var unowned []Row
	for _, found := range sections {
		for _, row := range found.rows {
			if row.unowned() {
				unowned = append(unowned, row)
			}
		}
	}
	var head []string
	if len(unowned) > 0 {
		head = append([]string{fmt.Sprintf("%d rows have nobody on them; each also sits in its own section below.", len(unowned)), ""}, table(unowned)...)
	}
	lists := v.listSections(in, lanes, doneLines)
	out := append([]string{fmt.Sprintf("# Owner update: %s, %s", in.Drive, pacific(in.Now))}, titled(unownedTitle, head)...)
	needs := make([]Need, 0, 1+len(sections)+len(lists)+len(lanes)+len(open)+len(prs))
	needs = append(needs, Need{unownedTitle, unownedTitle})
	for _, found := range sections {
		out = append(out, titled(found.title, table(found.rows))...)
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
	return Report{strings.Join(out, "\n") + "\n", needs}
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
