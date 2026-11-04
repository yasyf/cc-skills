package update

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	workstreamsTitle = "Workstreams"
	workstreamList   = "progress-desk/workstreams.md"
	workstreamsHead  = "| Workstream | What it delivers | Lanes on it | State | What changed since you last looked | What blocks it | What you must do |"
	inNoWorkstream   = "Live lanes in no workstream: "
	noLiveLane       = "no live lane"
)

var (
	workstreamColumns = []string{"Workstream", "Delivers", "Lanes", "Steps", "Note"}
	changedKinds      = []string{"report", "done", "landed", "fix-live", "handoff"}
	blockedKinds      = []string{"blocked", "blocker"}
)

type Workstream struct {
	Name     string
	Delivers string
	Stems    []string
	Steps    []string
	Note     string
}

func ReadWorkstreams(path string) ([]Workstream, error) {
	rows, err := readTable(path, workstreamColumns)
	if err != nil {
		return nil, err
	}
	var out []Workstream
	for _, cells := range rows {
		out = append(out, Workstream{cells[0], cells[1], listed(cells[2]), listed(cells[3]), cells[4]})
	}
	return out, nil
}

func listed(text string) []string {
	return strings.FieldsFunc(text, func(r rune) bool { return r == ',' || unicode.IsSpace(r) })
}

func (w Workstream) holds(lane Lane) bool {
	stem := client.Stem(lane.Name)
	if slices.ContainsFunc(w.Stems, func(listed string) bool {
		listed = client.Stem(listed)
		return stem == listed || strings.HasPrefix(stem, listed+"-")
	}) {
		return true
	}
	return lane.Standing == "" && slices.ContainsFunc(w.Steps, func(number string) bool { return onStep(lane, &plans.Step{Number: number}) })
}

func (v view) laneNamed(name string) Lane {
	if lane, found := v.byLane[name]; found {
		return lane
	}
	return Lane{Name: name}
}

func stamp(at, now time.Time) string {
	if at.IsZero() || !at.Before(midnight(now)) {
		return pacificText(at)
	}
	return at.In(pacificZone).Format("Jan 2 3:04 PM")
}

func prWord(row store.ReviewRow) string {
	switch {
	case row.State == prrow.StateQueued:
		return queueLabelled
	case row.Hold != "" && row.Hold != prrow.None:
		return "held"
	case row.CI == prrow.CIRed:
		return "red"
	case row.TapRefused != "" && row.TapRefused != prrow.None:
		return "refused"
	case row.ReadyForTap && !row.Tapped:
		return "ready for your tap"
	case row.Tapped:
		return "tapped"
	}
	return "open"
}

func openPRRows(rows []store.ReviewRow) []store.ReviewRow {
	return slices.DeleteFunc(slices.Clone(rows), func(row store.ReviewRow) bool {
		return prrow.Terminal(row.State) || row.State == prrow.StateSuperseded
	})
}

func (v view) workstreamLines(in Inputs, lanes []Lane) (lines, gaps []string) {
	if in.Unread != nil {
		gap := fmt.Sprintf("the workstream list is unread, so a workstream may be missing: %v", in.Unread)
		return []string{"- " + gap}, []string{gap}
	}
	if len(in.Streams) == 0 {
		gap := fmt.Sprintf("no workstreams: the progress desk keeps one row per workstream in <state dir>/%s", workstreamList)
		return []string{"- " + gap}, []string{gap}
	}
	lines = []string{workstreamsHead, "|---|---|---|---|---|---|---|"}
	for _, each := range in.Streams {
		lines = append(lines, v.workstreamRow(in, each, lanesWhere(lanes, each.holds)))
	}
	var missing []string
	for _, lane := range lanes {
		if !slices.ContainsFunc(in.Streams, func(each Workstream) bool { return each.holds(lane) }) {
			missing = append(missing, lane.Name)
		}
	}
	if len(missing) == 0 {
		return lines, nil
	}
	slices.Sort(missing)
	gap := inNoWorkstream + strings.Join(missing, ", ")
	return append(lines, "", gap), []string{gap}
}

