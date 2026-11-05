package grading

import (
	"cmp"
	"context"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	defaultPriority  = 100
	rootFirstHorizon = 3 * time.Hour
	firstReach       = 160
)

var (
	prHash       = regexp.MustCompile(`#(\d+)\b`)
	firstWord    = regexp.MustCompile(`(?i)\bfirst\b`)
	firstList    = regexp.MustCompile(`(?i)\bfirst:[^.\n]*`)
	incidentLane = regexp.MustCompile(`^(?:incident|outage)-`)
)

func (p *JobPool) Plan(ctx context.Context, items []store.Item, snapshot map[int]machine.Facts, queued int, now time.Time) ([]store.Item, error) {
	if err := p.Scale(queued, now); err != nil {
		return nil, err
	}
	picks, err := p.Picks()
	if err != nil {
		return nil, err
	}
	p.picks = picks
	return p.Rank(ctx, items, snapshot, now)
}

func (p *JobPool) Rank(ctx context.Context, items []store.Item, snapshot map[int]machine.Facts, now time.Time) ([]store.Item, error) {
	decides, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindDecide, kindAsk}, Lanes: []string{rootLane}, Since: now.Add(-rootFirstHorizon)})
	if err != nil {
		return nil, err
	}
	p.firsts = map[string]bool{}
	for _, decide := range decides {
		for _, pr := range namedFirst(fullText(decide)) {
			p.firsts[pr] = true
		}
	}
	rows, err := p.ledgerRows()
	if err != nil {
		return nil, err
	}
	ranked := slices.Clone(items)
	slices.SortStableFunc(ranked, func(a, b store.Item) int {
		return p.compareItems(a, b, snapshot, rows)
	})
	return ranked, nil
}

func (p *JobPool) ledgerRows() (map[string]map[string]string, error) {
	if p.landing.Ledger == "" {
		return nil, nil
	}
	return p.Ledger(p.landing.Ledger)
}

func (p *JobPool) compareItems(a, b store.Item, snapshot map[int]machine.Facts, rows map[string]map[string]string) int {
	left, right := strconv.Itoa(a.Number), strconv.Itoa(b.Number)
	factsA, factsB := snapshot[a.Number], snapshot[b.Number]
	return cmp.Or(
		cmp.Compare(falseFirst(p.firsts[left]), falseFirst(p.firsts[right])),
		cmp.Compare(falseFirst(incidentLane.MatchString(factsA.Lane)), falseFirst(incidentLane.MatchString(factsB.Lane))),
		cmp.Compare(falseFirst(factsA.Green() && factsA.Approved), falseFirst(factsB.Green() && factsB.Approved)),
		cmp.Compare(priorityOf(rows[left]), priorityOf(rows[right])),
		cmp.Compare(falseFirst(factsA.Tap == machine.TapTapped), falseFirst(factsB.Tap == machine.TapTapped)),
	)
}

func falseFirst(want bool) int {
	if want {
		return 0
	}
	return 1
}

func priorityOf(row map[string]string) int {
	priority, err := strconv.Atoi(row["priority"])
	if err != nil {
		return defaultPriority
	}
	return priority
}

func namedFirst(text string) []string {
	var prs []string
	for _, list := range firstList.FindAllString(text, -1) {
		for _, match := range prHash.FindAllStringSubmatch(list, -1) {
			prs = append(prs, match[1])
		}
	}
	for _, at := range prHash.FindAllStringSubmatchIndex(text, -1) {
		window := text[at[1]:]
		if stop := strings.IndexAny(window, ".\n"); stop >= 0 {
			window = window[:stop]
		}
		found := firstWord.FindStringIndex(window)
		if found != nil && utf8.RuneCountInString(window[:found[0]]) <= firstReach {
			prs = append(prs, text[at[2]:at[3]])
		}
	}
	return prs
}
