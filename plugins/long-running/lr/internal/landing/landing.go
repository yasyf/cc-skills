package landing

import (
	"context"
	"strconv"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	ResubmittedFact = "resubmitted"
	RestackedFact   = "restacked"
	conflictValue   = "conflict"
	restacksPerPass = 2
)

type Queue interface {
	Enqueue(ctx context.Context, number int, head string) (string, error)
	Dequeue(ctx context.Context, number int) error
}

type Restacker interface {
	Restack(ctx context.Context, number int, head, base string) (string, error)
}

type Head struct {
	Number int
	Head   string
}

type Move struct {
	Number int
	From   string
	To     string
}

type Done struct {
	Enqueued    []Head
	Resubmitted []Head
	Dequeued    []int
	Restacked   []Move
	Conflicts   []int
}

func Act(ctx context.Context, s *store.Store, queue Queue, restacker Restacker, acts []pass.Act, now time.Time) (Done, error) {
	var done Done
	for _, act := range acts {
		var err error
		switch act.Verdict.Action {
		case machine.ActionEnqueue:
			err = enqueue(ctx, s, queue, act.Facts, now, &done)
		case machine.ActionDequeue:
			if err = queue.Dequeue(ctx, act.Facts.Number); err == nil {
				done.Dequeued = append(done.Dequeued, act.Facts.Number)
			}
		case machine.ActionRestack:
			err = restack(ctx, s, restacker, act.Facts, now, &done)
		}
		if err != nil {
			return done, err
		}
	}
	return done, nil
}

func enqueue(ctx context.Context, s *store.Store, queue Queue, f machine.Facts, now time.Time, done *Done) error {
	said, err := queue.Enqueue(ctx, f.Number, f.Head)
	if err != nil {
		return err
	}
	if said != "" {
		if _, err := s.PutFact(f.Repo, f.Number, f.Head, pass.EnqueuedFact, said, now); err != nil {
			return err
		}
	}
	if f.Queue != machine.QueueEvicted {
		done.Enqueued = append(done.Enqueued, Head{f.Number, f.Head})
		return nil
	}
	if _, err := s.PutFact(f.Repo, f.Number, f.Head, ResubmittedFact, strconv.Itoa(f.Resubmitted+1), now); err != nil {
		return err
	}
	done.Resubmitted = append(done.Resubmitted, Head{f.Number, f.Head})
	return nil
}

func restack(ctx context.Context, s *store.Store, restacker Restacker, f machine.Facts, now time.Time, done *Done) error {
	if !f.Tapped() || len(done.Restacked)+len(done.Conflicts) >= restacksPerPass {
		return nil
	}
	facts, err := s.Facts(f.Repo, f.Number, f.Head)
	if err != nil {
		return err
	}
	if _, restacked := facts[RestackedFact]; restacked {
		return nil
	}
	moved, err := restacker.Restack(ctx, f.Number, f.Head, f.Base)
	if err != nil {
		return err
	}
	value := moved
	if moved == "" {
		value = conflictValue
	}
	if _, err := s.PutFact(f.Repo, f.Number, f.Head, RestackedFact, value, now); err != nil {
		return err
	}
	if moved == "" {
		done.Conflicts = append(done.Conflicts, f.Number)
		return nil
	}
	done.Restacked = append(done.Restacked, Move{f.Number, f.Head, moved})
	return nil
}