func (v view) workstreamRow(in Inputs, each Workstream, mine []Lane) string {
	dayStart := in.Now.Add(-quietAfter)
	prs := v.workstreamPRs(in.PRRows, each)
	open := openPRRows(prs)
	var state, changed, blocks, actions []string
	for _, pr := range open {
		word := prWord(pr)
		state = append(state, fmt.Sprintf("#%d %s", pr.Number, word))
		if seq, held := in.Held[strconv.Itoa(pr.Number)]; held {
			blocks = append(blocks, fmt.Sprintf("#%d held by cci #%d", pr.Number, seq))
		} else if _, wrong := prWrong[word]; wrong {
			blocks = append(blocks, fmt.Sprintf("#%d %s", pr.Number, word))
		}
	}
	for _, step := range stepsNumbered(in.Plans, each.Steps) {
		state = append(state, fmt.Sprintf("step %s %d of %d tasks done", step.Number, step.Count(plans.Done), len(step.Tasks)))
		for _, task := range step.Tasks {
			if task.State == plans.Blocked && task.Waiting != "" {
				blocks = append(blocks, fmt.Sprintf("step %s task %s waits on %s", step.Number, clip(task.Title), task.Waiting))
			}
		}
	}
	for _, order := range in.Orders {
		if order.Lane != "" && each.holds(v.laneNamed(order.Lane)) {
			state = append(state, "owes your order "+order.ID)
		}
	}
	var newest Record
	for _, lane := range mine {
		last, found := v.latest[lane.Name]
		if wait, waiting := v.waits[lane.Name]; waiting {
			blocks = append(blocks, fmt.Sprintf("%s waits on the coordinator's answer to %s #%d", lane.Name, wait.Kind, wait.Seq))
		} else if found && slices.Contains(blockedKinds, last.Kind) && last.Status == statusOpen {
			blocks = append(blocks, fmt.Sprintf("%s: %s", lane.Name, clip(last.Text)))
		}
		if found && last.Seq > newest.Seq {
			newest = last
		}
	}
	if newest.Lane != "" {
		state = append(state, fmt.Sprintf("%s %s %s: %s", newest.Lane, newest.Kind, pacific(newest.At), clip(newest.Text)))
	}
	if each.Note != "" {
		state = append(state, each.Note)
	}
	for _, pr := range prs {
		if landed, err := time.Parse(time.RFC3339, pr.LandedAt); pr.State == prrow.StateLanded && err == nil && landed.After(in.Before.At) {
			changed = append(changed, fmt.Sprintf("#%d landed %s", pr.Number, stamp(landed, in.Now)))
		}
	}
	for _, release := range slices.Backward(in.Releases) {
		if release.At.After(in.Before.At) && slices.ContainsFunc(prs, func(pr store.ReviewRow) bool { return slices.Contains(prsOf(release), strconv.Itoa(pr.Number)) }) {
			changed = append(changed, fmt.Sprintf("release %s: %s", pacific(release.At), clip(release.Text)))
			break
		}
	}
	for _, record := range slices.Backward(bySeq(in.All)) {
		if record.At.After(in.Before.At) && slices.Contains(changedKinds, record.Kind) && each.holds(v.laneNamed(record.Lane)) {
			changed = append(changed, fmt.Sprintf("%s %s %s: %s", record.Lane, record.Kind, pacific(record.At), clip(record.Text)))
			break
		}
	}
	for _, card := range in.Cards {
		if onCard(card, each, open) {
			actions = append(actions, card.Line())
		}
	}
	noAction := "nothing on your board as of " + pacific(in.Now)
	if in.Unboarded != nil {
		noAction = fmt.Sprintf("board unread: %v", in.Unboarded)
	}
	cells := []string{
		cell(each.Name),
		cell(each.Delivers),
		strings.ReplaceAll(v.workstreamLanes(each, mine), "|", "/"),
		cell(orWord(strings.Join(state, "; "), "nothing recorded since "+stamp(dayStart, in.Now))),
		cell(orWord(strings.Join(changed, "; "), "nothing recorded since "+stamp(in.Before.At, in.Now))),
		cell(orWord(strings.Join(blocks, "; "), "nothing recorded since "+stamp(dayStart, in.Now))),
		cell(orWord(strings.Join(actions, "; "), noAction)),
	}
	return "| " + strings.Join(cells, " | ") + " |"
}

func (v view) workstreamPRs(rows []store.ReviewRow, each Workstream) []store.ReviewRow {
	var numbers []int
	for _, lane := range v.lanes {
		if each.holds(lane) {
			numbers = append(numbers, lane.PRs...)
		}
	}
	var out []store.ReviewRow
	for _, pr := range rows {
		if (pr.Lane != "" && each.holds(v.laneNamed(pr.Lane))) || slices.Contains(numbers, pr.Number) {
			out = append(out, pr)
		}
	}
	slices.SortFunc(out, func(one, other store.ReviewRow) int { return one.Number - other.Number })
	return out
}

func stepsNumbered(list []*plans.Plan, numbers []string) []*plans.Step {
	var out []*plans.Step
	for _, plan := range list {
		for _, step := range plan.Steps {
			if slices.Contains(numbers, step.Number) {
				out = append(out, step)
			}
		}
	}
	return out
}

func (v view) workstreamLanes(each Workstream, mine []Lane) string {
	if len(mine) > 0 {
		return strings.Join(namesOf(mine), ", ")
	}
	var last string
	var at time.Time
	for _, lane := range v.lanes {
		if each.holds(lane) && lane.Heard.After(at) {
			last, at = lane.Name, lane.Heard
		}
	}
	for _, number := range each.Steps {
		if record, found := v.lastOnStep(number); found && record.At.After(at) {
			last, at = record.Lane, record.At
		}
	}
	if last == "" {
		return noLiveLane + ", and no lane on it recorded"
	}
	return fmt.Sprintf("%s; last %s, heard %s", noLiveLane, last, stamp(at, v.now))
}

func onCard(card board.Wait, each Workstream, open []store.ReviewRow) bool {
	if card.PR != "" {
		return slices.ContainsFunc(open, func(pr store.ReviewRow) bool { return strconv.Itoa(pr.Number) == card.PR })
	}
	words := strings.ToLower(strings.Join([]string{card.Block, card.Subject, card.Title}, " "))
	return slices.ContainsFunc(each.Stems, func(stem string) bool {
		stem = regexp.QuoteMeta(strings.ToLower(client.Stem(stem)))
		return regexp.MustCompile(`(?:^|[^a-z0-9])(?:` + stem + `|` + strings.ReplaceAll(stem, "-", " ") + `)(?:$|[^a-z0-9])`).MatchString(words)
	})
}
