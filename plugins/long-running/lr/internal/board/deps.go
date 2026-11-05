package board

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	communicator = "owner-relay"
	premiseKind  = "premise"
	decidedKind  = "decided"
	movedTopic   = "cards-moved"
	inForce      = "in-force"
)

var prCited = regexp.MustCompile(`(?:#|/pull/)(\d+)\b`)

type Judgment struct {
	Verdict        string `json:"verdict"`
	ArtifactPhrase string `json:"artifact_phrase"`
	EvidencePhrase string `json:"evidence_phrase"`
	Sentence       string `json:"sentence"`
}

type Judge func(ctx context.Context, kind, evidence, artifact string) (*Judgment, error)

type Moved struct {
	Card store.CardRef
	How  string
	Why  string
}

func (m Moved) Line() string {
	switch m.How {
	case "redrew":
		return fmt.Sprintf("redrew %s on the new premise: %s", m.Card.Card, m.Why)
	}
	return fmt.Sprintf("kept %s and flagged it to %s: %s", m.Card.Card, laneOr(m.Card.Lane), m.Why)
}

func (m Moved) laneReport(cause string) string {
	if m.How == "held" {
		return fmt.Sprintf("Card %s stays on the owner's board for you to redraw or withdraw: %s changes it, and %s.", m.Card.Card, cause, m.Why)
	}
	return fmt.Sprintf("Card %s stays on the owner's board for you to redraw or withdraw: %s may change it, and the judge could not settle how (%s).", m.Card.Card, cause, m.Why)
}

func laneOr(lane string) string {
	if lane == "" {
		return "no owner lane, since none registered it"
	}
	return lane
}

func prKey(number string) string { return "#" + number }

func cardKey(card string) string { return "card:" + card }

func termKey(term string) string { return strings.ToLower(strings.TrimSpace(term)) }

func listed(value string) ([]string, error) {
	if value == "" {
		return nil, nil
	}
	if !strings.HasPrefix(value, "[") {
		return []string{value}, nil
	}
	var out []string
	return out, json.Unmarshal([]byte(value), &out)
}

func rulingTerms(fields map[string]string) ([]string, error) {
	var terms []string
	for _, field := range []string{"names", "surfaces"} {
		values, err := listed(fields[field])
		if err != nil {
			return nil, fmt.Errorf("register %s %q: %w", field, fields[field], err)
		}
		terms = append(terms, values...)
	}
	return slices.DeleteFunc(terms, func(term string) bool { return strings.TrimSpace(term) == "" }), nil
}

func landedWords(fields map[string]string) (string, error) {
	var added []struct {
		Words string `json:"words"`
	}
	if err := json.Unmarshal([]byte(cmp.Or(fields["repeats"], "[]")), &added); err != nil {
		return "", fmt.Errorf("register repeats %q: %w", fields["repeats"], err)
	}
	if len(added) == 0 {
		return fields["words"], nil
	}
	return added[len(added)-1].Words, nil
}

func rulingDeps(fields map[string]string, landed string) ([]string, error) {
	terms, err := rulingTerms(fields)
	if err != nil {
		return nil, err
	}
	deps := map[string]bool{}
	for _, term := range terms {
		deps[termKey(term)] = true
	}
	for _, cited := range prCited.FindAllStringSubmatch(landed+" "+fields["names"]+" "+fields["surfaces"], -1) {
		deps[prKey(cited[1])] = true
	}
	return sortedKeys(deps), nil
}

