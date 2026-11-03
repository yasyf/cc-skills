package board

import (
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Merge   = "Merge"
	Hold    = "Hold"
	Explain = "Explain more"

	tapFact  = "tap"
	noTap    = "no tap"
	byOwner  = "owner"
	shortSHA = 12
)

type Key struct {
	PR   int
	Head string
}

type Row struct {
	PR          int
	Head        string
	Kind        string
	Gates       []string
	CarriedFrom string
}

func (r Row) Key() Key {
	return Key{r.PR, r.Head}
}

func (r Row) BlockID() string {
	return fmt.Sprintf("%s-%d-%s", r.Kind, r.PR, short(r.Head))
}

type Block struct {
	ID      string   `json:"id"`
	Type    string   `json:"type"`
	Title   string   `json:"title"`
	Body    string   `json:"body"`
	Options []string `json:"options"`
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
		if item.Mover != machine.MoverOwner || !found || facts.Head != item.Head || facts.Tapped() {
			continue
		}
		stored, err := s.Facts(repo, item.Number, item.Head)
		if err != nil {
			return nil, err
		}
		out = append(out, Row{item.Number, item.Head, item.Kind, gates.Missing(facts), stored[gates.CarriedFact]})
	}
	return out, nil
}

func Render(rows []Row, answered map[Key]bool) []Block {
	blocks := []Block{}
	for _, row := range rows {
		if answered[row.Key()] {
			continue
		}
		blocks = append(blocks, Block{
			ID:      row.BlockID(),
			Type:    "choice",
			Title:   fmt.Sprintf("#%d at %s: %s", row.PR, short(row.Head), row.Kind),
			Body:    body(row),
			Options: []string{Merge, Hold, Explain},
		})
	}
	return blocks
}

func body(row Row) string {
	waits := slices.DeleteFunc(slices.Clone(row.Gates), func(gate string) bool { return gate == noTap })
	words := "every other gate is met"
	if len(waits) > 0 {
		words = strings.Join(waits, ", ")
	}
	if row.CarriedFrom != "" {
		return fmt.Sprintf("%s; rebased from %s, same patch", words, short(row.CarriedFrom))
	}
	return words
}

func Answer(s *store.Store, repo string, hooks Hooks, row Row, choice string, at time.Time) error {
	switch choice {
	case Merge:
		if _, err := s.PutFact(repo, row.PR, row.Head, tapFact, string(machine.TapTapped), at); err != nil {
			return err
		}
		return hooks.Tap(TapRecord{row.PR, row.Head, byOwner, machine.TapTapped})
	case Hold:
		return hooks.Hold(row.PR, row.Head)
	case Explain:
		return nil
	}
	return fmt.Errorf("not a board answer: %q", choice)
}
