package board

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
)

const (
	answersJudged      = 4
	confirmKind        = "confirm"
	touchedHold        = 2 * time.Minute
	boardHoldRefusal   = "holds it for"
	neverOnBoard       = "was never on this board"
	checkedField       = "settled-checked"
	finishedKind       = "finished"
	finishedField      = "finished-checked"
	finishedAskedField = "finished-asked"
	askedField         = "settled-asked"
	cardLabel          = "card:"
	boardCardLabel     = "board-card:"
	ownerLabel         = "from:owner"
	threadProof        = "proof:slack-thread:"
	writeIn            = "wrote “"
	optionsKind        = "options"
	setOptionsKind     = "set-options"
	drawnForHim        = "A coding agent drew this card on the owner's board for him to answer."
)

type ownerAnswer struct {
	ID      string   `json:"id"`
	Title   string   `json:"title"`
	Body    string   `json:"body"`
	Updated string   `json:"updated_at"`
	Tags    []string `json:"tags"`
}

type Settling struct {
	Answer   ownerAnswer
	Why      string
	Finished bool
	Closes   bool
}

type settlingSource struct {
	checked, asked string
	kind           string
	finished       bool
	read           func(context.Context) ([]ownerAnswer, error)
	unread         string
	evidence       func(ownerAnswer) string
}

func hisAnswer(answer ownerAnswer) string {
	return fmt.Sprintf("His answer, recorded in cc-notes, to the question %q, in his words and as recorded:\n%s", answer.Title, answer.Body)
}

func (s Settling) Line() string {
	why := strings.TrimSuffix(strings.TrimSpace(s.Why), ".")
	if s.Finished {
		return "a lane already finished what it asks: " + why
	}
	return fmt.Sprintf("his answer to %q already settles it: %s", strings.TrimSpace(s.Answer.Title), why)
}

func (s Settling) Refusal() error {
	return fmt.Errorf("refused: %s (cc-notes answer %s); apply his answer instead of asking him again", s.Line(), s.shortID())
}

func (s Settling) Extension(card string) string {
	read := fmt.Sprintf("the settled check reads his words under %q (cc-notes answer %s) as settling it", strings.TrimSpace(s.Answer.Title), s.shortID())
	if s.Finished {
		read = fmt.Sprintf("the finished check reads the lane's %q as finishing what it asks", strings.TrimSpace(s.Answer.Title))
	}
	return fmt.Sprintf("Card %s stays on the owner's board, since only his tap, his numbered reply or a relay's record of his words on this card closes it: %s: %s. Withdraw it with `lr ask-owner --withdraw %s` if nothing is left to ask, or reply to this record saying why it stays.", card, read, strings.TrimSuffix(strings.TrimSpace(s.Why), "."), card)
}

