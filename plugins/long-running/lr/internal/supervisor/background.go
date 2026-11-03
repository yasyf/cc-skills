package supervisor

import (
	"cmp"
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	recurEvery  = time.Hour
	rejoinEvery = 5 * time.Minute
	wasteEvery  = time.Hour
	judgeEvery  = time.Minute
	judgeLimit  = 15 * time.Minute
)

type background struct {
	name  string
	every time.Duration
	run   func(context.Context) error
	store *store.Store
	post  pass.Post
	log   *slog.Logger
	to    string
	read  time.Time
}

func (b *background) Check(ctx context.Context, now time.Time) error {
	if !b.read.IsZero() && now.Sub(b.read) < b.every {
		return nil
	}
	b.read = now
	b.pass(ctx, now)
	return nil
}

func (b *background) pass(ctx context.Context, now time.Time) {
	failed := b.run(ctx)
	if failed == nil || ctx.Err() != nil {
		return
	}
	cause := fmt.Sprintf("the last %s pass failed; the next one runs on schedule: %v", b.name, failed)
	to := cmp.Or(b.to, lanes.Root)
	raised, err := b.store.Escalate(b.name, cause, to, now)
	if err == nil && raised {
		err = b.post([]string{to}, bus.Overdue(to, b.name, cause))
	}
	if err != nil {
		b.log.Warn("a background pass failed and could not say so", "pass", b.name, "failed", failed, "err", err)
	}
}
