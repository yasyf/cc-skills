package board

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	FactTapCard  = "tap_card"
	Communicator = communicator

	liveTapCards   = 10
	tapCardDrawn   = "drawn"
	tapCardOnBoard = "on the board"
	tapCardInSlack = "in slack"
	tapCardRefused = "refused"
	newVersionWhy  = "a new version replaced it, and its own card asks again"
)

var tapCardName = regexp.MustCompile(`^card-tap-(\d+)-[0-9a-f]+$`)

type ReadyCards struct {
	Cards Cards
	Repo  string
	Lane  string
	Named func(context.Context) (map[int]bool, error)
}

func (r ReadyCards) Pass(ctx context.Context) error {
	rows, err := r.Cards.Store.ReviewRows(r.Repo)
	if err != nil {
		return err
	}
	facts, err := r.Cards.Store.FactsAtRowHeads(r.Repo)
	if err != nil {
		return err
	}
	live, due := 0, []store.ReviewRow{}
	for _, row := range rows {
		if !waitsOnHim(row) || row.State != prrow.StateOpen {
			continue
		}
		switch facts[row.Number][FactTapCard] {
		case "":
			due = append(due, row)
		case tapCardRefused:
		default:
			live++
		}
	}
	if len(due) == 0 || live >= liveTapCards {
		return nil
	}
	shown, err := r.Cards.tapCards(ctx)
	if err != nil {
		return err
	}
	drawable := due[:0]
	for _, row := range due {
		if !slices.ContainsFunc(shown, func(card tapCard) bool { return card.asksAt(row) }) {
			drawable = append(drawable, row)
			continue
		}
		if err := r.mark(row, tapCardOnBoard); err != nil {
			return err
		}
		live++
	}
	if live >= liveTapCards || len(drawable) == 0 {
		return nil
	}
	named, err := r.Named(ctx)
	if err != nil {
		return err
	}
	_, known, err := r.Cards.rows(ctx)
	if err != nil {
		return err
	}
	inTapOrder(drawable, rows, named)
	for _, row := range drawable[:min(liveTapCards-live, len(drawable))] {
		if err := r.draw(ctx, row, shown, known[tapCardID(row)]["withdrawn"]); err != nil {
			return fmt.Errorf("#%d: %w", row.Number, err)
		}
	}
	return nil
}

func inTapOrder(due, rows []store.ReviewRow, named map[int]bool) {
	freed := make(map[int]int, len(due))
	for _, row := range due {
		freed[row.Number] = lanesFreed(row, rows)
	}
	slices.SortStableFunc(due, func(one, other store.ReviewRow) int {
		if named[one.Number] != named[other.Number] {
			if named[one.Number] {
				return -1
			}
			return 1
		}
		return cmp.Or(cmp.Compare(freed[other.Number], freed[one.Number]), cmp.Compare(one.Number, other.Number))
	})
}

func lanesFreed(row store.ReviewRow, rows []store.ReviewRow) int {
	lanes := map[string]bool{row.Lane: true}
	below := []int{row.Number}
	for len(below) > 0 {
		number := "#" + strconv.Itoa(below[0])
		below = below[1:]
		for _, above := range rows {
			if above.Parent == number && above.State == prrow.StateOpen {
				lanes[above.Lane] = true
				below = append(below, above.Number)
			}
		}
	}
	return len(lanes)
}

func (r ReadyCards) draw(ctx context.Context, row store.ReviewRow, shown []tapCard, withdrawn string) error {
	for _, card := range shown {
		if strings.HasPrefix(card.ID, tapCardPrefix(row.Number)) {
			if _, err := DrawCard(ctx, r.Cards.Bus, r.Lane, "tap-off-"+card.ID, cardBody{Remove: card.ID, Reason: newVersionWhy}); err != nil {
				return err
			}
		}
	}
	block, err := TapCard(r.Repo, row)
	if err != nil {
		return err
	}
	pr := fmt.Sprintf("%s#%d", r.Repo, row.Number)
	key := tapCardID(row)
	if withdrawn != "" {
		key += "@" + withdrawn
	}
	if _, err := DrawCard(ctx, r.Cards.Bus, r.Lane, key, cardBody{Block: block, Until: KindPR + ":" + pr, TapOf: pr, TapHead: row.Head}); err != nil {
		return err
	}
	return r.mark(row, tapCardDrawn)
}

