package lanes

import (
	"fmt"
	"maps"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const Unregistered = "unregistered"

type Liveness struct {
	Lane        string  `json:"lane"`
	State       string  `json:"state"`
	Live        bool    `json:"live"`
	Heard       *string `json:"heard"`
	PRs         []int   `json:"prs"`
	Handoff     *string `json:"handoff"`
	RetiredBy   *string `json:"retired_by"`
	Placement   *string `json:"placement"`
	Predecessor *string `json:"predecessor"`
	Route       string  `json:"route"`
	Brief       *string `json:"brief"`
	Step        string  `json:"step"`
	Standing    string  `json:"standing"`
	Deliverable string  `json:"deliverable"`
}

func nullable(value string) *string {
	if value == "" {
		return nil
	}
	return &value
}

func LivenessRows(s *store.Store) (rows []Liveness, err error) {
	defer recovered(&err)
	registered := map[string]store.Lane{}
	for _, lane := range must(s.AllLanes()) {
		registered[lane.Name] = lane
	}
	heard, owned, probes := must(s.Heard()), must(s.Owners()), must(s.Probes())
	names := map[string]bool{}
	for name := range registered {
		names[name] = true
	}
	for name := range heard {
		names[name] = true
	}
	for name := range owned {
		names[name] = true
	}
	for _, name := range slices.Sorted(maps.Keys(names)) {
		row := Liveness{Lane: name, State: Unregistered, Live: probes[name].Dead.IsZero(), PRs: slices.Concat([]int{}, owned[name]), Route: routeOf(s, name)}
		if at, ok := heard[name]; ok {
			row.Heard = nullable(store.Stamp(at))
		}
		if lane, ok := registered[name]; ok {
			said := must(BriefOf(lane.Brief))
			row.State, row.Step, row.Standing, row.Deliverable = string(lane.State), said.Step, said.Standing, said.Deliverable
			row.Handoff, row.RetiredBy, row.Placement, row.Predecessor, row.Brief = nullable(lane.Handoff), nullable(lane.RetiredBy), nullable(lane.Placement), nullable(lane.Predecessor), nullable(lane.Brief)
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func RegistryLines(s *store.Store, names []string) (lines []string, err error) {
	defer recovered(&err)
	probes := must(s.Probes())
	for _, lane := range must(s.AllLanes()) {
		if len(names) > 0 && !slices.Contains(names, lane.Name) {
			continue
		}
		var line strings.Builder
		state := "silent"
		if probes[lane.Name].Dead.IsZero() {
			state = "live"
		}
		heard := "never"
		if !lane.Heard.IsZero() {
			heard = pacific(lane.Heard)
		}
		fmt.Fprintf(&line, "%s %s %s heard %s", lane.Name, lane.State, state, heard)
		if lane.Last != "" {
			fmt.Fprintf(&line, " %s", lane.Last)
		}
		if seq := must(s.NewestSeq(lane.Name)); seq != 0 {
			fmt.Fprintf(&line, " #%d", seq)
		}
		fmt.Fprintf(&line, " since %s handoff %s prs %s", lane.Since.UTC().Format("15:04Z"), or(lane.Handoff, "-"), or(numberList(lane.PRs), "-"))
		if lane.Predecessor != "" {
			fmt.Fprintf(&line, " after %s: %s", lane.Predecessor, lane.Reason)
		}
		if lane.RetiredBy != "" {
			fmt.Fprintf(&line, " retired by %s", lane.RetiredBy)
		}
		if duty := must(BriefOf(lane.Brief)).Standing; duty != "" {
			fmt.Fprintf(&line, " standing: %s", duty)
		}
		lines = append(lines, line.String())
	}
	for _, name := range names {
		if _, found := laneOf(s, name); !found {
			lines = append(lines, name+" "+Unregistered)
		}
	}
	return lines, nil
}

type RouteAnswer struct {
	Lane     string `json:"lane"`
	Route    string `json:"route"`
	State    string `json:"state"`
	Standing string `json:"standing"`
}

func AnswerRoute(s *store.Store, name string) (answer RouteAnswer, err error) {
	defer recovered(&err)
	answer = RouteAnswer{Lane: name, Route: routeOf(s, name), State: Unregistered}
	if lane, found := laneOf(s, name); found {
		answer.State, answer.Standing = string(lane.State), must(BriefOf(lane.Brief)).Standing
	}
	return answer, nil
}

type Request struct {
	Verb      string
	Lane      string
	Successor string
	LastRead  string
	PRs       []int
	By        string
}

func (r Request) Args(drive string) []string {
	args := []string{"post", "--drive", drive, "--lane", r.By, "--kind", kindRequest, "--to", Self, "--attr", "verb=" + r.Verb, "--attr", "lane=" + r.Lane}
	words := []string{r.Verb, r.Lane}
	if r.Successor != "" {
		args = append(args, "--attr", "successor="+r.Successor)
		words = append(words, "for "+r.Successor)
	}
	if r.LastRead != "" {
		args = append(args, "--attr", "last-read="+r.LastRead)
		words = append(words, "last-read="+r.LastRead)
	}
	for _, pr := range r.PRs {
		args = append(args, "--pr", fmt.Sprint(pr))
		words = append(words, fmt.Sprintf("#%d", pr))
	}
	return append(args, "--stamp", "--text", "registry request: "+strings.Join(words, " "))
}

func StandingRefusal(s *store.Store, r Request) (refusal string, err error) {
	defer recovered(&err)
	if r.Verb != "stand-down" || r.Successor != "" {
		return "", nil
	}
	lane, found := laneOf(s, r.Lane)
	if !found {
		return "", nil
	}
	if duty := must(BriefOf(lane.Brief)).Standing; duty != "" {
		return fmt.Sprintf("%s is standing (%s): it stands down only for a successor, so name one with --successor, or delete the `Standing:` line in %s to end the duty", r.Lane, duty, lane.Brief), nil
	}
	return "", nil
}

func (p Post) Args(drive string) []string {
	args := []string{"post", "--drive", drive, "--lane", Self, "--kind", p.Kind}
	for _, to := range p.To {
		args = append(args, "--to", to)
	}
	if p.Successor != "" {
		args = append(args, "--successor", p.Successor)
	}
	if p.Re != 0 {
		args = append(args, "--re", fmt.Sprint(p.Re))
	}
	if p.LaneRef != "" {
		args = append(args, "--lane-ref", p.LaneRef)
	}
	if p.Attr != "" {
		args = append(args, "--attr", p.Attr)
	}
	for _, pr := range p.PRs {
		args = append(args, "--pr", fmt.Sprint(pr))
	}
	return append(args, "--stamp", "--text", p.Text)
}
