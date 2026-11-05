package gates

import (
	"cmp"
	"fmt"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	NoRow       = "it has no review row in the landing store"
	NoStore     = "this box has no landing store: it holds no review row for any PR, so read the row on the landing host"
	NoTapCards  = "this box's landing store has never recorded a tap card shown, so it cannot say whether his card for this head is open; read it on the landing host"
	notReadyYet = "its review row is not ready"
	exemptSays  = "Exempt as "
)

func ExemptWaits(class string) string {
	return exemptSays + class + "; the landing desk lands it next."
}

func LandsExempt(row store.ReviewRow) bool {
	return strings.HasPrefix(row.WaitsOn, exemptSays)
}

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
	if row.ReadyForTap || LandsExempt(row) {
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

func TapCardShownAt(s *store.Store, repo string, number int, head string) (string, error) {
	facts, err := s.Facts(repo, number, head)
	if err != nil {
		return "", err
	}
	if facts[ShownFact] != "" {
		return fmt.Sprintf("his tap card for it was shown at %s, so it lands on his tap whatever its class", short(head)), nil
	}
	shown, err := s.FactsOfKind(repo, ShownFact)
	if err != nil || len(shown) > 0 {
		return "", err
	}
	return NoTapCards, nil
}

func NoRowIn(s *store.Store, repo string) (string, error) {
	held, err := s.HoldsReviewRows(repo)
	if err != nil || held {
		return NoRow, err
	}
	return NoStore, nil
}
