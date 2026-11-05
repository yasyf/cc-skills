// Package findings keeps every sweep finding and every owner pick on a card in
// one cc-notes ledger per drive, live until a record shows it carried out.
package findings

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	stateFile  = "findings.json"
	firstSince = 12 * time.Hour
	blockLabel = "board-block:"
	cardLabel  = "board-card:"
)

type Source interface {
	Tail(ctx context.Context, q client.Query) ([]client.Record, error)
	Among(ctx context.Context, drive string, seqs []int64) ([]client.Record, error)
}

type liveBus struct{}

func (liveBus) Tail(ctx context.Context, q client.Query) ([]client.Record, error) {
	return client.Tail(ctx, q)
}

func (liveBus) Among(ctx context.Context, drive string, seqs []int64) ([]client.Record, error) {
	return client.Among(ctx, drive, seqs)
}

type Book struct {
	Entry  drive.Entry
	Runner shell.Runner
	Store  ledger.Store
	Bus    Source
	Now    func() time.Time
}

func New(a *app.App, entry drive.Entry) Book {
	return Book{entry, a.Runner, ledger.CLI{Runner: a.Runner, Repo: entry.Checkout}, liveBus{}, a.Now}
}

type saved struct {
	Ledger string `json:"ledger"`
	Cursor int64  `json:"cursor,omitempty"`
}

func (b Book) path() string {
	return filepath.Join(b.Entry.StateDir, stateFile)
}

func (b Book) load() (saved, error) {
	data, err := os.ReadFile(b.path())
	if errors.Is(err, fs.ErrNotExist) {
		return saved{}, nil
	}
	if err != nil {
		return saved{}, err
	}
	var state saved
	if err := json.Unmarshal(data, &state); err != nil {
		return saved{}, fmt.Errorf("%s: %w", b.path(), err)
	}
	return state, nil
}

func (b Book) save(state saved) error {
	data, err := json.Marshal(state)
	if err != nil {
		return err
	}
	staged := fmt.Sprintf("%s.%d", b.path(), os.Getpid())
	if err := os.WriteFile(staged, data, 0o600); err != nil {
		return err
	}
	return os.Rename(staged, b.path())
}

func (b Book) ccn(ctx context.Context, argv ...string) (string, error) {
	result, err := b.Runner.Run(ctx, shell.Command{Argv: append([]string{"ccn", "-R", b.Entry.Checkout}, argv...)})
	if err != nil {
		return "", err
	}
	return result.Stdout, nil
}

func (b Book) ledger(ctx context.Context, state *saved) (string, error) {
	if state.Ledger != "" {
		return state.Ledger, nil
	}
	out, err := b.ccn(ctx, "ledger", "add", "findings: "+b.Entry.Bus(), "--json")
	if err != nil {
		return "", err
	}
	var made struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(out), &made); err != nil {
		return "", fmt.Errorf("ccn ledger add: %w", err)
	}
	state.Ledger = made.ID
	return made.ID, b.save(*state)
}

func (b Book) rows(ctx context.Context, id string) (Rows, error) {
	stored, err := b.Store.Rows(ctx, id)
	if err != nil {
		return nil, err
	}
	rows := make(Rows, len(stored))
	for _, row := range stored {
		rows[row.Key] = row.Fields
	}
	return rows, nil
}

func (b Book) write(ctx context.Context, id string, rows Rows, keys []string) error {
	if len(keys) == 0 {
		return nil
	}
	changed := make([]ledger.Row, len(keys))
	for i, key := range keys {
		changed[i] = ledger.Row{Key: key, Fields: rows[key]}
	}
	return b.Store.Sync(ctx, id, changed)
}

