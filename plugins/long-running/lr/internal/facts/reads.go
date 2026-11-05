package facts

import (
	"context"
	"fmt"
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

func (s Source) readMoved(ctx context.Context, rows []store.ReviewRow, held map[int]hold, statusesOf statusRead) ([]machine.Facts, error) {
	itemed, err := s.itemed()
	if err != nil {
		return nil, err
	}
	heard, ends, err := s.Rows().Heard(ctx, readMovedCursor)
	if err != nil {
		return nil, fmt.Errorf("read the github records: %w", err)
	}
	last, err := s.Store.Cursor(readAllCursor)
	if err != nil {
		return nil, err
	}
	all := s.Now().Unix()-last >= int64(ReadAllEvery.Seconds())
	numbers := readable(rows, itemed)
	if !all {
		atHeads, err := s.Store.FactsAtRowHeads(s.Repo, rows)
		if err != nil {
			return nil, err
		}
		numbers = toRead(numbers, rows, atHeads, prrow.Moved(heard, rows, s.Repo, s.Trunk))
	}
	read, err := s.readNumbers(ctx, numbers, itemed, rows, held, statusesOf)
	if err != nil {
		return nil, err
	}
	unread, err := s.fromRows(unreadRows(rows, numbers), held)
	if err != nil {
		return nil, err
	}
	for at := range unread {
		unread[at].FromRow = true
	}
	if err := s.Rows().PostReads(ctx, read); err != nil {
		s.Log.Warn("a pull request's read went unposted; the next pass reads it again", "error", err)
		return slices.Concat(read, unread), nil
	}
	for at := range read {
		read[at].Posted = true
	}
	if all {
		ends[readAllCursor] = s.Now().Unix()
	}
	for cursor, end := range ends {
		if err := s.Store.Advance(cursor, end); err != nil {
			return nil, err
		}
	}
	return slices.Concat(read, unread), nil
}

func toRead(numbers []int, rows []store.ReviewRow, atHeads map[int]map[string]string, moved map[int]bool) []int {
	byNumber := make(map[int]store.ReviewRow, len(rows))
	for _, row := range rows {
		byNumber[row.Number] = row
	}
	return slices.DeleteFunc(numbers, func(number int) bool {
		row, known := byNumber[number]
		return known && !moved[number] && !mustRead(row, atHeads[number])
	})
}

func mustRead(row store.ReviewRow, facts map[string]string) bool {
	return row.ReadHead == "" || row.Tapped || machine.Queue(row.Queue) == machine.QueueQueued || row.State == prrow.StateExempt ||
		row.Mergeable == string(machine.MergeableUnknown) || facts[prrow.FactUnknownSince] != ""
}

func unreadRows(rows []store.ReviewRow, read []int) []store.ReviewRow {
	return slices.DeleteFunc(slices.Clone(rows), func(row store.ReviewRow) bool {
		_, found := slices.BinarySearch(read, row.Number)
		return found
	})
}
