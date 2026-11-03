package landing

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	SlackCardFact = "slack-card"
	TapRowFact    = "tap-card-row"

	tapRowOn       = "on"
	tapRowOff      = "off"
	tapCursor      = "tap-cards"
	tapCardsAttr   = "cards"
	tapFirstRead   = 24 * time.Hour
	tapCardDue     = 3 * time.Minute
	tapNoCard      = "no slack card"
	tapHeadChars   = 7
	boardWatchLane = "board-watch"
	boardWriteOp   = "cards"
	boardWrites    = "board-write"
	appliedKind    = "applied"
	ccPresent      = "cc-present"
)

type TapHead struct {
	PR   int    `json:"pr"`
	Head string `json:"head"`
}

type BoundRow struct {
	PR     int    `json:"pr"`
	Head   string `json:"head"`
	Block  string `json:"block"`
	Thread string `json:"thread"`
}

type TapAsk struct {
	Session string     `json:"session"`
	Waiting []TapHead  `json:"waiting"`
	Tapped  []int      `json:"tapped"`
	Bound   []BoundRow `json:"bound"`
}

type TapParked struct {
	PR    int    `json:"pr"`
	Head  string `json:"head"`
	Below int    `json:"below"`
}

type TapReport struct {
	Waiting int         `json:"waiting"`
	Drew    int         `json:"drew"`
	Parked  []TapParked `json:"parked"`
	Missing []TapHead   `json:"missing"`
}

type TapDrawn struct {
	Seq   int64
	Links []board.TapLink
}

type TapBus interface {
	Drawn(ctx context.Context, after int64, since time.Time) ([]TapDrawn, error)
	Write(ctx context.Context, session string, card board.TapCard) error
	Blocker(ctx context.Context, head TapHead, text string) error
}

type Tapper struct {
	Store  *store.Store
	Bus    TapBus
	Pulls  func(ctx context.Context) ([]board.Pull, error)
	Repo   string
	Thread string
}

func (t Tapper) Ask(ctx context.Context, ask TapAsk, now time.Time) (TapReport, error) {
	report := TapReport{Waiting: len(ask.Waiting), Parked: []TapParked{}, Missing: []TapHead{}}
	if err := t.foldDrawn(ctx, now); err != nil {
		return report, err
	}
	wanted, err := t.unbound(ask, now)
	if err != nil {
		return report, err
	}
	on, err := t.rowsOn()
	if err != nil {
		return report, err
	}
	drawn := wanted
	if !sameHeads(wanted, on) {
		if drawn, report.Parked, err = t.redraw(ctx, ask, wanted, on, now); err != nil {
			return report, err
		}
	}
	report.Drew = len(drawn)
	report.Missing, err = t.raiseMissing(ctx, drawn, on, now)
	return report, err
}

func (t Tapper) foldDrawn(ctx context.Context, now time.Time) error {
	cursor, err := t.Store.Cursor(tapCursor)
	if err != nil {
		return err
	}
	records, err := t.Bus.Drawn(ctx, cursor, now.Add(-tapFirstRead))
	if err != nil {
		return err
	}
	for _, record := range records {
		for _, link := range record.Links {
			if _, err := t.Store.PutFact(t.Repo, link.PR, link.Head, SlackCardFact, link.Permalink, now); err != nil {
				return err
			}
		}
		if err := t.Store.Advance(tapCursor, record.Seq); err != nil {
			return err
		}
	}
	return nil
}

func (t Tapper) unbound(ask TapAsk, now time.Time) ([]TapHead, error) {
	asked := make(map[TapHead]bool, len(ask.Bound))
	for _, row := range ask.Bound {
		if strings.HasPrefix(row.Block, board.TapBlockID) {
			continue
		}
		if _, err := t.Store.PutFact(t.Repo, row.PR, row.Head, SlackCardFact, "thread "+row.Thread, now); err != nil {
			return nil, err
		}
		asked[TapHead{row.PR, row.Head}] = true
	}
	wanted := make([]TapHead, 0, len(ask.Waiting))
	for _, head := range ask.Waiting {
		if !asked[head] {
			wanted = append(wanted, head)
		}
	}
	return wanted, nil
}

