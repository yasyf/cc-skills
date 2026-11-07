package lanes

import (
	"context"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Unregistered = "unregistered"
	nodeChars    = 8
	unreadShown  = 10
)

var diskPath = regexp.MustCompile("(?:^|[\\s\"'`(\\[=])((?:~|\\$HOME)/[^\\s\"'`)\\]]*|/[^\\s/\"'`)\\]]+/[^\\s\"'`)\\]]*)")

type Liveness struct {
	Lane        string   `json:"lane"`
	State       string   `json:"state"`
	Live        bool     `json:"live"`
	Heard       *string  `json:"heard"`
	PRs         []int    `json:"prs"`
	Handoff     *string  `json:"handoff"`
	RetiredBy   *string  `json:"retired_by"`
	Placement   *string  `json:"placement"`
	Predecessor *string  `json:"predecessor"`
	Route       string   `json:"route"`
	Brief       *string  `json:"brief"`
	Step        string   `json:"step"`
	Standing    string   `json:"standing"`
	Deliverable string   `json:"deliverable"`
	Orders      []string `json:"orders"`
	Woke        *string  `json:"woke"`
	Node        *string  `json:"node"`
	LateMinutes int      `json:"late_minutes"`
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
	heard, owned := must(s.Heard()), must(s.Owners())
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
		row := Liveness{Lane: name, State: Unregistered, Live: true, PRs: append([]int{}, owned[name]...), Route: routeOf(s, name)}
		if at, ok := heard[name]; ok {
			row.Heard = nullable(store.Stamp(at))
		}
		if lane, ok := registered[name]; ok {
			said := must(BriefOf(lane.Brief))
			row.Live = isLive(lane.State)
			row.State, row.Step, row.Standing, row.Deliverable, row.Orders = string(lane.State), or(lane.Step, said.Step), dutyOf(lane), said.Deliverable, said.Orders
			row.Handoff, row.RetiredBy, row.Placement, row.Predecessor, row.Brief = nullable(lane.Handoff), nullable(lane.RetiredBy), nullable(lane.Placement), nullable(lane.Predecessor), nullable(lane.Brief)
			if !lane.Answered.IsZero() {
				row.Woke = nullable(store.Stamp(lane.Answered))
			}
			row.Node, row.LateMinutes = nullable(lane.Node), int(lane.Late.Minutes())
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func RegistryLines(s *store.Store, names []string, now time.Time) (lines []string, err error) {
	defer recovered(&err)
	for _, lane := range must(s.AllLanes()) {
		if len(names) > 0 && !slices.Contains(names, lane.Name) {
			continue
		}
		var line strings.Builder
		state := "silent"
		switch {
		case isLive(lane.State):
			state = "live"
		case now.Sub(lane.Heard) < StaleAfter:
			state = "posting"
		case now.Sub(lane.Heard) <= lane.Late:
			state = "lagging"
		}
		heard := "never"
		if !lane.Heard.IsZero() {
			heard = pacific(lane.Heard)
		}
		fmt.Fprintf(&line, "%s %s %s heard %s", lane.Name, lane.State, state, heard)
		if lane.Node != "" {
			fmt.Fprintf(&line, " on %s, arrived %d min late", lane.Node[:nodeChars], int(lane.Late.Minutes()))
		}
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
		if duty := dutyOf(lane); duty != "" {
			fmt.Fprintf(&line, " standing: %s", duty)
		}
		lines = append(lines, line.String())
	}
	posted := must(s.Heard())
	for _, name := range names {
		if _, found := laneOf(s, name); found {
			continue
		}
		at, heard := posted[name]
		if !heard {
			lines = append(lines, name+" "+Unregistered)
			continue
		}
		state := "silent"
		if now.Sub(at) < StaleAfter {
			state = "posting"
		}
		lines = append(lines, fmt.Sprintf("%s %s %s heard %s #%d", name, Unregistered, state, pacific(at), must(s.NewestSeq(name))))
	}
	return lines, nil
}

type RouteAnswer struct {
	Lane     string `json:"lane"`
	Route    string `json:"route"`
	State    string `json:"state"`
	Standing string `json:"standing"`
	Meta     bool   `json:"meta"`
}

func AnswerRoute(s *store.Store, name string) (answer RouteAnswer, err error) {
	defer recovered(&err)
	answer = RouteAnswer{Lane: name, Route: routeOf(s, name), State: Unregistered, Meta: isMeta(s, name)}
	if lane, found := laneOf(s, name); found {
		answer.State, answer.Standing = string(lane.State), dutyOf(lane)
	}
	return answer, nil
}

type Request struct {
	Verb      string
	Lane      string
	Successor string
	LastRead  string
	Open      string
	Step      string
	PRs       []int
	By        string
}

func (r Request) Draft(drive string) client.Draft {
	attrs := map[string]string{"verb": r.Verb, "lane": r.Lane}
	words := []string{r.Verb, r.Lane}
	if r.Step != "" {
		attrs["step"] = r.Step
		words = append(words, "to step "+r.Step)
	}
	if r.Successor != "" {
		attrs["successor"] = r.Successor
		words = append(words, "for "+r.Successor)
	}
	if r.LastRead != "" {
		attrs["last-read"] = r.LastRead
		words = append(words, "last-read="+r.LastRead)
	}
	if r.Open != "" {
		attrs["open"] = r.Open
		words = append(words, "open: "+r.Open)
	}
	refs := make([]string, 0, len(r.PRs))
	for _, pr := range r.PRs {
		refs = append(refs, strconv.Itoa(pr))
		words = append(words, fmt.Sprintf("#%d", pr))
	}
	return client.Draft{Drive: drive, Lane: r.By, Kind: kindRequest, To: []string{Self}, Attrs: attrs, PRs: refs, Text: "registry request: " + strings.Join(words, " ")}
}

func (r Request) Command() string {
	words := make([]string, 0, 4+2*len(r.PRs))
	words = append(words, "lr", "request", r.Verb, r.Lane)
	for _, pr := range r.PRs {
		words = append(words, "--pr", strconv.Itoa(pr))
	}
	if r.Step != "" {
		words = append(words, "--step", r.Step)
	}
	if r.Successor != "" {
		words = append(words, "--successor", r.Successor)
	}
	if r.LastRead != "" {
		words = append(words, "--last-read", r.LastRead)
	}
	if r.Open != "" {
		words = append(words, "--open", strconv.Quote(r.Open))
	}
	if r.By != "" {
		words = append(words, "--by", r.By)
	}
	return strings.Join(words, " ")
}

func UnreadRefusal(ctx context.Context, s *store.Store, drive string, r Request) (refusal string, err error) {
	defer recovered(&err)
	var named []string
	switch r.Verb {
	case "stand-down":
		if lane, found := laneOf(s, r.Lane); found && isLive(lane.State) {
			named = []string{r.Lane}
		}
	case "owner":
		named = strippedBy(s, r.Lane, r.PRs)
	}
	after, _ := strconv.ParseInt(r.LastRead, 10, 64)
	var newest int64
	var lines strings.Builder
	for _, name := range named {
		read, seq := unseen(s, name, r.LastRead)
		if read == "" {
			continue
		}
		newest = max(newest, seq)
		records := must(client.Tail(ctx, client.Query{Drive: drive, After: after, Lanes: []string{name}}))
		fmt.Fprintf(&lines, "%s: this request %s. Its records you have not read:\n", name, read)
		if hidden := len(records) - unreadShown; hidden > 0 {
			fmt.Fprintf(&lines, "(%d older not shown)\n", hidden)
			records = records[hidden:]
		}
		for _, record := range records {
			fmt.Fprintf(&lines, "%s %s %s\n", record.Name(), record.Kind, record.Text)
			newest = max(newest, record.Seq)
		}
	}
	if lines.Len() == 0 {
		return "", nil
	}
	r.LastRead = strconv.FormatInt(newest, 10)
	return fmt.Sprintf("Nothing posted: the supervisor would refuse this request and hold each lane below.\n%sOnce read, run:\n%s", lines.String(), r.Command()), nil
}

func StandingRefusal(s *store.Store, r Request) (refusal string, err error) {
	defer recovered(&err)
	if r.Verb != "stand-down" {
		return "", nil
	}
	lane, found := laneOf(s, r.Lane)
	if !found || !isStanding(lane) {
		return "", nil
	}
	if duty := dutyOf(lane); duty != "" && r.Successor == "" {
		return fmt.Sprintf("%s is standing (%s): it stands down only for a successor, so name one with --successor%s", r.Lane, duty, dutyEnd(lane)), nil
	}
	if r.Open == "" {
		return fmt.Sprintf("%s is a desk, so its stand-down carries its open items for whoever takes them: every owner order it holds (`lr owner audit`), and every ask, PR and card it owes. Name them with --open '<item>; <item>', or pass --open none", r.Lane), nil
	}
	return "", nil
}

func DoneRefusal(s *store.Store, lane string, said []string, home string, states func(prs []int) (map[int]string, error)) (refusal string, err error) {
	defer recovered(&err)
	if lane == Root {
		return "", nil
	}
	var open []int
	var flags strings.Builder
	if owned := must(s.Owners())[lane]; len(owned) > 0 {
		found := must(states(owned))
		for _, n := range owned {
			if found[n] != Closed {
				open = append(open, n)
				fmt.Fprintf(&flags, " --pr %d", n)
			}
		}
	}
	if len(open) > 0 {
		return fmt.Sprintf("A lane that owns an open PR never posts done, since done retires it for good. Post the blocked record below and wait for each PR to land or close:\n```\nopen: %s\ncci post --lane %s --kind blocked%s --to <your desk> --text '<what lands it>'\n```", numberList(open), lane, flags.String()), nil
	}
	if row, found := laneOf(s, lane); !found || row.Placement != placedOnSprite {
		return "", nil
	}
	if paths := spriteOnly(said, home); len(paths) > 0 {
		return fmt.Sprintf("%s runs on a Sprite, so nobody on the Mac can read %s once the Sprite stops: put each file in cc-notes with `ccn doc add --body-file <path>` and cite the doc id, or attach it to the record with `--path <path>`.", lane, strings.Join(paths, ", ")), nil
	}
	return "", nil
}

func spriteOnly(said []string, home string) []string {
	var paths []string
	for _, words := range said {
		for _, named := range diskPath.FindAllStringSubmatch(words, -1) {
			if path := strings.TrimRight(named[1], ".,;:"); !strings.HasPrefix(path, home+"/") && !slices.Contains(paths, path) {
				paths = append(paths, path)
			}
		}
	}
	return paths
}

func (p Post) Draft(drive string) client.Draft {
	draft := client.Draft{Drive: drive, Lane: Self, Kind: p.Kind, To: p.To, Text: p.Text, Re: p.Re, Successor: p.Successor, Attrs: p.Attrs, Force: true}
	if p.LaneRef != "" {
		draft.LaneRefs = []string{p.LaneRef}
	}
	for _, pr := range p.PRs {
		draft.PRs = append(draft.PRs, strconv.Itoa(pr))
	}
	return draft
}
