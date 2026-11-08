package board

import (
	"fmt"
	"slices"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Waits struct {
	Repo    string
	Waiting []store.ReviewRow
	Cards   []OpenCard
}

func WaitsOnHim(entry drive.Entry, cards []OpenCard) (Waits, error) {
	cfg, err := DriveConfig(entry)
	if err != nil {
		return Waits{}, err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return Waits{}, err
	}
	defer func() { _ = s.Close() }()
	rows, err := s.ReviewRows(cfg.Repo)
	if err != nil {
		return Waits{}, err
	}
	w := Waits{Repo: cfg.Repo, Cards: cards}
	for _, row := range rows {
		if row.State == prrow.StateOpen && waitsOnHim(row) && OffersApproval(row) {
			w.Waiting = append(w.Waiting, row)
		}
	}
	slices.SortFunc(w.Waiting, func(one, other store.ReviewRow) int { return one.Number - other.Number })
	return w, nil
}

func (w Waits) URLs() []string {
	urls := make([]string, len(w.Waiting))
	for n, row := range w.Waiting {
		urls[n] = fmt.Sprintf("https://github.com/%s/pull/%d", w.Repo, row.Number)
	}
	return urls
}
