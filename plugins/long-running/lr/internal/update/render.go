package update

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
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
	reapTitle      = "Session reap"
	reapPasses     = 6
	newestOrders   = 10
	unfinishedKind = "unfinished"
	workstream     = "Workstream "
	workstreams    = "Lanes by workstream"
	firstUpdate    = "- The first update from this generator: every row below is new."
	closedToday    = "Closed today: "
	quietNow       = "Quiet over 2 h, nobody on them now: "
	updateBytes    = 40_000
	columns        = "| Row | What it delivers | State | Who is on it right now | What blocks it | What we are doing to unblock it, and by when | What went wrong or slowed it |"
	divider        = "|---|---|---|---|---|---|---|"
	subjectShare   = 0.6
	subjectWord    = 4
	whoColumn      = 3
	portStall      = time.Hour
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
	PRRows    []store.ReviewRow
	Held      map[string]int64
	Incidents []Record
	Releases  []Record
	Cards     []board.Wait
	Unboarded error
	TapQueue  string
	Before    Snapshot
	Retro     []Retro
	Owed      []deletions.Owed
	Pins      []drive.Pin
	Programs  []Program
	Streams   []Workstream
	Unread    error
	Rulings   []PlanRuling
	Overdue   []store.Item
	Orders    []owner.Row
	Leading   []Record
	Released  map[int]string
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
	Programs  []Program
}

type section struct {
	title string
	rows  []Row
	step  bool
	group string
	piece bool
}

