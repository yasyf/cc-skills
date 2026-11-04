package facts

import (
	"context"
	"slices"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	ReadAllEvery    = 30 * time.Minute
	readAllCursor   = "github-read-all"
	readMovedCursor = "github-read-moved"
)

type readPlan struct {
	numbers []int
	through int64
	all     bool
}

func (s Source) planReads(ctx context.Context, rows []store.ReviewRow, live []int) (readPlan, error) {
	heard, err := prrow.Heard(ctx, s.Store, readMovedCursor, s.Now())
	if err != nil {
		return readPlan{}, err
	}
	through, err := s.Store.Cursor(readMovedCursor)
	if err != nil {
		return readPlan{}, err
	}
	if len(heard) > 0 {
		through = heard[len(heard)-1].Seq
	}
	last, err := s.Store.Cursor(readAllCursor)
	if err != nil {
		return readPlan{}, err
	}
	if s.Now().Unix()-last >= int64(ReadAllEvery.Seconds()) {
		return readPlan{live, through, true}, nil
	}
	moved := prrow.Moved(heard, rows, s.Repo, s.Trunk)
	byNumber := make(map[int]store.ReviewRow, len(rows))
	for _, row := range rows {
		byNumber[row.Number] = row
	}
	numbers := slices.DeleteFunc(slices.Clone(live), func(number int) bool {
		row, known := byNumber[number]
		return known && !moved[number] && !mustRead(row)
	})
	return readPlan{numbers, through, false}, nil
}

func mustRead(row store.ReviewRow) bool {
	return row.ReadHead == "" || row.State == prrow.StateTapped || row.State == prrow.StateQueued || row.Mergeable == string(machine.MergeableUnknown)
}

func (s Source) settleReads(plan readPlan) error {
	if plan.all {
		if err := s.Store.Advance(readAllCursor, s.Now().Unix()); err != nil {
			return err
		}
	}
	return s.Store.Advance(readMovedCursor, plan.through)
}

func unreadRows(rows []store.ReviewRow, read []int) []store.ReviewRow {
	return slices.DeleteFunc(slices.Clone(rows), func(row store.ReviewRow) bool {
		_, found := slices.BinarySearch(read, row.Number)
		return found
	})
}
