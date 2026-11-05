package gates

import (
	"cmp"
	"fmt"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	NoRow       = "it has no review row in the landing store"
	NoStore     = "this box has no landing store: it holds no review row for any PR, so read the row on the landing host"
	notReadyYet = "its review row is not ready"
)

func RowRead(row store.ReviewRow, found bool, head string) string {
	switch {
	case !found:
		return NoRow
	case !sameHead(row.Head, head):
		return fmt.Sprintf("its review row is at %s, not %s", short(row.Head), short(head))
	}
	return ""
}

func RowWait(row store.ReviewRow, found bool, head string) string {
	if unread := RowRead(row, found, head); unread != "" {
		return unread
	}
	if row.ReadyForTap {
		return ""
	}
	return cmp.Or(row.WaitsOn, notReadyYet)
}

func RowReadAt(s *store.Store, repo string, number int, head string) (string, error) {
	row, found, err := s.ReviewRow(repo, number)
	if err != nil || found {
		return RowRead(row, found, head), err
	}
	return NoRowIn(s, repo)
}

func NoRowIn(s *store.Store, repo string) (string, error) {
	held, err := s.HoldsReviewRows(repo)
	if err != nil || held {
		return NoRow, err
	}
	return NoStore, nil
}
