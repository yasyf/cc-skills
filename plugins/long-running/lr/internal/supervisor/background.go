package supervisor

import (
	"context"
	"fmt"
	"log/slog"
	"sync/atomic"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	recurEvery = time.Hour
	judgeEvery = time.Minute
	judgeLimit = 15 * time.Minute
)

type background struct {
	name  string
	every time.Duration
	run   func(context.Context) error
	store *store.Store
	post  pass.Post
	log   *slog.Logger
	read  time.Time
	busy  atomic.Bool
}

func (b *background) Check(ctx context.Context, now time.Time) error {
	if (!b.read.IsZero() && now.Sub(b.read) < b.every) || !b.busy.CompareAndSwap(false, true) {
		return nil
	}
	b.read = now
	go func() {
		defer b.busy.Store(false)
		b.pass(ctx, now)
	}()
	return nil
}

func (b *background) pass(ctx context.Context, now time.Time) {
	failed := b.run(ctx)
	if failed == nil {
		return
	}
	cause := fmt.Sprintf("the last %s pass failed, so it posts nothing until a pass succeeds: %v", b.name, failed)
	raised, err := b.store.Escalate(b.name, cause, lanes.Root, now)
	if err == nil && raised {
		err = b.post([]string{lanes.Root}, bus.Escalation{Subject: b.name, Cause: cause})
	}
	if err != nil {
		b.log.Warn("a background pass failed and could not say so", "pass", b.name, "failed", failed, "err", err)
	}
}
