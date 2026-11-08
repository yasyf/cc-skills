package prrow

import (
	"cmp"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
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
	StateExempt     = "exempt"
	StateQueued     = "queued"
	StateEvicted    = "evicted"
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

	NotGraded      = "not graded at this head"
	AlreadyLanded  = "Nothing: it landed."
	RedAtHead      = "CI is red at this head"
	NoHeadYet      = "no record names its head yet"
	ClosedUnlanded = "Nothing: it was closed without landing."
	IsDraft        = "it is a draft"

	heldLead       = "held, "
	verdictLead    = "a grade needs a verdict"
	onlyHim        = "Only your approval."
	tapEndsHold    = "Only your approval at this head, which ends the hold: "
	tappedWaits    = "You approved it; the landing desk lands it next."
	fieldChars     = 700
	planChecked    = "Checked against the plan by the fidelity review at this version."
	planOverridden = "The fidelity review's finding was overridden at this version, so the plan check did not pass it."
	PortLands      = "Nothing from you: a clean old-tree port lands on your standing word that ports need no tap, and its go comes next."
	FlakeLands     = "Nothing from you: a test-flake fix lands on your standing word that flake fixes need no tap, and its go comes next."
)

var (
	heading     = regexp.MustCompile(`(?m)^#{1,3}\s+(.+?)\s*$`)
	holdLane    = regexp.MustCompile(`^[a-z][\w-]*:`)
	holdNoise   = regexp.MustCompile(`(?i)\s*\((?:record #\d+|replaces [^)]*|[0-9a-f]{7,40}|\d{5,}(?:@[0-9a-f]+)?)\)`)
	holdLeadIn  = regexp.MustCompile(`(?i)^(?:\d{1,2}:\d{2} [AP]M )?(?:[\w-]+ holds?|hold(?: on)?) (?:#?\d+|this PR|PR \d+)(?: widened)?[ :,]*`)
	sentenceEnd = regexp.MustCompile(`[.!?](?:\s|$)`)
	planLine    = regexp.MustCompile(`(?im)^(?:[\s>*-]*plan:.*|.*\b(?:plan (?:step|row|\d)|rule/[0-9a-f]{6,}|appendix|owner:m\d+|cc-notes task\W+[0-9a-f]{7,})\b.*)$`)
	planNamed   = regexp.MustCompile(`(?i)\b(?:step \d+|owner:m\d+|cc-notes task\W+[0-9a-f]{7,})\b`)
	itemList    = regexp.MustCompile(`(?i)\bitems? \d+(?:(?:, and |, | and )\d+)*\b`)
	itemNumber  = regexp.MustCompile(`\d+`)
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
		if row.State == StateOpen || row.State == StateExempt {
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

var ErrRowsNotFolded = errors.New("the landing store holds no review row yet, so no approval can be judged until the first fold")

func firstUnmet(row store.ReviewRow) string {
	return unmet(row, true)
}

// TapAt names the head his approval stands at and what refuses it. A hold, a check
// still running or failing, or a line missing from the PR body, refuses nothing, since the landing pass lands only a
// row that is both ready and tapped; a head the row has moved past keeps his
// approval there, for the fold's carry rule to judge.
func TapAt(s *store.Store, repo string, tap bus.Tap) (head, refusal string, err error) {
	row, found, err := s.ReviewRow(repo, tap.PR)
	if err != nil {
		return "", "", err
	}
	if !found {
		refusal, err = gates.NoRowIn(s, repo)
		if refusal == gates.NoStore {
			return "", "", ErrRowsNotFolded
		}
		return tap.Head, refusal, err
	}
	head = tap.Head
	if strings.HasPrefix(row.Head, tap.Head) {
		head = row.Head
	}
	return head, TapRefusal(row), nil
}

// TapRefusal names what refuses his approval of a row, empty when it counts.
func TapRefusal(row store.ReviewRow) string {
	switch {
	case row.ReadyForTap:
		return ""
	case LandsOnHisWord(row):
		return row.WaitsOn
	}
	return unmet(row, false)
}

// LandsOnce says what an approval on an open row lands behind, reading the
// checks in the order unmet reads them.
func LandsOnce(row store.ReviewRow) string {
	switch {
	case row.Hold != None:
		return strings.TrimSuffix("its hold lifts: "+heldWords(row.Hold), ": ")
	case row.State == StateQueued:
		return "the merge queue takes it"
	case row.ParentState == ParentOpen:
		return fmt.Sprintf("the change below it, %s, lands", row.Parent)
	case row.ParentState == ParentClosed:
		return fmt.Sprintf("the change below it, %s, is replaced", row.Parent)
	case row.ParentState == ParentUnknown:
		return fmt.Sprintf("the change below it, on branch %s, has a row", row.Parent)
	case row.CI != CIGreen:
		return "CI is green at this version"
	case row.Approvers == None:
		return "it has an approving review at this version"
	case row.Grade == GradePending:
		return "its review grade passes"
	case row.Grade != GradePass:
		return "its review grade gets a verdict"
	case row.Mergeable != string(machine.MergeableClean):
		return "it can merge cleanly"
	case row.ForUsers == "":
		return "its PR body has a Summary"
	case row.PlanRow == "":
		return "its PR body names its plan"
	case row.Risk == "":
		return "its PR body has a Risk profile"
	}
	return "its checks pass"
}

func unmet(row store.ReviewRow, checks bool) string {
	switch {
	case row.State == StateLanded:
		return AlreadyLanded
	case row.State == StateClosed:
		return ClosedUnlanded
	case row.SupersededBy != None:
		return "Nothing: superseded, " + row.SupersededBy
	case row.State == StateDraft:
		return IsDraft
	case checks && row.Hold != None:
		return heldLead + row.Hold
	case checks && row.State == StateQueued:
		return "it is in the merge queue"
	case checks && row.ParentState == ParentOpen:
		return fmt.Sprintf("the change below it, %s, has not landed", row.Parent)
	case checks && row.ParentState == ParentClosed:
		return fmt.Sprintf("the change below it, %s, was closed without landing", row.Parent)
	case checks && row.ParentState == ParentUnknown:
		return fmt.Sprintf("the change below it, on branch %s, has no row", row.Parent)
	case row.Head == "":
		return NoHeadYet
	case checks && row.CI == CIRed:
		return RedAtHead
	case checks && row.CI != CIGreen:
		return "CI is running or not read yet at this head"
	case checks && row.Approvers == None:
		return "no approving review at this head"
	case checks && row.Grade == GradePending:
		return NotGraded
	case checks && row.Grade != GradePass:
		return "a grade needs a verdict: " + row.GradeFindings
	case checks && row.Mergeable != string(machine.MergeableClean):
		return "mergeability is " + row.Mergeable
	case row.UnsafeWait != "":
		return gates.UnsafeOnDev(row.UnsafeWait)
	case checks && row.ForUsers == "":
		return "its PR body has no Summary"
	case checks && row.PlanRow == "":
		return `its PR body names no plan: open a line with "Plan:", or name plan step N, owner:m<row> or a cc-notes task id`
	case checks && row.Risk == "":
		return "its PR body has no Risk profile"
	}
	return ""
}

// BlocksWords is what stands between a row and landing, as he reads it on a
// card. WaitsOn stays the record of it that gates and lanes read.
func BlocksWords(row store.ReviewRow) string {
	switch {
	case row.Hold != None && strings.HasPrefix(row.WaitsOn, heldLead):
		return strings.TrimSpace("Held: " + heldWords(row.Hold))
	case row.WaitsOn == NotGraded:
		return "Its review grade is still running at this version."
	case strings.HasPrefix(row.WaitsOn, verdictLead):
		return "A review grade needs a verdict at this version."
	}
	first, size := utf8.DecodeRuneInString(row.WaitsOn)
	if !unicode.IsLower(first) {
		return row.WaitsOn
	}
	return string(unicode.ToUpper(first)) + strings.TrimSuffix(row.WaitsOn[size:], ".") + "."
}

func heldWords(hold string) string {
	said := holdNoise.ReplaceAllString(holdLane.ReplaceAllString(hold, ""), "")
	said = holdLeadIn.ReplaceAllString(strings.TrimSpace(said), "")
	if end := sentenceEnd.FindStringIndex(said); end != nil {
		said = said[:end[0]+1]
	}
	return said
}

func Items(text string) []string {
	var items []string
	for _, list := range itemList.FindAllString(text, -1) {
		for _, number := range itemNumber.FindAllString(list, -1) {
			if !slices.Contains(items, number) {
				items = append(items, number)
			}
		}
	}
	return items
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

func planRow(line string, fidelity machine.Fidelity, overridden string) string {
	said := ""
	switch {
	case fidelity == machine.FidelityPass:
		said = planChecked
	case overridden != "":
		said = planOverridden
	}
	return strings.TrimSpace(line + " " + said)
}

func Exempted(row store.ReviewRow, facts map[string]string) bool {
	return gates.TaplessClass(facts["exempt"]) && row.AwaitsTap == ""
}

func WordsWait(row store.ReviewRow) string {
	if sameHead(row.ReadHead, row.Head) {
		return ""
	}
	return "waits on a read of its GitHub title and body at " + cmp.Or(shortHead(row.Head), "a head no record names yet")
}

// LandsOnHisWord reports a row that lands on a standing word of his with no tap.
func LandsOnHisWord(row store.ReviewRow) bool {
	return row.WaitsOn == PortLands || row.WaitsOn == FlakeLands
}
