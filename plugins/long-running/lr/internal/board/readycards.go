package board

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	FactCardDrawn  = "card_drawn"
	staleDrawn     = "stale"
	FactTapCard    = "tap_card"
	Communicator   = communicator
	factChangeTold = "change_told"

	tapOrderKind    = "lr.tap"
	tapAnswerWindow = time.Hour
	landedKind      = "landed"
	landedReason    = "it landed, so nothing waits on your tap"

	slackMessageBlocks = 50
	listFrameBlocks    = 3
	listRowBlocks      = 3
	listRowsPerPost    = (slackMessageBlocks - listFrameBlocks) / listRowBlocks
	cardVisibleChars   = 9000
	listRowFrameChars  = 160
	listRowChars       = cardVisibleChars/listRowsPerPost - listRowFrameChars
	listTitle          = 90
	listPlan           = 90
	listBlocks         = 120
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
	newVersionAsks = "a new version asks again in its own card"
	redWhy         = "CI went red at its newest version and the lane that owns it was told; a new card asks once CI is green"
	exemptWhy      = "it lands on root's go with no tap, under the drive's standing rule for its class"
)

const listPostLimit = time.Minute

var (
	tapCardName = regexp.MustCompile(`^card-tap-(\d+)-[0-9a-f]+$`)
	alertLane   = regexp.MustCompile(`^(?:incident|outage|sentry-\d+|dd-\d+)-`)
)

type ReadyCards struct {
	Cards  Cards
	Repo   string
	Lane   string
	Exempt func() ([]string, error)
}

