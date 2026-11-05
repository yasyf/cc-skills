package update

import (
	"context"
	"maps"
	"slices"
	"strconv"
	"time"
)

type Owners struct {
	PRs  map[string][]string `json:"prs"`
	Live []string            `json:"live"`
}

func ReadOwners(ctx context.Context, src Sources, now time.Time) (Owners, error) {
	lanes, err := src.Lanes(ctx)
	if err != nil {
		return Owners{}, err
	}
	records, err := src.Records(ctx, now.Add(-quietAfter))
	if err != nil {
		return Owners{}, err
	}
	rows, err := src.PRRows()
	if err != nil {
		return Owners{}, err
	}
	v := newView(lanes, records, nil, now)
	owners := Owners{PRs: map[string][]string{}, Live: slices.Collect(maps.Keys(v.answerers))}
	for _, row := range openPRRows(rows) {
		owners.PRs[strconv.Itoa(row.Number)] = aliases(slices.DeleteFunc(v.prLanes(row), func(lane string) bool { return !v.live(lane) }))
	}
	for _, lane := range lanes {
		if v.live(lane.Name) {
			owners.Live = append(owners.Live, lane.Name)
		}
	}
	slices.Sort(owners.Live)
	owners.Live = slices.Compact(owners.Live)
	return owners, nil
}
