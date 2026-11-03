package supervisor

import (
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
	recurEvery   = time.Hour
	recurSubject = "recurrence-count"
)

type recurrences struct {
	count func(context.Context) error
	store *store.Store
	post  pass.Post
	log   *slog.Logger
	read  time.Time
}

func (r *recurrences) Check(ctx context.Context, now time.Time) error {
	if !r.read.IsZero() && now.Sub(r.read) < recurEvery {
		return nil
	}
	r.read = now
	go r.pass(ctx, now)
	return nil
}

func (r *recurrences) pass(ctx context.Context, now time.Time) {
	failed := r.count(ctx)
	if failed == nil {
		return
	}
	cause := fmt.Sprintf("the last recurrence pass failed, so no recurrence count posts until it passes again: %v", failed)
	raised, err := r.store.Escalate(recurSubject, cause, lanes.Root, now)
	if err == nil && raised {
		err = r.post([]string{lanes.Root}, bus.Escalation{Subject: recurSubject, Cause: cause})
	}
	if err != nil {
		r.log.Warn("recurrence pass failed and could not say so", "failed", failed, "err", err)
	}
}