func (s section) key() string {
	if s.group == "" {
		return s.title
	}
	return s.group + ", " + s.title
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

func (s section) lines(f folding) []string {
	if s.title == openPRsTitle {
		return prLines(s.rows, f.at(foldPRs))
	}
	lanes := s.lanes()
	var full []Row
	var brief, quiet, still, onIt, blocked []string
	for _, row := range s.rows {
		isLane := slices.Contains(lanes, row)
		switch {
		case isLane && row.Who == nobody && f.at(foldQuiet):
			quiet = append(quiet, row.Name)
		case (isLane && row.Who == nobody) || (s.title == incidentsTitle && row.Done):
			quiet = append(quiet, fmt.Sprintf("%s (%s)", row.Name, opening(row.State)))
		case f.folds(s, row, isLane):
			still = append(still, row.Name)
		case isLane && f.at(foldBlocked) && row.Blocks != nothingBlocks:
			blocked = append(blocked, row.Name)
		case isLane && f.at(foldLanes) && row.Blocks != nothingBlocks:
			brief = append(brief, fmt.Sprintf("- %s: %s", row.Name, cell(row.Blocks)))
		case isLane && f.at(foldLanes):
			if !s.step {
				onIt = append(onIt, row.Name)
			}
		case s.title == dutiesTitle && f.at(foldDuties):
			brief = append(brief, rowLine(row.Name, row))
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
	if len(blocked) > 0 {
		lines = append(lines, blockedNow+strings.Join(blocked, ", "))
	}
	if len(onIt) > 0 {
		lines = append(lines, onItNow+strings.Join(onIt, ", "))
	}
	if len(still) > 0 {
		lines = append(lines, fmt.Sprintf(stillSince, f.since, strings.Join(still, ", ")))
	}
	if len(quiet) == 0 {
		return lines
	}
	label := quietNow
	if s.title == incidentsTitle {
		label = closedToday
	}
	return append(lines, "", label+strings.Join(quiet, "; "))
}

func prLines(rows []Row, numbersOnly bool) []string {
	var states []string
	byState := map[string][]string{}
	for _, row := range rows {
		if _, seen := byState[row.State]; !seen {
			states = append(states, row.State)
		}
		words := row.Name
		if !numbersOnly {
			words += " " + opening(row.Delivers)
			if row.Who != nobody {
				words += " [" + row.Who + "]"
			}
		}
		byState[row.State] = append(byState[row.State], words)
	}
	lines := make([]string, len(states))
	for i, state := range states {
		lines[i] = fmt.Sprintf("- %s (%d): %s", state, len(byState[state]), strings.Join(byState[state], "; "))
	}
	return lines
}

func unownedLines(sections []section, f folding) []string {
	var lines []string
	count, foldedLanes := 0, 0
	for _, found := range sections {
		lanes := found.lanes()
		var names []string
		for _, row := range found.rows {
			switch {
			case !row.unowned():
			case f.at(foldUnowned) && slices.Contains(lanes, row):
				foldedLanes++
			default:
				names = append(names, row.Name)
			}
		}
		if len(names) > 0 {
			count += len(names)
			lines = append(lines, fmt.Sprintf("- %s (%d): %s", found.title, len(names), strings.Join(names, ", ")))
		}
	}
	count += foldedLanes
	if count == 0 {
		return nil
	}
	if foldedLanes > 0 {
		lines = append(lines, fmt.Sprintf("- %d lanes, named in their own sections", foldedLanes))
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

func (v view) pinnedRows(in Inputs, lanes []Lane) ([]Row, []Step) {
	rows := make([]Row, len(in.Pins))
	steps := make([]Step, len(in.Pins))
	for i, pin := range in.Pins {
		mine := lanesWhere(lanes, func(lane Lane) bool { return onWords(lane, pin.Words) })
		rows[i] = v.stepRow(pin.Name, pin.Delivers, &plans.Step{}, namesOf(mine))
		if line := deliveryLine(pin.PRs, in.Book, in.Released); line != "" {
			rows[i].State = line
		}
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

func (p *planned) add(v view, found section, row Row, lanes []Lane) {
	place(p.placed, lanes)
	found.rows = slices.Insert(v.laneRows(lanes), 0, row)
	p.sections = append(p.sections, found)
	p.steps = append(p.steps, v.stepEntry(found.title, found.key(), row, lanes))
}

func (v view) planSections(list []*plans.Plan, lanes []Lane) planned {
	out := planned{placed: map[string]bool{}}
	for _, plan := range list {
		group := plainTitle(v.rulings.Replace(plan.Title))
		var doneSteps []string
		for _, step := range plan.Steps {
			if step.Number == plans.Unmapped {
				for _, piece := range pieces(plan) {
					mine := lanesWhere(lanes, func(lane Lane) bool { return onPiece(lane, piece) })
					row := v.stepRow(piece, pieceDelivers(group, piece), &plans.Step{}, namesOf(mine))
					out.add(v, section{title: piece, step: true, group: group, piece: true}, row, mine)
				}
				continue
			}
			mine := lanesWhere(lanes, func(lane Lane) bool { return onStep(lane, step) })
			name := "Step " + step.Number
			title := orWord(plainTitle(v.rulings.Replace(step.Title)), name)
			if stepIsDone(step) && len(mine) == 0 {
				row := v.stepRow(name, title, step, nil)
				doneSteps = append(doneSteps, title)
				found := section{title: title, rows: []Row{row}, step: true, group: group}
				out.folded = append(out.folded, found)
				out.steps = append(out.steps, v.stepEntry(title, found.key(), row, nil))
				continue
			}
			out.add(v, section{title: title, step: true, group: group}, v.stepRow(name, title, step, namesOf(mine)), mine)
		}
		if len(doneSteps) > 0 {
			out.doneLines = append(out.doneLines, fmt.Sprintf("- %s: %s", group, strings.Join(doneSteps, "; ")))
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

type draft struct {
	header     string
	lead       []Row
	steps      []string
	overdue    []string
	orders     []string
	sinceTitle string
	since      string
	fyi        []FYI
	words      []string
	sections   []section
	lists      []listSection
	tracked    []section
	before     map[string][]string
	programs   []Program
	streams    []string
	reap       []string
}

func Render(in Inputs) Report {
	lanes := working(in.Lanes, in.Now)
	v := newView(in.Lanes, in.Records, in.Waits, in.Now)
	v.retro = in.Retro
	v.rulings = rulingsReplacer(in.Rulings)
	v.history = bySeq(slices.Concat(in.Records, in.All))
	pinned, pinnedSteps := v.pinnedRows(in, lanes)
	plan := v.planSections(in.Plans, lanesWhere(lanes, func(lane Lane) bool { return lane.Standing == "" }))
	sections := append([]section{{title: pinnedTitle, rows: pinned}, {title: dutiesTitle, rows: v.dutyRows()}}, plan.sections...)
	rest := lanesWhere(lanes, func(lane Lane) bool { return !plan.placed[lane.Name] })
	var named []string
	for _, lane := range rest {
		if name := workstreamOf(lane); name != "" && !slices.Contains(named, name) {
			named = append(named, name)
		}
	}
	slices.Sort(named)
	for _, name := range named {
		sections = append(sections, section{title: workstream + name, rows: v.laneRows(lanesWhere(rest, func(lane Lane) bool { return workstreamOf(lane) == name })), group: workstreams})
	}
	open := in.Book.Open(in.Now)
	askRows := make([]Row, len(open))
	for i, ask := range open {
		askRows[i] = v.askRow(ask)
	}
	prs := openPRRows(in.PRRows)
	prRows := make([]Row, len(prs))
	for i, pr := range prs {
		prRows[i] = v.prRow(pr, in.Held)
	}
	releases := v.releaseRows(in.Releases, in.Drive)
	sections = append(sections,
		section{title: noStepTitle, rows: v.laneRows(lanesWhere(rest, func(lane Lane) bool { return workstreamOf(lane) == "" }))},
		section{title: asksTitle, rows: askRows},
		section{title: incidentsTitle, rows: v.incidentRows(in.Incidents, in.Drive)},
		section{title: releasesTitle, rows: releases},
		section{title: openPRsTitle, rows: prRows},
	)
	feed := fyiEntries(recordsFrom(in.All, in.Now.Add(-feedWindow)))
	fresh := slices.DeleteFunc(slices.Clone(feed), func(entry FYI) bool { return !entry.At.After(in.Before.At) })
	words, gaps := ownerWords(recordsFrom(in.All, in.Before.At))
	lists := v.listSections(in, lanes, plan.doneLines)
	lead := leadRows(in)
	unownedSteps := unownedStepLines(in.Plans, in.Lanes)
	streams, streamGaps := v.workstreamLines(in, lanes)
	found := draft{
		header:     fmt.Sprintf("# Owner update: %s, %s", in.Drive, pacific(in.Now)),
		lead:       lead,
		steps:      unownedSteps,
		overdue:    slices.Concat(overdueLines(in.Overdue), stalledPortLines(in.PRRows, in.Now)),
		orders:     orderLines(in.Orders),
		sinceTitle: fmt.Sprintf("%s (%s)", sinceTitle, pacificText(in.Before.At)),
		since:      pacificText(in.Before.At),
		fyi:        fresh,
		words:      words,
		sections:   sections,
		lists:      lists,
		tracked:    slices.Concat(sections, plan.folded),
		before:     in.Before.Rows,
		programs:   in.Programs,
		streams:    streams,
		reap:       reapLines(recordsFrom(in.All, in.Now.Add(-quietAfter))),
	}
	needs := make([]Need, 0, 6+len(in.Streams)+len(sections)+len(lists)+len(lanes)+len(open)+len(prs))
	needs = append(needs, Need{workstreamsTitle, workstreamsTitle}, Need{sinceTitle, sinceTitle}, Need{fyiTitle, fyiTitle}, Need{wordsTitle, wordsTitle}, Need{unownedTitle, unownedTitle}, Need{toolingTitle, toolingTitle})
	for _, each := range in.Streams {
		needs = append(needs, Need{each.Name, each.Name})
	}
	if len(lead) > 0 {
		needs = append(needs, Need{leadTitle, leadTitle})
	}
	if len(unownedSteps) > 0 {
		needs = append(needs, Need{unownedStepsTitle, unownedStepsTitle})
	}
	for _, row := range lead {
		needs = append(needs, Need{row.Name, row.Delivers})
	}
	if len(found.reap) > 0 {
		needs = append(needs, Need{reapTitle, reapTitle})
	}
	for _, each := range sections {
		needs = append(needs, Need{each.title, each.title})
	}
	for _, each := range lists {
		needs = append(needs, Need{each.title, each.title})
	}
	for _, lane := range lanes {
		needs = append(needs, Need{lane.Name, lane.Deliverable})
	}
	for _, ask := range open {
		needs = append(needs, Need{ask.Key, clipTo(ask.Text, cellChars)})
	}
	for _, pr := range prs {
		needs = append(needs, Need{"#" + strconv.Itoa(pr.Number), pr.Title})
	}
	whole, _ := found.lines(folding{before: found.before, since: found.since})
	gaps = slices.Concat(gaps, streamGaps, ToolingLeft(strings.Join(whole, "\n"), in.Programs))
	text, dropped := found.fit(gaps)
	if dropped {
		left := Missing(text, needs)
		needs = slices.DeleteFunc(needs, func(need Need) bool { return slices.Contains(left, need.ID) })
	}
	return Report{
		Text:      text,
		Needs:     needs,
		Gaps:      gaps,
		Published: Published{Updated: in.Now, Steps: numbered(slices.Concat(pinnedSteps, plan.steps)), FYI: feed, Now: nowOf(releases)},
		Snapshot:  takeSnapshot(found.tracked, in.Now),
		Programs:  in.Programs,
	}
}

func reapLines(records []Record) []string {
	var lines []string
	for _, record := range records {
		if record.Topic == lanes.ReapTopic {
			lines = append(lines, fmt.Sprintf("- %s: %s", pacific(record.At), record.Text))
		}
	}
	return lines[max(0, len(lines)-reapPasses):]
}

func stalledPortLines(rows []store.ReviewRow, now time.Time) []string {
	var lines []string
	for _, row := range openPRRows(rows) {
		moved := dueOf(row.MovedAt)
		if strings.HasPrefix(row.Port, prrow.PortOf) && now.Sub(moved) >= portStall {
			lines = append(lines, fmt.Sprintf("- #%d, %s, has had no new head, grade or queue move since %s; it waits on %s", row.Number, row.Port, pacific(moved), row.WaitsOn))
		}
	}
	if len(lines) == 0 {
		return nil
	}
	return append([]string{"", fmt.Sprintf("Forward ports with no move in an hour (%d):", len(lines))}, lines...)
}

func overdueLines(items []store.Item) []string {
	if len(items) == 0 {
		return nil
	}
	lines := []string{"", fmt.Sprintf("%d overdue:", len(items))}
	ledgerRows := map[string]int{}
	for _, item := range items {
		if item.Kind == unfinishedKind {
			ledgerRows[ledgerOf(item)]++
		}
	}
	for _, item := range items {
		if item.Kind != unfinishedKind {
			subject := item.Head
			if item.Number != 0 {
				subject = fmt.Sprintf("#%d", item.Number)
			}
			lines = append(lines, fmt.Sprintf("- %s %s, due %s", item.Kind, subject, pacific(item.Deadline)))
			continue
		}
		if rows := ledgerRows[ledgerOf(item)]; rows > 0 {
			lines = append(lines, fmt.Sprintf("- unfinished %s rows: %d, oldest due %s", ledgerOf(item), rows, pacific(item.Deadline)))
			ledgerRows[ledgerOf(item)] = 0
		}
	}
	return lines
}

func ledgerOf(item store.Item) string {
	ledger, _, _ := strings.Cut(item.Head, "/")
	return ledger
}

func orderLines(rows []owner.Row) []string {
	if len(rows) == 0 {
		return nil
	}
	lines := []string{"", fmt.Sprintf("Owner asks nobody has delivered, orders with no live lane, open PR or delivered proof and questions nobody answered (%d):", len(rows))}
	older := max(0, len(rows)-newestOrders)
	for _, row := range rows[older:] {
		lines = append(lines, "- "+row.Line())
	}
	if older > 0 {
		lines = append(lines, fmt.Sprintf("- The %d older ones are in `lr owner audit`.", older))
	}
	return lines
}

type listSection struct {
	title string
	lines []string
	short []string
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
	var orphaned, orphanedShort, late []string
	for _, row := range in.Owed {
		if !v.liveHead(row.Owner) {
			orphaned = append(orphaned, "- "+row.String())
			orphanedShort = append(orphanedShort, "- "+opening(row.String()))
		}
		if row.Overdue(in.Now) {
			late = append(late, "- "+row.String())
		}
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
	var deliveredShort []string
	if len(delivered) > 0 {
		deliveredShort = []string{"- " + strings.Join(delivered, ", ")}
	}
	return []listSection{
		{fmt.Sprintf("Registry lanes quiet over %d h", int(quietAfter.Hours())), quietLines, nil},
		{"Waiting on the coordinator", waiting, waitCounts(in.Waits)},
		{"Owed deletions with no live owner", orphaned, orphanedShort},
		{"Owed deletions past due", late, nil},
		{"Your taps", yourTaps(in), nil},
		{fmt.Sprintf("Delivered in the last %d h", int(heardWithin.Hours())), deliveredLines, deliveredShort},
		{"Done plan steps", doneLines, nil},
	}
}

func yourTaps(in Inputs) []string {
	switch {
	case in.Unboarded != nil:
		return []string{fmt.Sprintf("- board unread: %v", in.Unboarded)}
	case len(in.Cards) == 0:
		return nil
	case in.TapQueue == "":
		return []string{fmt.Sprintf("- %d open, in order on the dashboard's Needs me tab", len(in.Cards))}
	}
	return []string{fmt.Sprintf("- [%d open, in the order they free work](%s)", len(in.Cards), in.TapQueue)}
}

func waitCounts(waits []Record) []string {
	var lanes []string
	count := map[string]int{}
	oldest := map[string]time.Time{}
	for _, wait := range waits {
		if count[wait.Lane] == 0 {
			lanes = append(lanes, wait.Lane)
			oldest[wait.Lane] = wait.At
		}
		count[wait.Lane]++
	}
	lines := make([]string, len(lanes))
	for i, lane := range lanes {
		lines[i] = fmt.Sprintf("- %s: %d waiting, oldest %s", lane, count[lane], pacific(oldest[lane]))
	}
	return lines
}

func recordLine(record Record) string {
	return fmt.Sprintf("- %s %s %s %s: %s", record.Kind, record.Name(), record.Lane, pacific(record.At), clip(record.Text))
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
