// Package live reads what a card's bound block shows: a card stores a typed key
// or a query, and every surface gets the value from Read when it draws.
package live

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Kind string

const (
	KindPR       Kind = "pr"
	KindCard     Kind = "card"
	KindAsk      Kind = "ask"
	KindRecord   Kind = "record"
	KindLane     Kind = "lane"
	KindArtifact Kind = "artifact"
)

type View string

const (
	ViewEmbed View = "embed"
	ViewGroup View = "group"
	ViewTable View = "table"
	ViewCount View = "count"
)

const (
	SourcePRs   = "prs"
	SourceCards = "cards"
	SourceAsks  = "asks"
	SourceWaits = "waits"

	StateWaits    = "waits"
	StateBlocked  = "blocked"
	StateSettled  = "settled"
	StateGone     = "gone"
	waitsOnYou    = "you"
	ApproveAction = "Approve"
	HoldAction    = "Hold"
)

var (
	refShape    = regexp.MustCompile(`^(pr|card|ask|record|lane|artifact):(\S+)$`)
	prKey       = regexp.MustCompile(`^(?:([\w.-]+/[\w.-]+)#)?(\d+)$`)
	commonCells = []string{"title", "says", "state"}
	rowCells    = []string{"number", "lane", "change", "risk", "ci", "head"}
	askCells    = []string{"lane"}
	sourceWhere = map[string][]string{
		SourcePRs:   {"waits", "state", "lane"},
		SourceCards: {"state"},
		SourceAsks:  {"state", "lane", "surface"},
		SourceWaits: {"lane"},
	}
)

// Ref is a validated typed key such as pr:49940, pr:yasyf/cc-skills#3130,
// card:card-x or ask:ask/000036.
type Ref string

func ParseRef(text string) (Ref, error) {
	found := refShape.FindStringSubmatch(text)
	if found == nil {
		return "", fmt.Errorf("%q is no typed key: write pr:N, pr:owner/repo#N, card:ID, ask:KEY, record:SEQ, lane:NAME or artifact:REPLY/N", text)
	}
	if Kind(found[1]) == KindPR && !prKey.MatchString(found[2]) {
		return "", fmt.Errorf("%q names no pull request: write pr:N or pr:owner/repo#N", text)
	}
	return Ref(text), nil
}

func (r Ref) Kind() Kind {
	kind, _, _ := strings.Cut(string(r), ":")
	return Kind(kind)
}

func (r Ref) Key() string {
	_, key, _ := strings.Cut(string(r), ":")
	return key
}

// Binding is what a live block stores in place of content: Refs, or a Source
// with Where.
type Binding struct {
	View    View              `json:"view"`
	Refs    []Ref             `json:"refs,omitempty"`
	Source  string            `json:"source,omitempty"`
	Where   map[string]string `json:"where,omitempty"`
	Columns []string          `json:"columns,omitempty"`
	Limit   int               `json:"limit,omitempty"`
}

type Item struct {
	Ref     Ref               `json:"ref"`
	Title   string            `json:"title"`
	State   string            `json:"state"`
	Says    string            `json:"says"`
	Cells   map[string]string `json:"cells,omitempty"`
	Actions []string          `json:"actions,omitempty"`
}

// Result is one binding's value: Total counts what matched before Limit.
type Result struct {
	Items []Item `json:"items"`
	Total int    `json:"total"`
}

// Card is a board card as the board's daemon holds it; Picked is empty until he
// answers.
type Card struct {
	ID      string   `json:"id"`
	Title   string   `json:"title"`
	Picked  string   `json:"picked,omitempty"`
	Options []string `json:"options,omitempty"`
}

// Sources are the stores Read draws on. Each runs at most once in a Read,
// however many bindings or rows ask for it. LeftBoard is asked only for cards
// the board no longer holds, all at once.
type Sources struct {
	Repo      string
	Cards     []Card
	Rows      func(repo string) ([]store.ReviewRow, error)
	Book      func() (asks.Book, error)
	LeftBoard func(ids []string) (map[string]Card, error)
	Now       func() time.Time
}

