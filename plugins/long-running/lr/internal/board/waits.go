package board

import (
	"fmt"
	"strconv"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	noOpenRow      = "it has no open row in the PR ledger, lr board rows"
	recordWaits    = "its row in lr board rows waits on: "
	unrecordedTime = "an unrecorded time"
)

type PRWait struct {
	PR      int    `json:"pr"`
	Head    string `json:"head"`
	Waiting bool   `json:"waiting"`
	Line    string `json:"line"`
}

func pacificStamp(stamp string) string {
	at, err := time.Parse(time.RFC3339, stamp)
	if err != nil {
		return unrecordedTime
	}
	return pacific(at)
}

func (l Live) tapFact(pr, sha string, row store.ReviewRow) string {
	entry := l.Taps[pr]
	if decided := l.Decided(pr, sha); decided != "" {
		fact := fmt.Sprintf("you %s it at %s", decided, abbreviate(sha))
		if entry.At != "" {
			fact += " at " + pacificStamp(entry.At)
		}
		return fact
	}
	shown := recordWaits + row.WaitsOn
	if entry.Verdict == "" || entry.Head == "" {
		return shown
	}
	return fmt.Sprintf("%s; you %s it at %s, an older head", shown, entry.Verdict, abbreviate(entry.Head))
}

func (l Live) Explain(pr int, rows map[int]store.ReviewRow) PRWait {
	row, recorded := rows[pr]
	if !recorded || prrow.Terminal(row.State) || row.State == prrow.StateSuperseded {
		return PRWait{PR: pr, Line: fmt.Sprintf("#%d: not waiting on you: %s", pr, noOpenRow)}
	}
	subject := fmt.Sprintf("#%d at %s", pr, abbreviate(row.Head))
	if row.ReadyForTap && !row.Tapped {
		return PRWait{pr, row.Head, true, fmt.Sprintf("%s: waits on your tap on the review tab", subject)}
	}
	fact := l.tapFact(strconv.Itoa(pr), row.Head, row)
	if row.Tapped && l.Decided(strconv.Itoa(pr), row.Head) == "" {
		fact = "a land order stands at " + abbreviate(row.Head)
	}
	return PRWait{pr, row.Head, false, fmt.Sprintf("%s: not waiting on you: %s", subject, fact)}
}