func cardDeps(card json.RawMessage, terms, explicit []string) ([]string, error) {
	var tree any
	if err := json.Unmarshal(card, &tree); err != nil {
		return nil, err
	}
	deps := map[string]bool{}
	var text strings.Builder
	var walk func(node any)
	walk = func(node any) {
		switch value := node.(type) {
		case map[string]any:
			if number, ok := value["number"].(float64); ok && value["type"] == "pr" {
				deps[prKey(strconv.Itoa(int(number)))] = true
			}
			for _, child := range value {
				walk(child)
			}
		case []any:
			for _, child := range value {
				walk(child)
			}
		case string:
			text.WriteString(value)
			text.WriteString("\n")
		}
	}
	walk(tree)
	said := text.String()
	for _, cited := range prCited.FindAllStringSubmatch(said, -1) {
		deps[prKey(cited[1])] = true
	}
	for _, term := range terms {
		if termPattern(term).MatchString(said) {
			deps[termKey(term)] = true
		}
	}
	for _, dep := range explicit {
		deps[termKey(dep)] = true
	}
	return sortedKeys(deps), nil
}

func termPattern(term string) *regexp.Regexp {
	return regexp.MustCompile(`(?i)(?:^|[^\w-])` + regexp.QuoteMeta(strings.TrimSpace(term)) + `(?:[^\w-]|$)`)
}

func sortedKeys(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for key := range set {
		out = append(out, key)
	}
	slices.Sort(out)
	return out
}

func (c Cards) register(ctx context.Context) ([]ledger.Row, error) {
	return ledger.Register(ctx, c.ccn, c.Ledger, c.Program)
}

