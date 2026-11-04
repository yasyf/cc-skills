package gates

import (
	"cmp"
	"fmt"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	NoRow       = "it has no review row in the landing store"
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

func TapAt(s *store.Store, repo string, tap bus.Tap) (head, refusal string, err error) {
	row, found, err := s.ReviewRow(repo, tap.PR)
	if err != nil {
		return "", "", err
	}
	head = tap.Head
	if found && strings.HasPrefix(row.Head, tap.Head) {
		head = row.Head
	}
	return head, RowWait(row, found, head), nil
}
