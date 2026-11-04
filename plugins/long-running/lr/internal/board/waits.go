package board

import (
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	noOpenPR       = "it is no open PR of this drive's landing ledger"
	noRecordRow    = "it has no row in lr board rows"
	recordWaits    = "its row in lr board rows waits on: "
	unrecordedTime = "an unrecorded time"
)

type PRWait struct {
	PR      int    `json:"pr"`
	Head    string `json:"head"`
	Waiting bool   `json:"waiting"`
	Line    string `json:"line"`
}

func withoutTap(missing []string) []string {
	return slices.DeleteFunc(slices.Clone(missing), func(gate string) bool { return gate == noTap })
}

func pacificStamp(stamp string) string {
	at, err := time.Parse(time.RFC3339, stamp)
	if err != nil {
		return unrecordedTime
	}
	return pacific(at)
}

func (l Live) tapFact(pr, sha string, row store.ReviewRow, recorded bool) string {
	entry := l.Taps[pr]
	if decided := l.Decided(pr, sha); decided != "" {
		fact := fmt.Sprintf("you %s it at %s", decided, abbreviate(sha))
		if entry.At != "" {
			fact += " at " + pacificStamp(entry.At)
		}
		return fact
	}
	shown := noRecordRow
	if recorded {
		shown = recordWaits + row.WaitsOn
	}
	if entry.Verdict == "" || entry.Head == "" {
		return shown
	}
	return fmt.Sprintf("%s; you %s it at %s, an older head", shown, entry.Verdict, abbreviate(entry.Head))
}

func (l Live) Explain(pr int, snapshot map[int]machine.Facts, rows map[int]store.ReviewRow) PRWait {
	f, open := snapshot[pr]
	if !open {
		return PRWait{PR: pr, Line: fmt.Sprintf("#%d: not waiting on you: %s", pr, noOpenPR)}
	}
	subject := fmt.Sprintf("#%d at %s", pr, abbreviate(f.Head))
	row, recorded := rows[pr]
	tapped := f.Tapped() || recorded && row.Tapped
	if recorded && row.ReadyForTap && !tapped {
		return PRWait{pr, f.Head, true, fmt.Sprintf("%s: waits on your tap on the review tab", subject)}
	}
	fact := l.tapFact(strconv.Itoa(pr), f.Head, row, recorded)
	if tapped && l.Decided(strconv.Itoa(pr), f.Head) == "" {
		fact = "a land order stands at " + abbreviate(f.Head)
	}
	if recorded {
		return PRWait{pr, f.Head, false, fmt.Sprintf("%s: not waiting on you: %s", subject, fact)}
	}
	held := slices.Insert(withoutTap(gates.Missing(f)), 0, fact)
	if below, stuck := stuckBelow(f, snapshot); stuck {
		held = append(held, fmt.Sprintf("the PR below it, #%d, cannot land yet: %s", below.Number, strings.Join(gates.Missing(below), "; ")))
	}
	return PRWait{pr, f.Head, false, fmt.Sprintf("%s: not waiting on you: %s", subject, strings.Join(held, "; "))}
}

func stuckBelow(f machine.Facts, snapshot map[int]machine.Facts) (machine.Facts, bool) {
	byBranch := make(map[string]machine.Facts, len(snapshot))
	for _, open := range snapshot {
		byBranch[open.Branch] = open
	}
	for range len(snapshot) {
		below, open := byBranch[f.Base]
		if !open {
			return machine.Facts{}, false
		}
		if !below.ClearsAbove() {
			return below, true
		}
		f = below
	}
	return machine.Facts{}, false
}
