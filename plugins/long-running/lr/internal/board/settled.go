package board

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
)

const (
	answersJudged    = 4
	confirmKind      = "confirm"
	touchedHold      = 2 * time.Minute
	boardHoldRefusal = "holds it for"
	checkedField     = "settled-checked"
	askedField       = "settled-asked"
)

type ownerAnswer struct {
	ID      string `json:"id"`
	Title   string `json:"title"`
	Body    string `json:"body"`
	Updated string `json:"updated_at"`
}

type Settling struct {
	Answer ownerAnswer
	Why    string
}

func (s Settling) Line() string {
	return fmt.Sprintf("his answer to %q already settles it: %s", strings.TrimSpace(s.Answer.Title), strings.TrimSuffix(strings.TrimSpace(s.Why), "."))
}

func (s Settling) Refusal() error {
	return fmt.Errorf("refused: %s (cc-notes answer %s); apply his answer instead of asking him again", s.Line(), s.Answer.ID[:min(len(s.Answer.ID), 7)])
}

func (c Cards) answers(ctx context.Context) ([]ownerAnswer, error) {
	listed, err := c.ccn(ctx, "answer", "list", "--json", "--full", "--limit", "0")
	if err != nil {
		return nil, fmt.Errorf("his ccn answers were not read: %w", err)
	}
	var answers []ownerAnswer
	return answers, json.Unmarshal([]byte(listed), &answers)
}

func candidates(title string, answers []ownerAnswer, since string) []ownerAnswer {
	asked := map[string]bool{}
	for _, word := range subjectWords(title) {
		asked[word] = true
	}
	type scored struct {
		answer ownerAnswer
		shared int
	}
	var found []scored
	for _, answer := range answers {
		if answer.Updated <= since {
			continue
		}
		shared := map[string]bool{}
		for _, word := range subjectWords(answer.Title + "\n" + answer.Body) {
			if asked[word] {
				shared[word] = true
			}
		}
		if len(shared) >= questionWords {
			found = append(found, scored{answer, len(shared)})
		}
	}
	slices.SortStableFunc(found, func(one, other scored) int {
		if one.shared != other.shared {
			return other.shared - one.shared
		}
		return strings.Compare(other.answer.Updated, one.answer.Updated)
	})
	out := make([]ownerAnswer, 0, min(len(found), answersJudged))
	for _, hit := range found[:min(len(found), answersJudged)] {
		out = append(out, hit.answer)
	}
	return out
}

func settles(ctx context.Context, judge Judge, card json.RawMessage, found []ownerAnswer) (*Settling, error) {
	verdicts := make([]*Judgment, len(found))
	failed := make([]error, len(found))
	var wg sync.WaitGroup
	for i, answer := range found {
		wg.Go(func() {
			evidence := fmt.Sprintf("His answer, recorded in cc-notes, to the question %q, in his words and as recorded:\n%s", answer.Title, answer.Body)
			verdicts[i], failed[i] = judge(ctx, decidedKind, evidence, string(card))
			if failed[i] == nil && verdicts[i] == nil {
				failed[i] = fmt.Errorf("the judge gave no verdict on answer %s", answer.ID[:min(len(answer.ID), 7)])
			}
		})
	}
	wg.Wait()
	for i, verdict := range verdicts {
		if verdictOf(verdict) == "yes" {
			return &Settling{found[i], verdict.Sentence}, nil
		}
	}
	return nil, errors.Join(failed...)
}

func asksTitle(card json.RawMessage) (string, string, bool) {
	var block struct {
		ID       string `json:"id"`
		Type     string `json:"type"`
		Title    string `json:"title"`
		Children []struct {
			Type string `json:"type"`
		} `json:"children"`
	}
	if json.Unmarshal(card, &block) != nil || block.Type != "card" {
		return "", "", false
	}
	for _, child := range block.Children {
		if child.Type == "choice" {
			return block.ID, block.Title, true
		}
	}
	return "", "", false
}

func sameText(one, other string) bool {
	return strings.EqualFold(strings.Join(strings.Fields(one), " "), strings.Join(strings.Fields(other), " "))
}

func (c Cards) Check(ctx context.Context, judge Judge, card json.RawMessage) (refused, unchecked error) {
	if name := cardtext.Bare(card); name != "" {
		return cardtext.Refusal(name), nil
	}
	id, title, asks := asksTitle(card)
	if !asks {
		return nil, nil
	}
	var failed []error
	open, err := c.OpenAsks(ctx)
	if err != nil {
		failed = append(failed, fmt.Errorf("his board was not read for a card asking the same: %w", err))
	}
	for _, ask := range open {
		if ask.ID == id && sameText(ask.Title, title) {
			return nil, nil
		}
		if sameText(ask.Title, title) {
			return fmt.Errorf("refused: %s already asks him %q; change that card instead of drawing a second", ask.ID, strings.TrimSpace(ask.Title)), nil
		}
	}
	confirm, err := judge(ctx, confirmKind, "A coding agent drew this card on the owner's board for him to answer.", string(card))
	if verdictOf(confirm) == "yes" {
		return fmt.Errorf("refused: this card asks him to grade a reading of his own words, and no such card is drawn: %s", confirm.Sentence), nil
	}
	if err != nil || confirm == nil {
		failed = append(failed, fmt.Errorf("the judge did not read whether it asks him to grade a reading: %w", cmp.Or(err, errors.New("no verdict"))))
	}
	answers, err := c.answers(ctx)
	if err != nil {
		return nil, errors.Join(append(failed, err)...)
	}
	settled, err := settles(ctx, judge, card, candidates(title, answers, ""))
	if settled != nil {
		return settled.Refusal(), nil
	}
	return nil, errors.Join(append(failed, err)...)
}

func (c Cards) Unask(ctx context.Context, lane string, judge Judge, out io.Writer) error {
	asks, err := c.OpenAsks(ctx)
	if err != nil || len(asks) == 0 {
		return err
	}
	answers, err := c.answers(ctx)
	if err != nil {
		fmt.Fprintf(out, "WARNING every card stays up unchecked against his answers: %s\n", err)
		return nil
	}
	newest := ""
	for _, answer := range answers {
		newest = max(newest, answer.Updated)
	}
	id, known, err := c.rows(ctx)
	if err != nil {
		return err
	}
	var checked []ledger.Row
	for _, ask := range asks {
		fields := known[ask.ID]
		since := fields[checkedField]
		if fields[askedField] != ask.Title {
			since = ""
		}
		found := candidates(ask.Title, answers, since)
		if len(found) == 0 {
			checked = append(checked, ledger.Row{Key: cardPrefix + ask.ID, Fields: map[string]string{checkedField: newest, askedField: ask.Title}})
			continue
		}
		shown, onBoard, _, err := c.card(ctx, ask.ID)
		if err != nil {
			return err
		}
		if !onBoard {
			continue
		}
		settled, err := settles(ctx, judge, shown, found)
		if settled != nil {
			if err := c.sweepOff(ctx, lane, ask.ID, fields, settled.Line(), settled.Line(), out); err != nil {
				return err
			}
			continue
		}
		if err != nil {
			fmt.Fprintf(out, "WARNING %s stays up: its check against his answers failed and runs again next pass: %s\n", ask.ID, strings.ReplaceAll(err.Error(), "\n", "; "))
			continue
		}
		checked = append(checked, ledger.Row{Key: cardPrefix + ask.ID, Fields: map[string]string{checkedField: newest, askedField: ask.Title}})
	}
	if len(checked) == 0 {
		return nil
	}
	return c.Ledger.Sync(ctx, id, checked)
}