func (c Cards) card(ctx context.Context, card string) (json.RawMessage, bool, int64, error) {
	shown, err := c.present(ctx, "outcomes", "--block", card)
	if err != nil {
		return nil, false, 0, err
	}
	var outcomes struct {
		Rev     int64 `json:"rev"`
		OnBoard bool  `json:"onBoard"`
		Doc     struct {
			Blocks []json.RawMessage `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return nil, false, 0, fmt.Errorf("cc-present outcomes --block %s: %w", card, err)
	}
	for _, block := range outcomes.Doc.Blocks {
		var top struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal(block, &top); err != nil {
			return nil, false, 0, err
		}
		if top.ID == card {
			return block, outcomes.OnBoard, outcomes.Rev, nil
		}
	}
	return nil, false, 0, nil
}

type boardRead struct {
	rev    int64
	blocks map[string]json.RawMessage
}

func (c Cards) wholeBoard(ctx context.Context) (boardRead, error) {
	shown, err := c.present(ctx, "outcomes", "--all")
	if err != nil {
		return boardRead{}, err
	}
	var outcomes struct {
		Rev int64 `json:"rev"`
		Doc struct {
			Blocks []json.RawMessage `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return boardRead{}, fmt.Errorf("cc-present outcomes --all: %w", err)
	}
	read := boardRead{rev: outcomes.Rev, blocks: make(map[string]json.RawMessage, len(outcomes.Doc.Blocks))}
	for _, block := range outcomes.Doc.Blocks {
		var top struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal(block, &top); err != nil {
			return boardRead{}, err
		}
		read.blocks[top.ID] = block
	}
	return read, nil
}

func (c Cards) Depend(ctx context.Context, card, lane string, explicit []string) ([]string, error) {
	shown, onBoard, _, err := c.card(ctx, card)
	if err != nil {
		return nil, err
	}
	if !onBoard {
		return nil, fmt.Errorf("%s is not on board %s yet: push it, then run until again so its dependencies are read from what the board shows", card, c.Session)
	}
	rows, err := c.register(ctx)
	if err != nil {
		return nil, err
	}
	var terms []string
	for _, row := range rows {
		if row.Fields["status"] != inForce {
			continue
		}
		named, err := rulingTerms(row.Fields)
		if err != nil {
			return nil, err
		}
		terms = append(terms, named...)
	}
	deps, err := cardDeps(shown, terms, explicit)
	if err != nil {
		return nil, err
	}
	return deps, c.Store.PutCardDeps(store.CardRef{Session: c.Session, Card: card, Lane: lane}, deps)
}

type Recheck struct {
	Cards Cards
	Lane  string
	Judge Judge
}

func (r Recheck) Ruling(ctx context.Context, keys []string) ([]Moved, error) {
	rows, err := r.Cards.register(ctx)
	if err != nil {
		return nil, err
	}
	var deps, evidence []string
	for _, key := range keys {
		i := slices.IndexFunc(rows, func(row ledger.Row) bool { return row.Key == key })
		if i < 0 {
			return nil, fmt.Errorf("no register row %s", key)
		}
		fields := rows[i].Fields
		landed, err := landedWords(fields)
		if err != nil {
			return nil, err
		}
		named, err := rulingDeps(fields, landed)
		if err != nil {
			return nil, err
		}
		deps = append(deps, named...)
		evidence = append(evidence, fmt.Sprintf("His ruling, just recorded (%s), in his words: %s", fields["kind"], landed))
	}
	refs, err := r.Cards.Store.CardsDependingOn(deps)
	if err != nil {
		return nil, err
	}
	return r.move(ctx, refs, map[string]boardRead{}, strings.Join(evidence, "\n\n"), "his ruling "+strings.Join(keys, ", "))
}

func (r Recheck) Pick(ctx context.Context, card, said string) ([]Moved, error) {
	picked := store.CardRef{Session: r.Cards.Session, Card: card}
	deps, err := r.Cards.Store.CardDeps(picked)
	if err != nil {
		return nil, err
	}
	refs, err := r.Cards.Store.CardsDependingOn(append(deps, cardKey(card)))
	if err != nil {
		return nil, err
	}
	refs = slices.DeleteFunc(refs, func(ref store.CardRef) bool { return ref.Session == picked.Session && ref.Card == card })
	if len(refs) == 0 {
		return nil, nil
	}
	board, err := r.Cards.wholeBoard(ctx)
	if err != nil {
		return nil, err
	}
	answered, onBoard := board.blocks[card]
	if !onBoard {
		return nil, fmt.Errorf("%s is not on board %s, so the question he answered cannot be read", card, r.Cards.Session)
	}
	asked, err := questionOn(answered)
	if err != nil {
		return nil, err
	}
	return r.move(ctx, refs, map[string]boardRead{r.Cards.Session: board}, fmt.Sprintf("His pick, just made on another card of his board, answering %q: %s", asked, said), "his pick on "+card)
}

func questionOn(card json.RawMessage) (string, error) {
	var shown map[string]any
	if err := json.Unmarshal(card, &shown); err != nil {
		return "", err
	}
	asked := []string{text(shown, "title")}
	for _, choice := range typed(shown, "choice") {
		asked = append(asked, text(choice, "prompt"))
	}
	return strings.Join(slices.Compact(slices.DeleteFunc(asked, func(part string) bool { return part == "" })), " / "), nil
}

func (r Recheck) move(ctx context.Context, refs []store.CardRef, boards map[string]boardRead, evidence, cause string) ([]Moved, error) {
	for _, ref := range refs {
		if _, read := boards[ref.Session]; read {
			continue
		}
		cards := r.Cards
		cards.Session = ref.Session
		board, err := cards.wholeBoard(ctx)
		if err != nil {
			return nil, err
		}
		boards[ref.Session] = board
	}
	checked := make([]judged, len(refs))
	failed := make([]error, len(refs))
	var wg sync.WaitGroup
	for i, ref := range refs {
		wg.Go(func() { checked[i], failed[i] = r.judge(ctx, ref, boards[ref.Session], evidence) })
	}
	wg.Wait()
	if err := errors.Join(failed...); err != nil {
		return nil, err
	}
	var moved []Moved
	for _, one := range checked {
		done, err := r.apply(ctx, one)
		if err != nil {
			return nil, err
		}
		if done == nil {
			continue
		}
		if done.How != "redrew" && one.ref.Lane != "" {
			if err := r.Cards.Report(ctx, r.Lane, []string{one.ref.Lane}, one.ref.Card, done.laneReport(cause)); err != nil {
				return nil, err
			}
		}
		moved = append(moved, *done)
	}
	if len(moved) == 0 {
		return nil, nil
	}
	lines := make([]string, len(moved))
	for i, one := range moved {
		lines[i] = one.Line()
	}
	return moved, r.Cards.Report(ctx, r.Lane, []string{communicator}, movedTopic, fmt.Sprintf("%s moved %d card(s) on the owner's board: %s.", cause, len(moved), strings.Join(lines, "; ")))
}

type judged struct {
	ref     store.CardRef
	cards   Cards
	shown   json.RawMessage
	rev     int64
	gone    bool
	premise *Judgment
	decided *Judgment
}

func (r Recheck) judge(ctx context.Context, ref store.CardRef, board boardRead, evidence string) (judged, error) {
	cards := r.Cards
	cards.Session = ref.Session
	shown, onBoard := board.blocks[ref.Card]
	if !onBoard {
		return judged{ref: ref, cards: cards, gone: true}, nil
	}
	one := judged{ref: ref, cards: cards, shown: shown, rev: board.rev}
	var err error
	if one.premise, err = r.Judge(ctx, premiseKind, evidence, string(shown)); err != nil || verdictOf(one.premise) != "yes" {
		return one, err
	}
	if _, render := renderShown(shown); render {
		one.decided = &Judgment{Verdict: "no"}
		return one, nil
	}
	one.decided, err = r.Judge(ctx, decidedKind, evidence, string(shown))
	return one, err
}

func (r Recheck) apply(ctx context.Context, one judged) (*Moved, error) {
	if one.gone {
		return nil, one.cards.Store.DropCardDeps(one.ref)
	}
	switch verdictOf(one.premise) {
	case "no":
		return nil, nil
	case "unsure":
		return &Moved{one.ref, "flagged", sentenceOf(one.premise)}, nil
	}
	switch verdictOf(one.decided) {
	case "no":
		return r.redraw(ctx, one.cards, one.ref, one.shown, one.rev, one.premise.Sentence)
	}
	return &Moved{one.ref, "flagged", sentenceOf(one.decided)}, nil
}

func verdictOf(v *Judgment) string {
	if v == nil {
		return "unsure"
	}
	return v.Verdict
}

func sentenceOf(v *Judgment) string {
	if v == nil {
		return "the judge did not answer"
	}
	return v.Sentence
}

func (r Recheck) redraw(ctx context.Context, cards Cards, ref store.CardRef, shown json.RawMessage, rev int64, premise string) (*Moved, error) {
	var card map[string]any
	if err := json.Unmarshal(shown, &card); err != nil {
		return nil, err
	}
	note := map[string]any{"id": ref.Card + "-premise", "type": "markdown", "md": "**What changed since this card was drawn:** " + premise}
	children, _ := card["children"].([]any)
	children = slices.DeleteFunc(children, func(child any) bool {
		block, _ := child.(map[string]any)
		return block["id"] == note["id"]
	})
	card["children"] = append([]any{note}, children...)
	encoded, err := json.Marshal(card)
	if err != nil {
		return nil, err
	}
	if _, err := cards.run(ctx, shell.Command{Argv: []string{"cc-present", "update-block", "-", "--session", cards.Session, "--rev", strconv.FormatInt(rev, 10), "--lane", r.Lane, "--take-over"}, Stdin: string(encoded)}); err != nil {
		return refused(ref, fmt.Sprintf("redraw it to state %q", premise), err)
	}
	return &Moved{ref, "redrew", premise}, nil
}

func refused(ref store.CardRef, wanted string, err error) (*Moved, error) {
	var exit *shell.ExitError
	if !errors.As(err, &exit) {
		return nil, err
	}
	return &Moved{ref, "held", fmt.Sprintf("the judge would %s, but cc-present refused: %s", wanted, strings.TrimSpace(exit.Stderr))}, nil
}
