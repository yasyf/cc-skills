package board

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	FactCardDrawn = "card_drawn"
	staleDrawn    = "stale"
	FactTapCard   = "tap_card"
	Communicator  = communicator

	slackMessageBlocks = 50
	listFrameBlocks    = 3
	listRowBlocks      = 3
	listRowsPerPost    = (slackMessageBlocks - listFrameBlocks) / listRowBlocks
	cardVisibleChars   = 9000
	listRowFrameChars  = 160
	listRowChars       = cardVisibleChars/listRowsPerPost - listRowFrameChars
	listTitle          = 90
	listPlan           = 90
	listBlocks         = 60
	listChange         = listRowChars - listTitle - listPlan - listBlocks

	tapListNumber = 0
	tapListsHead  = "tap-lists"
	tapListNone   = "none"
	tapListPrefix = "tap-list-"
	onlyNoTap     = `["no tap"]`

	tapCardDrawn   = "drawn"
	tapCardOnBoard = "on the board"
	tapCardInSlack = "in slack"
	tapCardRefused = "refused"
	newVersionWhy  = "a new version replaced it, and its own card asks again"
)

const listPostLimit = time.Minute

var (
	tapCardName = regexp.MustCompile(`^card-tap-(\d+)-[0-9a-f]+$`)
	alertLane   = regexp.MustCompile(`^(?:incident|outage|sentry-\d+|dd-\d+)-`)
)

type ReadyCards struct {
	Cards Cards
	Repo  string
	Lane  string
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
	lists, err := r.tapLists()
	if err != nil {
		return err
	}
	if err := r.refreshLists(ctx, rows, facts, lists); err != nil {
		return err
	}
	followed, err := r.followDrawn(ctx, rows)
	if err != nil {
		return err
	}
	due := []store.ReviewRow{}
	for _, row := range rows {
		if waitsOnHim(row) && row.State == prrow.StateOpen && facts[row.Number][FactTapCard] == "" && !followed[row.Number] {
			due = append(due, row)
		}
	}
	if len(due) == 0 {
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
	}
	if len(drawable) == 0 {
		return nil
	}
	inTapOrder(drawable)
	if len(drawable) > 1 {
		var parked []store.ReviewRow
		parked, drawable = splitParked(drawable, facts)
		if err := r.list(ctx, parked, len(lists)); err != nil {
			return err
		}
	}
	_, known, err := r.Cards.rows(ctx)
	if err != nil {
		return err
	}
	for _, row := range drawable {
		if err := r.draw(ctx, row, shown, known[tapCardID(row)]["withdrawn"]); err != nil {
			return fmt.Errorf("#%d: %w", row.Number, err)
		}
	}
	return nil
}

// followDrawn redraws in place each post whose row now draws differently, and
// names the rows whose post stands, so a row turning ready draws no second one.
// A row whose post Slack no longer holds draws its own again.
func (r ReadyCards) followDrawn(ctx context.Context, rows []store.ReviewRow) (map[int]bool, error) {
	drawn, err := r.Cards.Store.LatestFacts(r.Repo, FactCardDrawn)
	if err != nil {
		return nil, err
	}
	followed := make(map[int]bool, len(drawn))
	for _, row := range rows {
		was := drawn[row.Number]
		if was == "" {
			continue
		}
		if was == drawnSignature(Card(r.Repo, row)) {
			followed[row.Number] = true
			continue
		}
		done, err := r.Cards.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "redraw-pr-cards", "--pr", fmt.Sprintf("https://github.com/%s/pull/%d", r.Repo, row.Number)}})
		if err != nil {
			return nil, fmt.Errorf("#%d: %w", row.Number, err)
		}
		var said struct {
			Redrawing int `json:"redrawing"`
		}
		if err := json.Unmarshal([]byte(done.Stdout), &said); err != nil {
			return nil, fmt.Errorf("#%d: cc-slack redraw-pr-cards said %q: %w", row.Number, done.Stdout, err)
		}
		if said.Redrawing > 0 {
			followed[row.Number] = true
			continue
		}
		if _, err := r.Cards.Store.PutFact(r.Repo, row.Number, row.Head, FactCardDrawn, "", r.Cards.Now()); err != nil {
			return nil, err
		}
	}
	return followed, nil
}

func inTapOrder(due []store.ReviewRow) {
	slices.SortStableFunc(due, func(one, other store.ReviewRow) int {
		if alertLane.MatchString(one.Lane) != alertLane.MatchString(other.Lane) {
			if alertLane.MatchString(one.Lane) {
				return -1
			}
			return 1
		}
		return cmp.Or(cmp.Compare(one.HeadAt, other.HeadAt), cmp.Compare(one.Number, other.Number))
	})
}

func splitParked(rows []store.ReviewRow, facts map[int]map[string]string) (parked, rest []store.ReviewRow) {
	for _, row := range rows {
		if facts[row.Number][gates.MissingFact] == onlyNoTap {
			parked = append(parked, row)
		} else {
			rest = append(rest, row)
		}
	}
	return parked, rest
}