// names reads the one answer list and owner ledger a pass resolves every row
// against. An answer labels its box and card, which share no reliable spelling.
func (b Book) names(ctx context.Context) (Names, error) {
	out, err := b.ccn(ctx, "answer", "list", "--label", "board-answer", "--limit", "0", "--json")
	if err != nil {
		return Names{}, err
	}
	var answers []struct {
		Tags []string `json:"tags"`
	}
	if err := json.Unmarshal([]byte(out), &answers); err != nil {
		return Names{}, fmt.Errorf("ccn answer list: %w", err)
	}
	names := Names{map[string]string{}, map[string]string{}}
	for _, answer := range answers {
		var box, card string
		for _, tag := range answer.Tags {
			box = cmp.Or(box, trimmed(tag, blockLabel))
			card = cmp.Or(card, trimmed(tag, cardLabel))
		}
		if box != "" {
			names.Cards[box] = card
		}
	}
	ledgerText, err := os.ReadFile(owner.Ledger(b.Entry.StateDir))
	if errors.Is(err, fs.ErrNotExist) {
		return names, nil
	}
	if err != nil {
		return Names{}, err
	}
	for _, row := range owner.Parse(ledgerText) {
		if box := BoxOfOrder(row); box != "" {
			names.Boxes[row.ID] = box
		}
	}
	return names, nil
}

func trimmed(tag, label string) string {
	if rest, ok := strings.CutPrefix(tag, label); ok {
		return rest
	}
	return ""
}

func (b Book) query(cursor int64, since string) (client.Query, error) {
	q := client.Query{Drive: b.Entry.Bus()}
	switch {
	case since != "":
		if seq, err := strconv.ParseInt(strings.TrimPrefix(since, "#"), 10, 64); err == nil {
			q.After = seq
			return q, nil
		}
		window, err := time.ParseDuration(since)
		if err != nil {
			return q, fmt.Errorf("--since %q is neither a seq nor a duration", since)
		}
		q.Since = b.Now().Add(-window)
	case cursor > 0:
		q.After = cursor
	default:
		q.Since = b.Now().Add(-firstSince)
	}
	return q, nil
}

// Sync reads the records after the cursor once, or after since when given, adds
// each new finding and pick as a row and settles every live row against them.
func (b Book) Sync(ctx context.Context, since string) (Rows, error) {
	state, err := b.load()
	if err != nil {
		return nil, err
	}
	id, err := b.ledger(ctx, &state)
	if err != nil {
		return nil, err
	}
	rows, err := b.rows(ctx, id)
	if err != nil {
		return nil, err
	}
	q, err := b.query(state.Cursor, since)
	if err != nil {
		return nil, err
	}
	records, err := b.Bus.Tail(ctx, q)
	if err != nil || len(records) == 0 {
		return rows, err
	}
	names, err := b.names(ctx)
	if err != nil {
		return nil, err
	}
	if err := b.write(ctx, id, rows, settle(rows, records, state.Cursor, names)); err != nil {
		return nil, err
	}
	state.Cursor = max(state.Cursor, records[len(records)-1].Seq)
	return rows, b.save(state)
}

func (b Book) Pass(ctx context.Context) error {
	_, err := b.Sync(ctx, "")
	return err
}