func (s Settling) shortID() string {
	return s.Answer.ID[:min(len(s.Answer.ID), 7)]
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

func holdsImage(value any) bool {
	switch shown := value.(type) {
	case map[string]any:
		return shown["type"] == "image" || slices.ContainsFunc(slices.Collect(maps.Values(shown)), holdsImage)
	case []any:
		return slices.ContainsFunc(shown, holdsImage)
	}
	return false
}

func renderShown(card json.RawMessage) (string, bool) {
	var shown map[string]any
	if json.Unmarshal(card, &shown) != nil || !holdsImage(shown) {
		return "", false
	}
	id, _ := shown["id"].(string)
	return id, true
}

func answersThatCanSettle(card json.RawMessage, answers []ownerAnswer) []ownerAnswer {
	id, render := renderShown(card)
	if !render {
		return answers
	}
	return slices.DeleteFunc(slices.Clone(answers), func(answer ownerAnswer) bool { return !answersCard(answer, id) })
}

func answersCard(answer ownerAnswer, id string) bool {
	return slices.Contains(answer.Tags, cardLabel+id) || slices.Contains(answer.Tags, boardCardLabel+id)
}

func closesCard(answer ownerAnswer, id string) bool {
	if !answersCard(answer, id) || !slices.Contains(answer.Tags, ownerLabel) {
		return false
	}
	said, _, _ := strings.Cut(answer.Body, "\n")
	inThread := slices.ContainsFunc(answer.Tags, func(tag string) bool { return strings.HasPrefix(tag, threadProof) })
	if !inThread && !strings.HasPrefix(said, writeIn) {
		return true
	}
	return cardtext.NumberedPick(strings.TrimSuffix(strings.TrimPrefix(said, writeIn), "”"))
}

func settles(ctx context.Context, judge Judge, source settlingSource, card json.RawMessage, question string, answers []ownerAnswer, since string) (*Settling, error) {
	found := candidates(question, answersThatCanSettle(card, answers), since)
	verdicts := make([]*Judgment, len(found))
	failed := make([]error, len(found))
	var wg sync.WaitGroup
	for i, answer := range found {
		wg.Go(func() {
			verdicts[i], failed[i] = judge(ctx, source.kind, source.evidence(answer), string(card))
			if failed[i] == nil && verdicts[i] == nil {
				failed[i] = fmt.Errorf("the judge gave no verdict on answer %s", answer.ID[:min(len(answer.ID), 7)])
			}
		})
	}
	wg.Wait()
	id, _, _ := asksTitle(card)
	var settled *Settling
	for i, verdict := range verdicts {
		if verdictOf(verdict) != "yes" {
			continue
		}
		closes := !source.finished && closesCard(found[i], id)
		if settled == nil || closes && !settled.Closes {
			settled = &Settling{found[i], verdict.Sentence, source.finished, closes}
		}
	}
	if settled != nil {
		return settled, nil
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

func (c Cards) Check(ctx context.Context, judge Judge, card json.RawMessage, replaces ...string) (refused, unchecked error) {
	if name := cardtext.Bare(card); name != "" {
		return cardtext.Refusal(name), nil
	}
	id, title, asks := asksTitle(card)
	if !asks {
		return nil, nil
	}
	if err := cardtext.UnshownDoc(string(card)); err != nil {
		return fmt.Errorf("refused: %w", err), nil
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
		if sameText(ask.Title, title) && !slices.Contains(replaces, ask.ID) {
			return fmt.Errorf("refused: %s already asks him %q; change that card instead of drawing a second", ask.ID, strings.TrimSpace(ask.Title)), nil
		}
	}
	confirm, err := judge(ctx, confirmKind, drawnForHim, string(card))
	if verdictOf(confirm) == "yes" {
		return fmt.Errorf("refused: this card asks him to grade a reading of his own words, and no such card is drawn: %s", confirm.Sentence), nil
	}
	if err != nil || confirm == nil {
		failed = append(failed, fmt.Errorf("the judge did not read whether it asks him to grade a reading: %w", cmp.Or(err, errors.New("no verdict"))))
	}
	answers, err := c.answers(ctx)
	if err != nil {
		failed = append(failed, err)
	}
	settled, err := settles(ctx, judge, c.settledByHim(), card, title, answers, "")
	if settled != nil {
		return settled.Refusal(), nil
	}
	if err != nil {
		failed = append(failed, err)
	}
	offered, err := judge(ctx, optionsKind, drawnForHim, string(card))
	if verdictOf(offered) == "yes" {
		return fmt.Errorf("refused: %s", offered.Sentence), nil
	}
	if err != nil || offered == nil {
		failed = append(failed, fmt.Errorf("the judge did not read whether its options fix the cause and name a fix beside any loss: %w", cmp.Or(err, errors.New("no verdict"))))
	}
	return nil, errors.Join(failed...)
}

func (c Cards) Lacks(ctx context.Context, judge Judge, card json.RawMessage) string {
	id, title, asks := asksTitle(card)
	if !asks {
		return ""
	}
	open, err := c.OpenAsks(ctx)
	if err != nil || slices.ContainsFunc(open, func(ask OpenAsk) bool { return ask.ID == id && sameText(ask.Title, title) }) {
		return ""
	}
	lacking, err := judge(ctx, setOptionsKind, drawnForHim, string(card))
	if err != nil || verdictOf(lacking) != "yes" {
		return ""
	}
	return "this card leaves out what he needs to pick: " + lacking.Sentence
}

func (c Cards) SettledByHim(ctx context.Context, judge Judge, open []OpenCard) (map[string]*Settling, error) {
	if len(open) == 0 {
		return nil, nil
	}
	answers, err := c.answers(ctx)
	if err != nil {
		return nil, err
	}
	found := map[string]*Settling{}
	var failed []error
	for _, card := range open {
		_, title, _ := asksTitle(card.Block)
		settled, err := settles(ctx, judge, c.settledByHim(), card.Block, title, answers, "")
		failed = append(failed, err)
		if settled != nil {
			found[card.ID] = settled
		}
	}
	return found, errors.Join(failed...)
}

func (c Cards) settledByHim() settlingSource {
	return settlingSource{checked: checkedField, asked: askedField, kind: decidedKind, read: c.answers, unread: "his answers", evidence: hisAnswer}
}

func (c Cards) finishedByLanes() settlingSource {
	return settlingSource{checked: finishedField, asked: finishedAskedField, kind: finishedKind, finished: true, read: c.Reports, unread: "the lanes' reports", evidence: func(report ownerAnswer) string {
		return fmt.Sprintf("A lane's %s, in its own words:\n%s", report.Title, report.Body)
	}}
}

func (c Cards) Unask(ctx context.Context, lane string, judge Judge, out io.Writer) error {
	return c.takeDown(ctx, lane, judge, c.settledByHim(), out)
}

func (c Cards) Finished(ctx context.Context, lane string, judge Judge, out io.Writer) error {
	return c.takeDown(ctx, lane, judge, c.finishedByLanes(), out)
}

func (c Cards) takeDown(ctx context.Context, lane string, judge Judge, source settlingSource, out io.Writer) error {
	asks, err := c.OpenAsks(ctx)
	if err != nil || len(asks) == 0 {
		return err
	}
	answers, err := source.read(ctx)
	if err != nil {
		fmt.Fprintf(out, "WARNING every card stays up unchecked against %s: %s\n", source.unread, err)
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
	var unjudged []ledger.Row
	for _, ask := range asks {
		if err := ctx.Err(); err != nil {
			return err
		}
		fields := known[ask.ID]
		since := fields[source.checked]
		if fields[source.asked] != ask.Title {
			since = ""
		}
		question := ask.Title
		if source.finished {
			question += " " + ask.ID
		}
		stamp := ledger.Row{Key: cardPrefix + ask.ID, Fields: map[string]string{source.checked: newest, source.asked: ask.Title}}
		if len(candidates(question, answers, since)) == 0 {
			unjudged = append(unjudged, stamp)
			continue
		}
		shown, onBoard, _, err := c.card(ctx, ask.ID)
		if err != nil {
			return err
		}
		if !onBoard {
			continue
		}
		settled, err := settles(ctx, judge, source, shown, question, answers, since)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		switch {
		case settled != nil && settled.Closes:
			if err := c.sweepOff(ctx, lane, ask.ID, fields, fmt.Sprintf("%s (cc-notes answer %s)", settled.Line(), settled.shortID()), settled.Line(), out); err != nil {
				return err
			}
			continue
		case settled != nil && cmp.Or(fields[laneField], lane) == lane:
			fmt.Fprintf(out, "KEPT %s: %s\n", ask.ID, settled.Extension(ask.ID))
		case settled != nil:
			drew := fields[laneField]
			if err := c.Report(ctx, lane, []string{drew}, ask.ID, settled.Extension(ask.ID)); err != nil {
				return err
			}
			fmt.Fprintf(out, "KEPT %s: told %s that %s\n", ask.ID, drew, settled.Line())
		case err != nil:
			fmt.Fprintf(out, "WARNING %s stays up: its check against %s failed and runs again next pass: %s\n", ask.ID, source.unread, strings.ReplaceAll(err.Error(), "\n", "; "))
			continue
		}
		if err := c.Ledger.Sync(ctx, id, []ledger.Row{stamp}); err != nil {
			return err
		}
	}
	if len(unjudged) == 0 {
		return nil
	}
	return c.Ledger.Sync(ctx, id, unjudged)
}