// Check refuses what no draw can repair: a bad view, source, where key or
// column, or a binding naming both or neither of Refs and Source.
func Check(b Binding) error {
	if !slices.Contains([]View{ViewEmbed, ViewGroup, ViewTable, ViewCount}, b.View) {
		return fmt.Errorf("no view %q: use embed, group, table or count", b.View)
	}
	if (len(b.Refs) == 0) == (b.Source == "") {
		return fmt.Errorf("a %s block names its keys or one source, not both or neither", b.View)
	}
	for _, ref := range b.Refs {
		if _, err := ParseRef(string(ref)); err != nil {
			return err
		}
		if !slices.Contains([]Kind{KindPR, KindCard, KindAsk}, ref.Kind()) {
			return fmt.Errorf("%s is a link target and draws no row", ref)
		}
	}
	if b.Source != "" {
		known, found := sourceWhere[b.Source]
		if !found {
			return fmt.Errorf("no source %q: use prs, cards, asks or waits", b.Source)
		}
		for name := range b.Where {
			if !slices.Contains(known, name) {
				return fmt.Errorf("source %s filters by %s, not %q", b.Source, strings.Join(known, ", "), name)
			}
		}
	}
	for _, column := range b.Columns {
		if !slices.Contains(slices.Concat(commonCells, rowCells, askCells), column) {
			return fmt.Errorf("no column %q: use %s", column, strings.Join(slices.Concat(commonCells, rowCells), ", "))
		}
	}
	return nil
}

// Read resolves every binding in one pass.
func Read(src Sources, bindings []Binding) ([]Result, error) {
	r := reader{src: src, rows: map[string]map[int]store.ReviewRow{}, cards: map[string]Card{}}
	for _, card := range src.Cards {
		r.cards[card.ID] = card
	}
	if err := r.loadLeft(bindings); err != nil {
		return nil, err
	}
	results := make([]Result, len(bindings))
	for i, binding := range bindings {
		if err := Check(binding); err != nil {
			return nil, err
		}
		items, err := r.items(binding)
		if err != nil {
			return nil, err
		}
		slices.SortStableFunc(items, func(one, other Item) int { return rank(one.State) - rank(other.State) })
		results[i] = Result{Items: items, Total: len(items)}
		if binding.Limit > 0 {
			results[i].Items = items[:min(binding.Limit, len(items))]
		}
	}
	return results, nil
}

func rank(state string) int {
	return slices.Index([]string{StateWaits, StateBlocked, StateSettled, StateGone}, state)
}

type reader struct {
	src   Sources
	rows  map[string]map[int]store.ReviewRow
	cards map[string]Card
	book  *asks.Book
}

func (r *reader) loadLeft(bindings []Binding) error {
	var missing []string
	for _, binding := range bindings {
		for _, ref := range binding.Refs {
			if _, held := r.cards[ref.Key()]; ref.Kind() == KindCard && !held && !slices.Contains(missing, ref.Key()) {
				missing = append(missing, ref.Key())
			}
		}
	}
	if len(missing) == 0 {
		return nil
	}
	left, err := r.src.LeftBoard(missing)
	if err != nil {
		return err
	}
	for id, card := range left {
		r.cards[id] = card
	}
	return nil
}

func (r *reader) items(b Binding) ([]Item, error) {
	if b.Source == "" {
		items := make([]Item, len(b.Refs))
		for i, ref := range b.Refs {
			item, err := r.one(ref)
			if err != nil {
				return nil, err
			}
			items[i] = item
		}
		return items, nil
	}
	switch b.Source {
	case SourcePRs:
		return r.queryRows(b.Where)
	case SourceCards:
		return r.queryCards(b.Where), nil
	case SourceAsks:
		return r.queryAsks(b.Where)
	}
	waiting, err := r.queryRows(map[string]string{"waits": waitsOnYou, "lane": b.Where["lane"]})
	if err != nil {
		return nil, err
	}
	if b.Where["lane"] != "" {
		return waiting, nil
	}
	return append(waiting, r.queryCards(map[string]string{"state": StateWaits})...), nil
}

func (r *reader) one(ref Ref) (Item, error) {
	switch ref.Kind() {
	case KindCard:
		card, held := r.cards[ref.Key()]
		if !held {
			return Item{Ref: ref, Title: ref.Key(), State: StateGone, Says: "No longer on your board"}, nil
		}
		return cardItem(card), nil
	case KindAsk:
		return r.askItem(ref)
	}
	found := prKey.FindStringSubmatch(ref.Key())
	repo := found[1]
	if repo == "" {
		repo = r.src.Repo
	}
	number, err := strconv.Atoi(found[2])
	if err != nil {
		return Item{}, err
	}
	rows, err := r.rowsOf(repo)
	if err != nil {
		return Item{}, err
	}
	row, held := rows[number]
	if !held {
		return Item{Ref: ref, Title: "#" + found[2], State: StateGone, Says: "No row in the PR ledger"}, nil
	}
	item := rowItem(row)
	item.Ref = ref
	return item, nil
}

