package supervisor

import (
	"cmp"
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	recurEvery    = time.Hour
	rejoinEvery   = 5 * time.Minute
	wasteEvery    = time.Hour
	judgeEvery    = time.Minute
	judgeLimit    = 15 * time.Minute
	choiceEvery   = 10 * time.Minute
	sweepEvery    = 5 * time.Minute
	findingsEvery = 5 * time.Minute

	recoveredCursor = "background-recovered:"
	startedCursor   = "background-started:"
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
	if b.read.IsZero() {
		started, err := b.store.Cursor(startedCursor + b.name)
		if err != nil {
			return err
		}
		b.read = time.UnixMicro(started)
	}
	if now.Sub(b.read) < b.every {
		return nil
	}
	b.read = now
	if err := b.store.Advance(startedCursor+b.name, now.UnixMicro()); err != nil {
		return err
	}
	b.pass(ctx, now)
	return nil
}

func (b *background) pass(ctx context.Context, now time.Time) {
	failed := b.run(ctx)
	if ctx.Err() != nil {
		return
	}
	to := cmp.Or(b.to, lanes.Root)
	if failed == nil {
		if err := b.sayRecovered(to); err != nil {
			b.log.Warn("a background pass recovered and could not say so", "pass", b.name, "err", err)
		}
		return
	}
	cause := fmt.Sprintf("the last %s pass failed; the next one runs on schedule: %v", b.name, failed)
	raised, err := b.store.Escalate(b.name, cause, to, now)
	if err == nil && raised {
		err = b.post([]string{to}, bus.Overdue(to, b.name, cause))
	}
	if err != nil {
		b.log.Warn("a background pass failed and could not say so", "pass", b.name, "failed", failed, "err", err)
	}
}

func (b *background) sayRecovered(to string) error {
	raised, err := b.store.Escalations(b.name)
	if err != nil || len(raised) == 0 {
		return err
	}
	last := raised[len(raised)-1]
	cursor := recoveredCursor + b.name
	said, err := b.store.Cursor(cursor)
	if err != nil || last.Raised.UnixMicro() <= said {
		return err
	}
	if err := b.post([]string{to}, bus.Overdue(to, b.name+" recovered:", "a pass succeeded after this failure: "+last.Cause)); err != nil {
		return err
	}
	return b.store.Advance(cursor, last.Raised.UnixMicro())
}

type sweepLog struct {
	log  *slog.Logger
	name string
}

func (l sweepLog) Write(line []byte) (int, error) {
	l.log.Info(l.name, "line", strings.TrimSpace(string(line)))
	return len(line), nil
}
