package facts

import (
	"context"
	"fmt"
	"maps"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	headBatch  = 100
	headFields = "number state headRefOid"
)

type Pushed struct {
	Facts   machine.Facts
	RowHead string
	HeadAt  string
}

func (s Source) AtPushedHeads(ctx context.Context) (map[int]Pushed, error) {
	held, err := s.holds(ctx)
	if err != nil {
		return nil, fmt.Errorf("read holds: %w", err)
	}
	rows, err := s.Store.ReviewRows(s.Repo)
	if err != nil {
		return nil, err
	}
	rows = slices.DeleteFunc(rows, func(row store.ReviewRow) bool { return prrow.Terminal(row.State) })
	heads, err := s.readAliased(ctx, liveNumbers(rows), headBatch, headFields)
	if err != nil {
		return nil, fmt.Errorf("read the pushed heads: %w", err)
	}
	unfolded, err := s.unfoldedFacts(ctx)
	if err != nil {
		return nil, fmt.Errorf("read records the pass has not folded: %w", err)
	}
	atHeads, err := s.Store.FactsAtRowHeads(s.Repo, rows)
	if err != nil {
		return nil, err
	}
	out := make(map[int]Pushed, len(rows))
	for _, row := range rows {
		pr := heads[fmt.Sprintf("r%d", row.Number)]
		if pr == nil || pr.State != openState {
			continue
		}
		pushed, stored := Pushed{HeadAt: row.HeadAt}, atHeads[row.Number]
		if row.Head == "" || !strings.HasPrefix(pr.HeadRefOid, row.Head) {
			pushed = Pushed{RowHead: row.Head}
			if stored, err = s.Store.Facts(s.Repo, row.Number, pr.HeadRefOid); err != nil {
				return nil, err
			}
			row = unreadAt(row, pr.HeadRefOid)
		}
		merged := map[string]string{}
		maps.Copy(merged, stored)
		for _, fact := range unfolded[row.Number] {
			if strings.HasPrefix(pr.HeadRefOid, fact.Head) {
				merged[fact.Kind] = fact.Value
			}
		}
		machine.DropVoided(merged)
		if pushed.Facts, err = factsOfRow(s.Repo, row, merged); err != nil {
			return nil, err
		}
		pushed.Facts.Head = pr.HeadRefOid
		held[row.Number].onto(&pushed.Facts)
		out[row.Number] = pushed
	}
	return out, nil
}

func unreadAt(row store.ReviewRow, head string) store.ReviewRow {
	row.Head, row.CI, row.Approvers, row.Mergeable, row.Tapped = head, prrow.CIPending, prrow.None, string(machine.MergeableUnknown), false
	return row
}

func (s Source) unfoldedFacts(ctx context.Context) (map[int][]bus.Fact, error) {
	since, err := s.Store.Cursor(bus.LegacyCursor)
	if err != nil {
		return nil, err
	}
	records, err := s.Bus.ReadLegacy(ctx, since, s.Now())
	if err != nil {
		return nil, err
	}
	unfolded := map[int][]bus.Fact{}
	for _, record := range records {
		for _, fact := range bus.LegacyFacts(record, s.Repo, s.GoLanes) {
			unfolded[fact.PR] = append(unfolded[fact.PR], fact)
		}
	}
	return unfolded, nil
}
