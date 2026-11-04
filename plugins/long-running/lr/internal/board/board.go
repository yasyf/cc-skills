package board

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	MergeBlock = "merge-prs"

	FactTitle     = "words-title"
	FactChange    = "words-change"
	FactPlan      = "words-plan"
	FactRisk      = "words-risk"
	FactBlocks    = "words-blocks"
	FactGroup     = "words-group"
	FactSummary   = "words-summary"
	FactWithdrawn = prrow.FactWithdrawn

	tapFact   = "tap"
	notedFact = "words-noted"
	noTap     = gates.NoTap
	byOwner   = "owner"
	shortSHA  = 12
)

var ownerFields = []struct{ fact, name string }{
	{FactChange, "what it changes for a user"},
	{FactPlan, "what differed from the plan"},
	{FactRisk, "the risk"},
	{FactBlocks, "what blocks it"},
}

type Key struct {
	PR   int
	Head string
}

type Row struct {
	PR    int
	Head  string
	Kind  string
	Lane  string
	Gates []string
	Words map[string]string
}

func (r Row) Key() Key {
	return Key{r.PR, r.Head}
}

func (r Row) MissingWords() []string {
	var missing []string
	for _, field := range ownerFields {
		if r.Words[field.fact] == "" {
			missing = append(missing, field.name)
		}
	}
	return missing
}

type PRRow struct {
	Number  int    `json:"number"`
	Head    string `json:"head"`
	Title   string `json:"title"`
	Change  string `json:"change"`
	Plan    string `json:"plan"`
	Risk    string `json:"risk"`
	Review  string `json:"review"`
	Blocker string `json:"blocker"`
}

type Group struct {
	ID      string  `json:"id"`
	Title   string  `json:"title"`
	Summary string  `json:"summary"`
	PRs     []PRRow `json:"prs"`
}

type Block struct {
	ID     string  `json:"id"`
	Type   string  `json:"type"`
	Repo   string  `json:"repo"`
	Prompt string  `json:"prompt"`
	Groups []Group `json:"groups"`
}

type TapRecord struct {
	PR    int
	Head  string
	By    string
	Value machine.Tap
}

type Hooks struct {
	Tap  func(TapRecord) error
	Hold func(pr int, head string) error
	Note func(lane, text string) error
}

func short(head string) string {
	return head[:min(shortSHA, len(head))]
}

func Rows(s *store.Store, repo string, snapshot map[int]machine.Facts) ([]Row, error) {
	items, err := s.OpenItems(repo, 0)
	if err != nil {
		return nil, err
	}
	var out []Row
	for _, item := range items {
		facts, found := snapshot[item.Number]
		if item.Mover != machine.MoverOwner || !found || !facts.Ours() || facts.Head != item.Head || facts.Tapped() || facts.Hold != "" {
			continue
		}
		stored, err := s.Facts(repo, item.Number, item.Head)
		if err != nil {
			return nil, err
		}
		if stored[FactWithdrawn] != "" {
			continue
		}
		out = append(out, Row{item.Number, item.Head, item.Kind, facts.Lane, gates.Missing(facts), stored})
	}
	return out, nil
}

func Render(repo string, rows []Row, answered map[Key]bool) Block {
	block := Block{ID: MergeBlock, Type: "pr-groups", Repo: repo, Prompt: "Approve each change?", Groups: []Group{}}
	for _, row := range rows {
		if answered[row.Key()] {
			continue
		}
		name := row.Words[FactGroup]
		if name == "" {
			name = row.Lane
		}
		at := slices.IndexFunc(block.Groups, func(group Group) bool { return group.ID == name })
		if at < 0 {
			summary := row.Words[FactSummary]
			if summary == "" {
				summary = "From lane " + row.Lane + "."
			}
			block.Groups = append(block.Groups, Group{ID: name, Title: name, Summary: summary})
			at = len(block.Groups) - 1
		}
		block.Groups[at].PRs = append(block.Groups[at].PRs, prRow(row))
	}
	return block
}

func prRow(row Row) PRRow {
	title := row.Words[FactTitle]
	if title == "" {
		title = fmt.Sprintf("#%d", row.PR)
	}
	return PRRow{row.PR, short(row.Head), title, row.Words[FactChange], row.Words[FactPlan], row.Words[FactRisk], review(row), row.Words[FactBlocks]}
}

func review(row Row) string {
	waits := withoutTap(row.Gates)
	words := "every other gate is met"
	if len(waits) > 0 {
		words = strings.Join(waits, ", ")
	}
	if row.Words[gates.BehaviorFact] == gates.BehaviorChanged {
		return fmt.Sprintf("%s; changed since the head he tapped, %s: %s", words, short(row.Words[gates.SinceFact]), row.Words[gates.ChangedFact])
	}
	return words
}

func Answer(s *store.Store, repo string, hooks Hooks, row Row, verdict string, at time.Time) error {
	switch verdict {
	case verdictApproved:
		if _, err := s.PutFact(repo, row.PR, row.Head, tapFact, string(machine.TapTapped), at); err != nil {
			return err
		}
		return hooks.Tap(TapRecord{row.PR, row.Head, byOwner, machine.TapTapped})
	case verdictRejected:
		return hooks.Hold(row.PR, row.Head)
	}
	return fmt.Errorf("not a board answer: %q", verdict)
}

type Pass struct {
	Store   *store.Store
	Repo    string
	Runner  shell.Runner
	CCX     ccx.Client
	Session string
	Hooks   Hooks
	Now     func() time.Time
}

func (p Pass) Run(ctx context.Context, snapshot map[int]machine.Facts) (Block, error) {
	rows, err := Rows(p.Store, p.Repo, snapshot)
	if err != nil {
		return Block{}, err
	}
	outcomes, err := ReadOutcomes(ctx, p.Runner, p.Session)
	if err != nil {
		return Block{}, err
	}
	if err := p.redrawMoved(ctx, outcomes); err != nil {
		return Block{}, err
	}
	answered := map[Key]bool{}
	live := Live{Taps: newestByPR(outcomes.Interactions.Triage)}
	for _, row := range rows {
		verdict := live.Decided(MergeRow{PR: fmt.Sprint(row.PR), Head: row.Head})
		if verdict == "" {
			continue
		}
		if err := Answer(p.Store, p.Repo, p.Hooks, row, verdict, p.Now()); err != nil {
			return Block{}, err
		}
		answered[row.Key()] = true
	}
	block := Render(p.Repo, rows, answered)
	encoded, err := json.Marshal(block)
	if err != nil {
		return Block{}, err
	}
	if _, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "update-block", "-", "--session", p.Session, "--rev", strconv.FormatInt(outcomes.Rev, 10)}, Stdin: string(encoded)}); err != nil && !writeRefused(err) {
		return Block{}, err
	}
	return block, p.noteMissing(rows, answered)
}

func (p Pass) noteMissing(rows []Row, answered map[Key]bool) error {
	for _, row := range rows {
		missing := row.MissingWords()
		if answered[row.Key()] || len(missing) == 0 {
			continue
		}
		fields := strings.Join(missing, ", ")
		changed, err := p.Store.PutFact(p.Repo, row.PR, row.Head, notedFact, fields, p.Now())
		if err != nil {
			return err
		}
		if !changed {
			continue
		}
		text := fmt.Sprintf("#%d at %s is on the owner's board without %s; post a head record carrying them.", row.PR, short(row.Head), fields)
		if err := p.Hooks.Note(row.Lane, text); err != nil {
			return err
		}
	}
	return nil
}