func (r *reader) rowsOf(repo string) (map[int]store.ReviewRow, error) {
	if rows, read := r.rows[repo]; read {
		return rows, nil
	}
	listed, err := r.src.Rows(repo)
	if err != nil {
		return nil, err
	}
	rows := make(map[int]store.ReviewRow, len(listed))
	for _, row := range listed {
		rows[row.Number] = row
	}
	r.rows[repo] = rows
	return rows, nil
}

func (r *reader) queryRows(where map[string]string) ([]Item, error) {
	rows, err := r.rowsOf(r.src.Repo)
	if err != nil {
		return nil, err
	}
	numbers := make([]int, 0, len(rows))
	for number := range rows {
		numbers = append(numbers, number)
	}
	slices.Sort(numbers)
	var items []Item
	for _, number := range numbers {
		row := rows[number]
		if terminal(row) && where["state"] == "" {
			continue
		}
		if matches(row, where) {
			items = append(items, rowItem(row))
		}
	}
	return items, nil
}

func matches(row store.ReviewRow, where map[string]string) bool {
	return (where["waits"] == "" || where["waits"] == waitsOnYou && waitsOnHim(row)) &&
		(where["state"] == "" || where["state"] == row.State) &&
		(where["lane"] == "" || where["lane"] == row.Lane)
}

func (r *reader) queryCards(where map[string]string) []Item {
	var items []Item
	for _, card := range r.src.Cards {
		item := cardItem(card)
		if where["state"] == "" || where["state"] == item.State {
			items = append(items, item)
		}
	}
	return items
}

func (r *reader) bookOf() (asks.Book, error) {
	if r.book != nil {
		return *r.book, nil
	}
	book, err := r.src.Book()
	if err != nil {
		return asks.Book{}, err
	}
	r.book = &book
	return book, nil
}

func (r *reader) askItem(ref Ref) (Item, error) {
	book, err := r.bookOf()
	if err != nil {
		return Item{}, err
	}
	at := slices.IndexFunc(book.Asks, func(ask asks.Ask) bool { return ask.Key == ref.Key() })
	if at < 0 {
		return Item{Ref: ref, Title: ref.Key(), State: StateGone, Says: "No such ask"}, nil
	}
	return r.src.graded(book, book.Asks[at]), nil
}

func (r *reader) queryAsks(where map[string]string) ([]Item, error) {
	book, err := r.bookOf()
	if err != nil {
		return nil, err
	}
	var items []Item
	for _, ask := range book.Asks {
		item := r.src.graded(book, ask)
		if (where["state"] == "" || where["state"] == string(book.State(ask, r.src.Now()))) &&
			(where["lane"] == "" || where["lane"] == ask.Lane) &&
			(where["surface"] == "" || where["surface"] == ask.Surface) {
			items = append(items, item)
		}
	}
	return items, nil
}

func (s Sources) graded(book asks.Book, ask asks.Ask) Item {
	state := book.State(ask, s.Now())
	item := Item{Ref: Ref("ask:" + ask.Key), Title: ask.Text, State: StateBlocked, Says: strings.ToLower(string(state)), Cells: map[string]string{"lane": ask.Lane}}
	if state == asks.Answered || state == asks.Dropped || state == asks.Live {
		item.State = StateSettled
	}
	return item
}

func waitsOnHim(row store.ReviewRow) bool {
	return row.ReadyForTap && !row.Tapped
}

func terminal(row store.ReviewRow) bool {
	return prrow.Terminal(row.State) || row.State == prrow.StateSuperseded
}

func rowItem(row store.ReviewRow) Item {
	item := Item{
		Ref: Ref(fmt.Sprintf("pr:%s#%d", row.Repo, row.Number)), Title: row.Title, State: StateBlocked, Says: prrow.BlocksWords(row), Actions: []string{ApproveAction, HoldAction},
		Cells: map[string]string{"number": strconv.Itoa(row.Number), "lane": row.Lane, "change": row.ForUsers, "risk": row.Risk, "ci": row.CI, "head": row.Head},
	}
	switch {
	case terminal(row):
		item.State, item.Says, item.Actions = StateSettled, strings.ToUpper(row.State[:1])+row.State[1:], nil
	case row.Tapped:
		item.State, item.Says, item.Actions = StateSettled, "You approved it", nil
	case waitsOnHim(row):
		item.State, item.Says = StateWaits, "Your approval"
	}
	return item
}

func cardItem(card Card) Item {
	item := Item{Ref: Ref("card:" + card.ID), Title: card.Title, State: StateWaits, Says: "Your answer", Actions: card.Options}
	if card.Picked != "" {
		item.State, item.Says, item.Actions = StateSettled, "You picked "+card.Picked, nil
	}
	return item
}