func (t Tapper) rowsOn() (map[TapHead]time.Time, error) {
	rows, err := t.Store.FactsOfKind(t.Repo, TapRowFact)
	if err != nil {
		return nil, err
	}
	on := make(map[TapHead]time.Time, len(rows))
	for _, row := range rows {
		if row.Value == tapRowOn {
			on[TapHead{row.Number, row.Head}] = row.Posted
		}
	}
	return on, nil
}

func sameHeads(heads []TapHead, on map[TapHead]time.Time) bool {
	if len(heads) != len(on) {
		return false
	}
	for _, head := range heads {
		if _, found := on[head]; !found {
			return false
		}
	}
	return true
}

func (t Tapper) redraw(ctx context.Context, ask TapAsk, wanted []TapHead, on map[TapHead]time.Time, now time.Time) ([]TapHead, []TapParked, error) {
	pulls, err := t.Pulls(ctx)
	if err != nil {
		return nil, nil, err
	}
	numbers := make(map[int]board.Pull, len(pulls))
	branches := make(map[string]int, len(pulls))
	for _, pull := range pulls {
		numbers[pull.Number] = pull
		branches[pull.Branch] = pull.Number
	}
	landable := make(map[int]bool, len(ask.Waiting)+len(ask.Tapped))
	for _, head := range ask.Waiting {
		landable[head.PR] = true
	}
	for _, number := range ask.Tapped {
		landable[number] = true
	}
	drawn := make([]TapHead, 0, len(wanted))
	waits := make([]board.TapWait, 0, len(wanted))
	parked := []TapParked{}
	for _, head := range wanted {
		pull, open := numbers[head.PR]
		if !open || !sameCommit(pull.Head, head.Head) {
			continue
		}
		if stuck := stuckBelow(pull, numbers, branches, landable); stuck != 0 {
			parked = append(parked, TapParked{head.PR, head.Head, stuck})
			continue
		}
		drawn = append(drawn, head)
		waits = append(waits, board.TapWait{Pull: pull, Head: head.Head, Below: branches[pull.Base]})
	}
	if sameHeads(drawn, on) {
		return drawn, parked, nil
	}
	if err := t.Bus.Write(ctx, ask.Session, board.BuildTapCard(t.Repo, t.Thread, waits)); err != nil {
		return nil, nil, err
	}
	return drawn, parked, t.markRows(drawn, on, now)
}

func sameCommit(one, other string) bool {
	return strings.HasPrefix(one, other) || strings.HasPrefix(other, one)
}

func stuckBelow(pull board.Pull, numbers map[int]board.Pull, branches map[string]int, landable map[int]bool) int {
	for below := branches[pull.Base]; below != 0; below = branches[numbers[below].Base] {
		if !landable[below] {
			return below
		}
	}
	return 0
}

func (t Tapper) markRows(drawn []TapHead, on map[TapHead]time.Time, now time.Time) error {
	kept := make(map[TapHead]bool, len(drawn))
	for _, head := range drawn {
		kept[head] = true
		if _, found := on[head]; found {
			continue
		}
		if _, err := t.Store.PutFact(t.Repo, head.PR, head.Head, TapRowFact, tapRowOn, now); err != nil {
			return err
		}
		on[head] = now
	}
	for head := range on {
		if kept[head] {
			continue
		}
		if _, err := t.Store.PutFact(t.Repo, head.PR, head.Head, TapRowFact, tapRowOff, now); err != nil {
			return err
		}
		delete(on, head)
	}
	return nil
}

func (t Tapper) raiseMissing(ctx context.Context, drawn []TapHead, on map[TapHead]time.Time, now time.Time) ([]TapHead, error) {
	cards, err := t.Store.FactsOfKind(t.Repo, SlackCardFact)
	if err != nil {
		return nil, err
	}
	carded := make(map[TapHead]bool, len(cards))
	for _, card := range cards {
		carded[TapHead{card.Number, card.Head}] = true
	}
	missing := []TapHead{}
	for _, head := range drawn {
		if carded[head] || now.Sub(on[head]) < tapCardDue {
			continue
		}
		missing = append(missing, head)
		if err := t.raise(ctx, head, on[head], now); err != nil {
			return missing, err
		}
	}
	return missing, nil
}

