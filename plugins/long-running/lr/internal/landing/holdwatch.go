package landing

import (
	"context"
	"log/slog"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	holdKind      = "hold"
	refollowAfter = time.Minute
)

type HoldWatch struct {
	Store *store.Store
	Drive string
	Repo  string
	Trunk string
	Read  func(ctx context.Context, named []int) ([]machine.Facts, error)
	Queue Queue
	Now   func() time.Time
	Log   *slog.Logger
	After int64
}

func (w *HoldWatch) Run(ctx context.Context) {
	for ctx.Err() == nil {
		err := client.Follow(ctx, client.Watch{Drive: w.Drive, After: w.After, Kinds: []string{holdKind}}, func(record client.Record) error {
			w.After = record.Seq
			w.Handle(ctx, record)
			return nil
		})
		if ctx.Err() != nil {
			return
		}
		w.Log.Warn("the hold follower stopped and follows again; each landing pass still reads every hold", "err", err)
		select {
		case <-ctx.Done():
		case <-time.After(refollowAfter):
		}
	}
}

func (w *HoldWatch) Handle(ctx context.Context, record client.Record) {
	named := bus.HeldBy(record, w.Repo)
	if len(named) == 0 || !bus.HoldGates(record) {
		return
	}
	snapshot, err := w.Read(ctx, named)
	if err != nil {
		w.Log.Warn("a new hold's PRs did not read, so the next landing pass acts on it", "hold", record.Seq, "err", err)
		return
	}
	done, err := Act(ctx, w.Store, w.Queue, nil, Clear(pass.HeldInQueue(snapshot), snapshot, w.Trunk), w.Now())
	if err != nil {
		w.Log.Warn("a new hold's dequeue stopped, so the next landing pass acts on it", "hold", record.Seq, "err", err)
	}
	for _, said := range done.Said {
		w.Log.Info("a new hold pulled a queued head", "hold", record.Seq, "pr", said.Facts.Number, "head", said.Facts.Head, "said", said.Text, "since_posted", w.Now().Sub(record.At).String())
	}
	if err := Settle(ctx, w.Store, w.Drive, done.Said); err != nil {
		w.Log.Warn("a queue answer went unposted", "hold", record.Seq, "err", err)
	}
}
