package board

import (
	"fmt"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	MergeBlock = "merge-prs"

	FactTitle  = "words-title"
	FactChange = "words-change"
	FactPlan   = "words-plan"
	FactRisk   = "words-risk"
	FactBlocks = "words-blocks"

	factDescription = "ledger-description"
	factStanding    = "ledger-standing"
	FactGroup       = "words-group"
	FactSummary     = "words-summary"
	FactWithdrawn   = prrow.FactWithdrawn

	tapFact  = "tap"
	noTap    = gates.NoTap
	byOwner  = "owner"
	shortSHA = 12
)

var ownerFields = []struct{ fact, name string }{
	{FactChange, "what it changes for a user"},
	{FactPlan, "what differed from the plan"},
	{FactRisk, "the risk"},
	{FactBlocks, "what blocks it"},
}

type Row struct {
	PR    int
	Head  string
	Kind  string
	Lane  string
	Gates []string
	Words map[string]string
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

type TapRecord struct {
	PR    int
	Head  string
	By    string
	Value machine.Tap
}

type Hooks struct {
	Tap    func(TapRecord) error
	Refuse func(tap TapRecord, reason string) error
	Hold   func(pr int, head string) error
	Note   func(lane, text string) error
}

func short(head string) string {
	return head[:min(shortSHA, len(head))]
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
	if on := row.Words[factStanding]; on != "" {
		words += "; " + on
	}
	if row.Words[gates.BehaviorFact] == gates.BehaviorChanged {
		return fmt.Sprintf("%s; changed since the head he tapped, %s: %s", words, short(row.Words[gates.SinceFact]), row.Words[gates.ChangedFact])
	}
	return words
}

func Answer(s *store.Store, repo string, hooks Hooks, row Row, verdict string, at time.Time) error {
	switch verdict {
	case verdictApproved:
		tap := TapRecord{row.PR, row.Head, byOwner, machine.TapTapped}
		_, refusal, err := gates.TapAt(s, repo, bus.Tap{PR: row.PR, Head: row.Head})
		if err != nil {
			return err
		}
		if refusal != "" {
			if _, err := s.PutFact(repo, row.PR, row.Head, bus.TapRefusedFact, refusal, at); err != nil {
				return err
			}
			if err := hooks.Refuse(tap, refusal); err != nil {
				return err
			}
			return fmt.Errorf("#%d: %s", row.PR, refusal)
		}
		if _, err := s.PutFact(repo, row.PR, row.Head, tapFact, string(machine.TapTapped), at); err != nil {
			return err
		}
		return hooks.Tap(tap)
	case verdictRejected:
		return hooks.Hold(row.PR, row.Head)
	}
	return fmt.Errorf("not a board answer: %q", verdict)
}