func (r ReadyCards) Pass(ctx context.Context) error {
	rows, err := r.Cards.Store.ReviewRows(r.Repo)
	if err != nil {
		return err
	}
	facts, err := r.Cards.Store.FactsAtRowHeads(r.Repo, rows)
	if err != nil {
		return err
	}
	if err := r.tellChangeOwed(ctx, rows, facts); err != nil {
		return err
	}
	if err := r.retireCards(ctx, rows, redWhy, func(row store.ReviewRow) bool {
		return row.CI == prrow.CIRed && !row.Tapped && row.State == prrow.StateOpen
	}); err != nil {
		return err
	}
	if err := r.retireCards(ctx, rows, exemptWhy, func(row store.ReviewRow) bool { return row.State == prrow.StateExempt }); err != nil {
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
	if drawable, err = r.stillAsked(ctx, drawable, facts); err != nil || len(drawable) == 0 {
		return err
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

// stillAsked reads GitHub and the bus rather than the rows, since a store that has not folded the merge or his tap yet still reads the row as waiting on him.
func (r ReadyCards) stillAsked(ctx context.Context, rows []store.ReviewRow, facts map[int]map[string]string) ([]store.ReviewRow, error) {
	numbers := make([]int, len(rows))
	for i, row := range rows {
		numbers[i] = row.Number
	}
	read, err := r.Cards.CCX.PRStatus(ctx, r.Repo, numbers)
	if err != nil {
		return nil, err
	}
	open := make(map[int]bool, len(read))
	for _, pr := range read {
		open[pr.Number] = pr.State == stateOpen
	}
	tapped, err := unansweredTaps(ctx, r.Cards.Bus, r.Repo, r.Cards.Now())
	if err != nil {
		return nil, err
	}
	exempt, err := r.Exempt()
	if err != nil {
		return nil, err
	}
	asked := rows[:0]
	for _, row := range rows {
		if !open[row.Number] || tapped[row.Number] {
			continue
		}
		carried, err := r.carriesHisTap(row, facts[row.Number])
		if err != nil {
			return nil, err
		}
		if carried {
			continue
		}
		graded, err := r.landsOnItsGrade(ctx, row, facts[row.Number], exempt)
		if err != nil {
			return nil, err
		}
		if !graded {
			asked = append(asked, row)
		}
	}
	return asked, nil
}

func (r ReadyCards) landsOnItsGrade(ctx context.Context, row store.ReviewRow, atHead map[string]string, exempt []string) (bool, error) {
	if atHead[tapFact] != "" || atHead[gates.StandingRefused] != "" {
		return false, nil
	}
	records, err := client.Tail(ctx, client.Query{Drive: r.Cards.Bus, PR: fmt.Sprintf("%s#%d", r.Repo, row.Number)})
	if err != nil {
		return false, err
	}
	return gates.ExemptGraded(gates.Claim{Repo: r.Repo, PR: row.Number, Head: row.Head}, gates.Read{Records: records, Exempt: exempt}), nil
}

func unansweredTaps(ctx context.Context, drive, repo string, now time.Time) (map[int]bool, error) {
	records, err := client.Tail(ctx, client.Query{Drive: drive, Since: now.Add(-tapAnswerWindow), Kinds: []string{ownerKind, tapOrderKind, bus.TapRefusedKind}})
	if err != nil {
		return nil, err
	}
	tapped := map[int]bool{}
	for _, record := range records {
		if tap, ordered := bus.OrderOf(record, repo); ordered && tap.Value == string(machine.TapTapped) {
			tapped[tap.PR] = true
		}
		if string(record.Kind) == ownerKind {
			continue
		}
		for _, pr := range record.Refs.PRs {
			delete(tapped, pr.Number)
		}
	}
	return tapped, nil
}

func (r ReadyCards) carriesHisTap(row store.ReviewRow, atHead map[string]string) (bool, error) {
	tapped, err := r.Cards.Store.StandingTap(r.Repo, row.Number)
	if err != nil || tapped == "" || strings.HasPrefix(row.Head, tapped) {
		return false, err
	}
	atTap, err := r.Cards.Store.Facts(r.Repo, row.Number, tapped)
	if err != nil {
		return false, err
	}
	deltas, err := r.Cards.Store.HeadFacts(r.Repo, row.Number, gates.DeltaFacts)
	if err != nil {
		return false, err
	}
	by, _ := gates.CarriedBy(tapped, row.Head, atTap, atHead, deltas)
	return by != "", nil
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

// tellChangeOwed asks each open row's lane, once per head, for the plain
// sentence its approval post shows as What changes when its Summary gives none.
func (r ReadyCards) tellChangeOwed(ctx context.Context, rows []store.ReviewRow, facts map[int]map[string]string) error {
	for _, row := range rows {
		flaw := prrow.ChangeFlaw(row.ForUsers)
		if flaw == "" || row.ReadHead == "" || row.State != prrow.StateOpen || row.Tapped || facts[row.Number][factChangeTold] != "" {
			continue
		}
		at := row.Head[:min(waitsSHA, len(row.Head))]
		text := fmt.Sprintf("#%d at %s: its approval post says only that you owe What changes, because %s. Open the PR body's Summary with one plain sentence on what a user sees change, with no plan text, record ids, hashes or exit codes; the plan goes on a Plan: line outside the Summary. The post reads the new body.", row.Number, at, flaw)
		if err := r.Cards.Report(ctx, r.Lane, []string{row.Lane}, fmt.Sprintf("change-words:%s#%d", r.Repo, row.Number), text); err != nil {
			return fmt.Errorf("#%d: %w", row.Number, err)
		}
		if _, err := r.Cards.Store.PutFact(r.Repo, row.Number, row.Head, factChangeTold, flaw, r.Cards.Now()); err != nil {
			return err
		}
	}
	return nil
}

func (r ReadyCards) retireCards(ctx context.Context, rows []store.ReviewRow, why string, retired func(store.ReviewRow) bool) error {
	drawn, err := r.Cards.Store.LatestFacts(r.Repo, FactTapCard)
	if err != nil {
		return err
	}
	due := slices.DeleteFunc(slices.Clone(rows), func(row store.ReviewRow) bool {
		return !retired(row) || !slices.Contains([]string{tapCardDrawn, tapCardOnBoard}, drawn[row.Number])
	})
	if len(due) == 0 {
		return nil
	}
	shown, err := r.Cards.tapCards(ctx)
	if err != nil {
		return err
	}
	var cards []string
	for _, row := range due {
		for _, card := range shown {
			if strings.HasPrefix(card.ID, gates.TapCardPrefix(row.Number)) || card.asksAt(row) {
				cards = append(cards, card.ID)
			}
		}
	}
	if err := r.Cards.Retire(ctx, r.Lane, why, cards, io.Discard); err != nil {
		return err
	}
	for _, row := range due {
		if _, err := r.Cards.Store.PutFact(r.Repo, row.Number, row.Head, FactTapCard, "", r.Cards.Now()); err != nil {
			return err
		}
	}
	return nil
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
			Change: clipWords(portOpening(r.Repo, row)+prrow.ChangeWords(row), listChange), Plan: clipWords(said.Said(ctx, PlanWords(row)), listPlan), Blocks: clipWords(prrow.BlocksWords(row), listBlocks),
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
	var replaced []string
	for _, card := range shown {
		if strings.HasPrefix(card.ID, gates.TapCardPrefix(row.Number)) && card.ID != tapCardID(row) {
			replaced = append(replaced, card.ID)
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
	if _, err := DrawCard(ctx, r.Cards.Bus, r.Lane, key, cardBody{Block: block, Until: KindPR + ":" + pr, TapOf: pr, TapHead: row.Head, Replaces: replaced}); err != nil {
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

func tapCardID(row store.ReviewRow) string {
	return gates.TapCardPrefix(row.Number) + row.Head[:min(waitsSHA, len(row.Head))]
}

func TapCard(repo string, row store.ReviewRow) (json.RawMessage, error) {
	id := tapCardID(row)
	url := fmt.Sprintf("https://github.com/%s/pull/%d", repo, row.Number)
	pr := Card(repo, row).PRs[0]
	pr.Port = ""
	return json.Marshal(map[string]any{
		"id": id, "type": "card", "title": fmt.Sprintf("Tap: #%d, %s", row.Number, row.Title),
		"summary": portOpening(repo, row) + fmt.Sprintf("[#%d](%s) at %s is green, approved and graded, and waits only on your tap. %s", row.Number, url, row.Head[:min(waitsSHA, len(row.Head))], strings.Join(strings.Fields(prrow.ChangeWords(row)), " ")),
		"children": []map[string]any{
			{"id": id + "-choice", "type": "choice", "options": []map[string]any{
				{"id": id + "-approve", "label": approveLabel, "recommended": true},
				{"id": id + "-hold", "label": holdLabel},
			}},
			FillPRChild(map[string]any{"id": id + "-pr", "type": "pr", "repo": repo, "number": row.Number}, pr),
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
	return c.ID == MergeWaits(row.Number, row.Head) || c.asks(row.Number, func(head string) bool { return head == "" || strings.HasPrefix(row.Head, head) })
}

func (c tapCard) asksFor(number int) bool {
	return strings.HasPrefix(c.ID, fmt.Sprintf("merge:%d:", number)) || c.asks(number, func(string) bool { return true })
}

func (c tapCard) asks(number int, at func(head string) bool) bool {
	offers, names := false, false
	for _, child := range c.Children {
		offers = offers || slices.ContainsFunc(child.Options, func(option cardOption) bool {
			return option.Label == approveLabel || option.Label == mergeLabel
		})
		names = names || child.Type == "pr" && child.Number == number && at(child.Head)
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
