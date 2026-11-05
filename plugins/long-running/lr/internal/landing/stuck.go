package landing

import (
	"context"
	"errors"
	"fmt"
	"slices"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
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

func WarnPersonQueued(ctx context.Context, drive string, snapshot []machine.Facts) error {
	var failed []error
	for _, f := range snapshot {
		if !f.PersonQueued || f.Tapped() || f.Hold != "" || f.QueuedHead != f.Head {
			continue
		}
		_, err := client.Post(ctx, client.Draft{
			Drive: drive,
			Lane:  self,
			Kind:  "report",
			To:    []string{lanes.OwnerRelay},
			ID:    fmt.Sprintf("person-queued:%d:%s", f.Number, f.Head),
			PRs:   []string{pullRef(f)},
			Text:  fmt.Sprintf("PERSON-QUEUED #%d at %s: someone outside the drive queued it with no owner tap or go at this head, so the supervisor leaves it queued instead of dequeuing it; if the owner queued it, record his landing", f.Number, shortHead(f.Head)),
		})
		failed = append(failed, err)
	}
	return errors.Join(failed...)
}
