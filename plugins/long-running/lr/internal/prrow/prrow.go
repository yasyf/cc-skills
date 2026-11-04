package prrow

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	None   = "none"
	NotYet = "not yet"

	StateOpen       = "open"
	StateDraft      = "draft"
	StateTapped     = "tapped"
	StateQueued     = "queued"
	StateLanded     = "landed"
	StateClosed     = "closed"
	StateSuperseded = "superseded"

	CIGreen   = "green"
	CIRed     = "red"
	CIPending = "pending"

	GradePass    = "pass"
	GradeFail    = "fail"
	GradePending = "pending"

	ParentLanded  = "landed"
	ParentOpen    = "open"
	ParentClosed  = "closed"
	ParentUnknown = "unknown"

	NotGraded = "not graded at this head"
	RedAtHead = "CI is red at this head"

	onlyHim     = "Only your approval."
	tapEndsHold = "Only your approval at this head, which ends the hold: "
	tappedWaits = "You approved it; the landing desk lands it next."
	fieldChars  = 700
	planChecked = "Checked against the plan by the fidelity review at this version."
	portLands   = "Nothing from you: a clean old-tree port lands on your standing word that ports need no tap, and its go comes next."
	flakeLands  = "Nothing from you: a test-flake fix lands on your standing word that flake fixes need no tap, and its go comes next."
)

var (
	heading  = regexp.MustCompile(`(?m)^#{1,3}\s+(.+?)\s*$`)
	planLine = regexp.MustCompile(`(?im)^.*\b(?:plan (?:step|row|\d)|rule/[0-9a-f]{6,}|appendix)\b.*$`)
)

func Terminal(state string) bool {
	return state == StateLanded || state == StateClosed
}

func Decide(row store.ReviewRow) store.ReviewRow {
	unmet := firstUnmet(row)
	row.ReadyForTap = unmet == ""
	switch {
	case unmet != "":
		row.WaitsOn = unmet
	case row.Tapped:
		row.WaitsOn = tappedWaits
	case row.AwaitsTap != "":
		row.WaitsOn = tapEndsHold + row.AwaitsTap
	default:
		row.WaitsOn = onlyHim
	}
	return row
}

// Tap records the owner's tap at head as its facts and applies the fold's rule
// to the row in the same transaction, so a card drawn next reads it tapped.
func Tap(s *store.Store, repo string, number int, head, by string, at time.Time) error {
	facts := map[string]string{"tap": string(machine.TapTapped), FactTapBy: by, FactTapAt: store.Stamp(at)}
	return transition(s, repo, number, head, facts, at, func(row store.ReviewRow) store.ReviewRow {
		row.Tapped, row.TapRefused, row.TappedAt, row.TappedBy = true, None, store.Stamp(at), by
		if row.State == StateOpen {
			row.State = StateTapped
		}
		return row
	})
}

// Withdraw records a lane's withdrawn tap at head as its fact and applies the
// fold's rule to the row in the same transaction.
func Withdraw(s *store.Store, repo string, number int, head, why string, at time.Time) error {
	return transition(s, repo, number, head, map[string]string{FactWithdrawn: why}, at, func(row store.ReviewRow) store.ReviewRow {
		row.SupersededBy, row.State = why, StateSuperseded
		return row
	})
}

func transition(s *store.Store, repo string, number int, head string, facts map[string]string, at time.Time, change func(store.ReviewRow) store.ReviewRow) error {
	return s.Update(func(t *store.Tx) error {
		for kind, value := range facts {
			if err := t.PutFact(repo, number, head, kind, value, at); err != nil {
				return err
			}
		}
		rows, err := t.ReviewRows(repo)
		if err != nil {
			return err
		}
		found := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == number && sameHead(row.Head, head) })
		if found < 0 || Terminal(rows[found].State) {
			return nil
		}
		return t.PutReviewRows([]store.ReviewRow{Decide(change(rows[found]))})
	})
}

func firstUnmet(row store.ReviewRow) string {
	switch {
	case row.State == StateLanded:
		return "Nothing: it landed."
	case row.State == StateClosed:
		return "Nothing: it was closed without landing."
	case row.SupersededBy != None:
		return "Nothing: superseded, " + row.SupersededBy
	case row.State == StateDraft:
		return "it is a draft"
	case row.Hold != None:
		return "held, " + row.Hold
	case row.State == StateQueued:
		return "it is in the merge queue"
	case row.ParentState == ParentOpen:
		return fmt.Sprintf("the change below it, %s, has not landed", row.Parent)
	case row.ParentState == ParentClosed:
		return fmt.Sprintf("the change below it, %s, was closed without landing", row.Parent)
	case row.ParentState == ParentUnknown:
		return fmt.Sprintf("the change below it, on branch %s, has no row", row.Parent)
	case row.Head == "":
		return "no record names its head yet"
	case row.CI == CIRed:
		return RedAtHead
	case row.CI != CIGreen:
		return "CI is running or not read yet at this head"
	case row.Approvers == None:
		return "no approving review at this head"
	case row.Grade == GradePending:
		return NotGraded
	case row.Grade != GradePass:
		return "a grade needs a verdict: " + row.GradeFindings
	case row.Mergeable != string(machine.MergeableClean):
		return "mergeability is " + row.Mergeable
	case row.UnsafeWait != "":
		return gates.UnsafeOnDev(row.UnsafeWait)
	case row.ForUsers == "":
		return "its PR body has no Summary"
	case row.PlanRow == "":
		return "its PR body names no plan row"
	case row.Risk == "":
		return "its PR body has no Risk profile"
	}
	return ""
}

func ApplyBody(row *store.ReviewRow, body string) {
	row.ForUsers = section(body, "Summary")
	row.Risk = section(body, "Risk profile")
	row.PlanLine = clip(strings.TrimSpace(planLine.FindString(withoutSection(body, "Risk profile"))), fieldChars)
	row.Description = strings.TrimSpace(body)
	row.UnsafeWait = gates.UnsafeWait(body)
}

func section(body, name string) string {
	opens, starts, ends := sectionAt(body, name)
	if opens < 0 {
		return ""
	}
	return strings.TrimSpace(body[starts:ends])
}

func withoutSection(body, name string) string {
	opens, _, ends := sectionAt(body, name)
	if opens < 0 {
		return body
	}
	return body[:opens] + body[ends:]
}

func sectionAt(body, name string) (opens, starts, ends int) {
	found := heading.FindAllStringSubmatchIndex(body, -1)
	for at, match := range found {
		if !strings.EqualFold(body[match[2]:match[3]], name) {
			continue
		}
		ends = len(body)
		if at+1 < len(found) {
			ends = found[at+1][0]
		}
		return match[0], match[1], ends
	}
	return -1, -1, -1
}

func clip(text string, chars int) string {
	runes := []rune(text)
	if len(runes) <= chars {
		return text
	}
	return strings.TrimRight(string(runes[:chars-1]), " \t\n") + "…"
}

func planRow(line string, fidelity machine.Fidelity) string {
	switch {
	case line != "" && fidelity == machine.FidelityPass:
		return line + " " + planChecked
	case fidelity == machine.FidelityPass:
		return planChecked
	}
	return line
}
