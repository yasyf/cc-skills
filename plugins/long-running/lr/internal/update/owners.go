package update

import (
	"context"
	"maps"
	"slices"
	"strconv"
	"time"

	"github.com/yasyf/cc-inbox/client"
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
	live := map[string]bool{}
	for _, lane := range lanes {
		if registered(lane) {
			live[lane.Name], live[client.Stem(lane.Name)] = true, true
		}
	}
	owners := Owners{PRs: map[string][]string{}, Live: slices.Sorted(maps.Keys(live))}
	for _, row := range openPRRows(rows) {
		owners.PRs[strconv.Itoa(row.Number)] = aliases(slices.DeleteFunc(v.prLanes(row), func(lane string) bool { return !live[lane] }))
	}
	return owners, nil
}