// Track adds the named records, such as a pick from before the ledger began, and
// settles them against the records after the oldest.
func (b Book) Track(ctx context.Context, seqs []int64) error {
	state, err := b.load()
	if err != nil {
		return err
	}
	id, err := b.ledger(ctx, &state)
	if err != nil {
		return err
	}
	named, err := b.Bus.Among(ctx, b.Entry.Bus(), seqs)
	if err != nil {
		return err
	}
	rows := Rows{}
	for _, seq := range seqs {
		i := slices.IndexFunc(named, func(record client.Record) bool { return record.Seq == seq })
		if i < 0 {
			return fmt.Errorf("findings: no record #%d", seq)
		}
		key, row, ok := rowOf(named[i])
		if !ok {
			return fmt.Errorf("findings: #%d is neither a [finding:<lane>] defect nor an owner pick on a board card", seq)
		}
		rows[key] = row
	}
	later, err := b.Bus.Tail(ctx, client.Query{Drive: b.Entry.Bus(), After: slices.Min(seqs)})
	if err != nil {
		return err
	}
	names, err := b.names(ctx)
	if err != nil {
		return err
	}
	settle(rows, later, math.MaxInt64, names)
	keys := make([]string, 0, len(rows))
	for key := range rows {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	return b.write(ctx, id, rows, keys)
}

// Cited returns which picks are carried, reading records once for them all: a
// record has named the pick since, or the pick is a land order whose PR row
// reads tapped at or after it. picks maps each pick's seq to its box id.
func (b Book) Cited(ctx context.Context, picks map[int64]string) ([]int64, error) {
	oldest := int64(math.MaxInt64)
	for seq := range picks {
		oldest = min(oldest, seq)
	}
	records, err := b.Bus.Tail(ctx, client.Query{Drive: b.Entry.Bus(), After: oldest - 1})
	if err != nil {
		return nil, err
	}
	names, err := b.names(ctx)
	if err != nil {
		return nil, err
	}
	tapped, err := b.tapped(picks, records)
	if err != nil {
		return nil, err
	}
	return slices.Compact(slices.Sorted(slices.Values(append(cited(picks, records, names), tapped...)))), nil
}

func (b Book) tapped(picks map[int64]string, records []client.Record) ([]int64, error) {
	orders := map[int64]bus.Tap{}
	at := map[int64]string{}
	for _, record := range records {
		if _, asked := picks[record.Seq]; !asked {
			continue
		}
		if tap, named := bus.OrderOf(record, b.Entry.Repo); named && tap.Value == string(machine.TapTapped) {
			orders[record.Seq], at[record.Seq] = tap, store.Stamp(record.At)
		}
	}
	if len(orders) == 0 {
		return nil, nil
	}
	s, err := store.Open(b.Entry.Store())
	if err != nil {
		return nil, err
	}
	defer s.Close()
	rows, err := s.ReviewRows(b.Entry.Repo)
	if err != nil {
		return nil, err
	}
	byNumber := make(map[int]store.ReviewRow, len(rows))
	for _, row := range rows {
		byNumber[row.Number] = row
	}
	var carried []int64
	for seq, tap := range orders {
		if row, known := byNumber[tap.PR]; known && row.Tapped && row.TappedAt >= at[seq] {
			carried = append(carried, seq)
		}
	}
	return carried, nil
}

// Close ends the rows with these seqs, for a pick no record will ever name.
func (b Book) Close(ctx context.Context, why string, seqs []int64) error {
	state, err := b.load()
	if err != nil {
		return err
	}
	id, err := b.ledger(ctx, &state)
	if err != nil {
		return err
	}
	rows, err := b.rows(ctx, id)
	if err != nil {
		return err
	}
	var keys []string
	for key, row := range rows {
		if slices.Contains(seqs, seqOf(row)) {
			rows[key] = with(row, stateClosed, why)
			keys = append(keys, key)
		}
	}
	slices.Sort(keys)
	return b.write(ctx, id, rows, keys)
}

// Post writes one finding as a defect addressed to the lane that owns its fix.
func (b Book) Post(ctx context.Context, sweep, lane, verdict, pr, line, text string) (client.Record, error) {
	pr = strings.TrimPrefix(pr, "#")
	numbered, spot := "-", cmp.Or(line, "-")
	var prs []string
	if pr != "" {
		numbered, prs = "#"+pr, []string{pr}
	}
	return client.Post(ctx, client.Draft{
		Drive: b.Entry.Bus(), Lane: sweep, Kind: "defect", To: []string{lane}, PRs: prs,
		Text: fmt.Sprintf("[finding:%s] %s %s %s owner %s: %s", sweep, numbered, spot, verdict, lane, text),
	})
}

// Live lists the open and routed rows by sweep and seq, narrowed when sweep or
// pr is given.
func Live(rows Rows, sweep, pr string) []Fields {
	var out []Fields
	for _, row := range rows {
		if live(row) && (sweep == "" || row["sweep"] == sweep) && (pr == "" || row["pr"] == pr) {
			out = append(out, row)
		}
	}
	slices.SortFunc(out, func(a, b Fields) int {
		return cmp.Or(cmp.Compare(a["sweep"], b["sweep"]), cmp.Compare(seqOf(a), seqOf(b)))
	})
	return out
}
