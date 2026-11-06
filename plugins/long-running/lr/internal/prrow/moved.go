package prrow

import (
	"context"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	reviewsDrive = "github"
	topicPull    = "pull_request"
	topicReview  = "review"
	topicCheck   = "check"
	topicPush    = "push"
)

func (f Fold) Heard(ctx context.Context, cursor string) ([]client.Record, map[string]int64, time.Time, error) {
	var heard []client.Record
	var pulled time.Time
	ends := map[string]int64{}
	for _, drive := range []string{pullsDrive, reviewsDrive} {
		read := cursor + ":" + drive
		records, err := f.tail(ctx, drive, read, []string{readKind})
		if err != nil {
			return nil, nil, time.Time{}, err
		}
		for _, record := range records {
			ends[read] = max(ends[read], record.Seq)
			if drive == pullsDrive && record.At.After(pulled) {
				pulled = record.At
			}
			if drive == pullsDrive || record.Topic == topicReview {
				heard = append(heard, record)
			}
		}
	}
	return heard, ends, pulled, nil
}

func Moved(records []client.Record, rows []store.ReviewRow, repo, trunk string) map[int]bool {
	byNumber := make(map[int]store.ReviewRow, len(rows))
	for _, row := range rows {
		byNumber[row.Number] = row
	}
	moved := map[int]bool{}
	bases := map[string]bool{}
	for _, record := range records {
		attrs := record.Fields.Attrs
		switch record.Topic {
		case topicPull:
			for _, number := range bus.HeldBy(record, repo) {
				if row, known := byNumber[number]; known {
					moved[number], bases[row.Branch] = true, true
				}
			}
		case topicReview:
			for _, number := range bus.HeldBy(record, repo) {
				moved[number] = true
			}
		case topicCheck:
			if !strings.EqualFold(attrs["repo"], repo) {
				continue
			}
			for _, row := range rows {
				if sameHead(row.Head, record.Fields.Commit) {
					moved[row.Number] = true
				}
			}
		case topicPush:
			if strings.EqualFold(attrs["repo"], repo) && attrs["branch"] == trunk {
				bases[trunk] = true
			}
		}
	}
	for _, row := range rows {
		if bases[row.Base] {
			moved[row.Number] = true
		}
	}
	return moved
}