func (r ReadyCards) mark(row store.ReviewRow, how string) error {
	_, err := r.Cards.Store.PutFact(r.Repo, row.Number, row.Head, FactTapCard, how, r.Cards.Now())
	return err
}

func (c Cards) freeTapRow(card string) error {
	named := tapCardName.FindStringSubmatch(card)
	if named == nil {
		return nil
	}
	number, err := strconv.Atoi(named[1])
	if err != nil {
		return err
	}
	rows, err := c.Store.NumberedReviewRows([]int{number})
	if err != nil {
		return err
	}
	for _, row := range rows {
		if tapCardID(row) != card {
			continue
		}
		stored, err := c.Store.Facts(row.Repo, row.Number, row.Head)
		if err != nil {
			return err
		}
		if stored[FactWithdrawn] != "" || stored[FactTapCard] == tapCardInSlack {
			continue
		}
		if err := c.Store.DeleteFact(row.Repo, row.Number, row.Head, FactTapCard); err != nil {
			return err
		}
	}
	return nil
}

func tapCardPrefix(number int) string {
	return fmt.Sprintf("card-tap-%d-", number)
}

func tapCardID(row store.ReviewRow) string {
	return tapCardPrefix(row.Number) + row.Head[:min(waitsSHA, len(row.Head))]
}

func TapCard(repo string, row store.ReviewRow) (json.RawMessage, error) {
	id := tapCardID(row)
	url := fmt.Sprintf("https://github.com/%s/pull/%d", repo, row.Number)
	return json.Marshal(map[string]any{
		"id": id, "type": "card", "title": fmt.Sprintf("Tap: #%d, %s", row.Number, row.Title),
		"summary": fmt.Sprintf("[#%d](%s) at %s is green, approved and graded, and waits only on your tap. %s", row.Number, url, row.Head[:min(waitsSHA, len(row.Head))], strings.Join(strings.Fields(row.ForUsers), " ")),
		"children": []map[string]any{
			{"id": id + "-choice", "type": "choice", "options": []map[string]any{
				{"id": id + "-approve", "label": approveLabel, "recommended": true},
				{"id": id + "-hold", "label": holdLabel},
			}},
			FillPRChild(map[string]any{"id": id + "-pr", "type": "pr", "repo": repo, "number": row.Number}, Card(repo, row).PRs[0]),
		},
	})
}

type cardOption struct {
	Label string `json:"label"`
}

type tapCard struct {
	ID       string `json:"id"`
	Type     string `json:"type"`
	Children []struct {
		Type    string       `json:"type"`
		Number  int          `json:"number"`
		Head    string       `json:"head"`
		Options []cardOption `json:"options"`
	} `json:"children"`
}

func (c tapCard) asksAt(row store.ReviewRow) bool {
	if c.ID == MergeWaits(row.Number, row.Head) {
		return true
	}
	offers, names := false, false
	for _, child := range c.Children {
		offers = offers || slices.ContainsFunc(child.Options, func(option cardOption) bool {
			return option.Label == approveLabel || option.Label == mergeLabel
		})
		names = names || child.Type == "pr" && child.Number == row.Number && (child.Head == "" || strings.HasPrefix(row.Head, child.Head))
	}
	return offers && names
}

func (c Cards) tapCards(ctx context.Context) ([]tapCard, error) {
	shown, err := c.present(ctx, "outcomes", "--all")
	if err != nil {
		return nil, err
	}
	var outcomes struct {
		Doc struct {
			Blocks []tapCard `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return nil, fmt.Errorf("cc-present outcomes: %w", err)
	}
	return slices.DeleteFunc(outcomes.Doc.Blocks, func(card tapCard) bool { return card.Type != "card" }), nil
}