func (r ReadyCards) tapLists() (map[string]string, error) {
	stored, err := r.Cards.Store.PRFacts(r.Repo, tapListNumber)
	if err != nil {
		return nil, err
	}
	maps.DeleteFunc(stored, func(kind, _ string) bool { return !strings.HasPrefix(kind, tapListPrefix) })
	return stored, nil
}

func (r ReadyCards) list(ctx context.Context, rows []store.ReviewRow, existing int) error {
	page := existing
	for chunk := range slices.Chunk(rows, listRowsPerPost) {
		page++
		name := listName(page)
		if err := r.sendList(ctx, name, chunk); err != nil {
			return err
		}
		for _, row := range chunk {
			if err := r.mark(row, name); err != nil {
				return err
			}
		}
	}
	return nil
}

func (r ReadyCards) refreshLists(ctx context.Context, rows []store.ReviewRow, facts map[int]map[string]string, lists map[string]string) error {
	for _, name := range slices.Sorted(maps.Keys(lists)) {
		held := slices.DeleteFunc(slices.Clone(rows), func(row store.ReviewRow) bool {
			return facts[row.Number][FactTapCard] != name || !waitsOnHim(row) || row.State != prrow.StateOpen
		})
		inTapOrder(held)
		if listKey(held) == lists[name] {
			continue
		}
		if err := r.sendList(ctx, name, held); err != nil {
			return err
		}
	}
	return nil
}

func (r ReadyCards) sendList(ctx context.Context, name string, rows []store.ReviewRow) error {
	card, err := json.Marshal(r.listCard(ctx, name, rows))
	if err != nil {
		return err
	}
	if _, err := r.Cards.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "dm-status", "--session", r.Cards.Session, "--card", string(card)}, Timeout: listPostLimit}); err != nil {
		return fmt.Errorf("%s: %w", name, err)
	}
	_, err = r.Cards.Store.PutFact(r.Repo, tapListNumber, tapListsHead, name, listKey(rows), r.Cards.Now())
	return err
}

func (r ReadyCards) listCard(ctx context.Context, name string, rows []store.ReviewRow) map[string]any {
	card := map[string]any{"status": "done", "title": "Nothing left on this list", "body": "Every PR it held was answered, landed or moved to a new version.", "group": name, "drive": r.Cards.Bus}
	if len(rows) == 0 {
		return card
	}
	said := NewAnswerWords(r.Cards.Runner, r.Cards.Checkout)
	listed := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		pr := SlackPRRow{
			URL: fmt.Sprintf("https://github.com/%s/pull/%d", r.Repo, row.Number), Title: clipWords(row.Title, listTitle), Head: row.Head,
			Change: clipWords(row.ForUsers, listChange), Plan: clipWords(said.Said(ctx, PlanWords(row)), listPlan), Blocks: clipWords(prrow.BlocksWords(row), listBlocks),
		}
		listed = append(listed, map[string]any{"id": MergeWaits(row.Number, row.Head), "buttons": []string{approveLabel, holdLabel}, "pr": pr, "pr_card": true})
	}
	card["status"], card["title"], card["rows"] = "needs_you", fmt.Sprintf("%d waiting only on your approval", len(rows)), listed
	card["body"] = "These were already waiting: incident fixes first, then oldest. Approve lands one, Hold keeps it out, and this post shrinks as you answer. The PR link has the whole story; a PR that turns ready from now on gets its own post."
	return card
}

func listName(page int) string {
	return tapListPrefix + strconv.Itoa(page)
}

func listKey(rows []store.ReviewRow) string {
	if len(rows) == 0 {
		return tapListNone
	}
	heads := make([]string, len(rows))
	for i, row := range rows {
		heads[i] = strconv.Itoa(row.Number) + "@" + row.Head
	}
	return strings.Join(heads, ",")
}

func clipWords(text string, limit int) string {
	words := strings.Join(strings.Fields(text), " ")
	runes := []rune(words)
	if len(runes) <= limit {
		return words
	}
	cut := string(runes[:limit])
	if space := strings.LastIndex(cut, " "); space > 0 {
		cut = cut[:space]
	}
	return strings.TrimRight(cut, " ,;:") + "…"
}

func (r ReadyCards) draw(ctx context.Context, row store.ReviewRow, shown []tapCard, withdrawn string) error {
	for _, card := range shown {
		if strings.HasPrefix(card.ID, tapCardPrefix(row.Number)) {
			if _, err := DrawCard(ctx, r.Cards.Bus, r.Lane, "tap-off-"+card.ID, cardBody{Remove: card.ID, Reason: newVersionWhy}); err != nil {
				return err
			}
		}
	}
	plan := NewAnswerWords(r.Cards.Runner, r.Cards.Checkout).Said(ctx, PlanWords(row))
	row.PlanLine, row.PlanRow = plan, plan
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