func (t Tapper) raise(ctx context.Context, head TapHead, since, now time.Time) error {
	subject := fmt.Sprintf("tap-card:%d@%s", head.PR, head.Head)
	raised, err := t.Store.Escalations(subject)
	if err != nil || len(raised) > 0 {
		return err
	}
	short := head.Head[:min(tapHeadChars, len(head.Head))]
	text := fmt.Sprintf("#%d at %s waits on the owner's tap with no Slack card: the row went up %.0f minutes ago, and `lr landing draw-taps` on the board's machine has recorded no card for this head, so he cannot tap it", head.PR, short, now.Sub(since).Minutes())
	if err := t.Bus.Blocker(ctx, head, text); err != nil {
		return err
	}
	_, err = t.Store.Escalate(subject, tapNoCard, string(machine.MoverRoot), now)
	return err
}

type tapInbox struct {
	drive string
}

func (b tapInbox) Drawn(ctx context.Context, after int64, since time.Time) ([]TapDrawn, error) {
	query := client.Query{Drive: b.drive, After: after, Kinds: []string{appliedKind}}
	if after == 0 {
		query.Since = since
	}
	records, err := client.Tail(ctx, query)
	if err != nil {
		return nil, err
	}
	drawn := make([]TapDrawn, 0, len(records))
	for _, record := range records {
		links, err := tapLinks(record)
		if err != nil {
			return nil, fmt.Errorf("applied record #%d: %w", record.Seq, err)
		}
		drawn = append(drawn, TapDrawn{record.Seq, links})
	}
	return drawn, nil
}

func tapLinks(record client.Record) ([]board.TapLink, error) {
	raw, carried := record.Fields.Attrs[tapCardsAttr]
	if !carried {
		return nil, nil
	}
	var links []board.TapLink
	err := json.Unmarshal([]byte(raw), &links)
	return links, err
}

func (b tapInbox) Write(ctx context.Context, session string, card board.TapCard) error {
	raw, err := json.Marshal(card)
	if err != nil {
		return err
	}
	_, err = client.Post(ctx, client.Draft{
		Drive: b.drive, Lane: deskRunner, Kind: "note", Topic: boardWrites, To: []string{boardWatchLane},
		Text:  "board write: draw the card of PRs waiting on the owner's tap",
		Attrs: map[string]string{"op": boardWriteOp, "session": session, "block": board.TapBlockID, "card": string(raw)},
	})
	return err
}

func (b tapInbox) Blocker(ctx context.Context, head TapHead, text string) error {
	_, err := client.Post(ctx, client.Draft{
		Drive: b.drive, Lane: deskRunner, Kind: "blocker", To: []string{string(machine.MoverRoot)}, Stamp: true,
		ID: fmt.Sprintf("tap-card-missing:%d:%s", head.PR, head.Head), PRs: []string{strconv.Itoa(head.PR)}, Text: text,
	})
	return err
}

func DrawTaps(ctx context.Context, runner shell.Runner, session string, card board.TapCard) ([]board.TapLink, string, error) {
	if len(card.Children) == 0 {
		_, err := runner.Run(ctx, shell.Command{Argv: []string{ccPresent, "remove-block", board.TapCardID, "--session", session}})
		return nil, "no PR waits on a tap; the card is down", err
	}
	raw, err := json.Marshal(card)
	if err != nil {
		return nil, "", err
	}
	result, err := runner.Run(ctx, shell.Command{Argv: []string{ccPresent, "update-block", "-", "--round", "current", "--session", session}, Stdin: string(raw)})
	links := board.TapLinks(card, result.Stdout)
	return links, fmt.Sprintf("drew %d rows, %d with a Slack card", rowsOf(card), len(links)), err
}

func rowsOf(card board.TapCard) int {
	rows := 0
	for _, block := range card.Children {
		for _, group := range block.Groups {
			rows += len(group.PRs)
		}
	}
	return rows
}
