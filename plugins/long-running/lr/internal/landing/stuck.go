package landing

import (
	"context"
	"errors"
	"slices"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func SayStuck(ctx context.Context, s *store.Store, drive string, snapshot []machine.Facts, stuck map[int]string) error {
	var failed []error
	for _, f := range snapshot {
		if why := stuck[f.Number]; why != f.Stuck {
			failed = append(failed, sayStuck(ctx, s, drive, f, why))
		}
	}
	return errors.Join(failed...)
}

func sayStuck(ctx context.Context, s *store.Store, drive string, f machine.Facts, why string) error {
	b := bus.Bus{Drive: drive, Lane: self}
	if why == "" {
		return b.PostStuck(ctx, f.Repo, f.Number, f.Head, nil, "")
	}
	holder, unowned, err := reach(s, drive, f)
	if err != nil {
		return err
	}
	return b.PostStuck(ctx, f.Repo, f.Number, f.Head, slices.Compact([]string{holder, rootLane}), unowned+why)
}
