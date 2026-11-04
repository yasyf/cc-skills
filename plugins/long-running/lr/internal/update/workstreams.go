package update

import (
	"fmt"
	"slices"
	"strings"
	"unicode"

	"github.com/yasyf/cc-inbox/client"
)

const (
	workstreamsTitle = "Workstreams"
	workstreamList   = "progress-desk/workstreams.md"
	workstreamsHead  = "| Workstream | State | What changed since you last looked | What blocks it | What you must do |"
	inNoWorkstream   = "Live lanes in no workstream: "
)

var workstreamColumns = []string{"Workstream", "Lanes", "State", "Changed", "Blocker", "Owner action"}

type Workstream struct {
	Name    string
	Stems   []string
	State   string
	Changed string
	Blocker string
	Action  string
}

func ReadWorkstreams(path string) ([]Workstream, error) {
	rows, err := readTable(path, workstreamColumns)
	if err != nil {
		return nil, err
	}
	var out []Workstream
	for _, cells := range rows {
		stems := strings.FieldsFunc(cells[1], func(r rune) bool { return r == ',' || unicode.IsSpace(r) })
		out = append(out, Workstream{cells[0], stems, cells[2], cells[3], cells[4], cells[5]})
	}
	return out, nil
}

func (w Workstream) holds(lane string) bool {
	stem := client.Stem(lane)
	return slices.ContainsFunc(w.Stems, func(listed string) bool {
		listed = client.Stem(listed)
		return stem == listed || strings.HasPrefix(stem, listed+"-")
	})
}

func workstreamLines(list []Workstream, unread error, lanes []Lane) (lines, gaps []string) {
	if unread != nil {
		gap := fmt.Sprintf("the workstream list is unread, so a workstream may be missing: %v", unread)
		return []string{"- " + gap}, []string{gap}
	}
	if len(list) == 0 {
		gap := fmt.Sprintf("no workstreams: the progress desk keeps one row per workstream in <state dir>/%s", workstreamList)
		return []string{"- " + gap}, []string{gap}
	}
	lines = []string{workstreamsHead, "|---|---|---|---|---|"}
	for _, each := range list {
		lines = append(lines, fmt.Sprintf("| %s | %s | %s | %s | %s |", cell(each.Name), cell(each.State), cell(each.Changed), cell(each.Blocker), cell(each.Action)))
	}
	var missing []string
	for _, lane := range lanes {
		if !slices.ContainsFunc(list, func(each Workstream) bool { return each.holds(lane.Name) }) {
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
