package gates

import (
	"cmp"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	DiffFact     = "diff"
	BehaviorFact = "behavior"
	SinceFact    = "behavior-since"
	ChangedFact  = "what-changed"
	MissingFact  = "gates-missing"
	fidelityFact = "fidelity"

	BehaviorSame    = "same"
	BehaviorChanged = "changed"

	BySameDiff = "same-diff"
	ByFidelity = "fidelity"

	DeltaFact     = "delta"
	DeltaFromFact = "delta-from"
	DeltaWhyFact  = "delta-why"

	DeltaRebase     = "rebase"
	DeltaReviewFix  = "review-fix"
	DeltaOrderedFix = "ordered-fix"
	DeltaBehavior   = "behavior"
	DeltaScope      = "scope"

	classSeparator = ","
	unjudged       = "its diff changed, and neither its head record nor fidelity says it means the same yet"
)

var (
	OverrideFacts  = []string{machine.OverrideFact(finderKind), machine.OverrideFact(fidelityKind)}
	DeltaFacts     = []string{DeltaFact, DeltaFromFact, DeltaWhyFact}
	carryingWords  = map[string]string{DeltaRebase: "a rebase", DeltaReviewFix: "a review fix", DeltaOrderedFix: "a fix the owner ordered"}
	returningWords = map[string]string{DeltaBehavior: "a behavior change", DeltaScope: "a scope change"}
)

var (
	hunkLines = regexp.MustCompile(`(?m)^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@`)
	blobLine  = regexp.MustCompile(`(?m)^index [0-9a-f]+\.\.[0-9a-f]+(?: \d+)?\n`)
	waitsLive = regexp.MustCompile(`(?im)(?:^|[.;:!?,]\s+|\*\*|\b(?:so|and)\s+)[\s>*-]*(` +
		`(?:hold (?:this|it)\b[^.\n]*?\buntil\b` +
		`|held by (?:the owner|root|him)\b` +
		`|(?:` + waiter + ` )?(?:do not|don't|must not|never) (?:merge|land|ship)\b[^.\n]*?\b(?:until|before)\b` +
		`|` + waiter + `(?: is [^.;\n]{0,40}? and)? goes live (?:only )?(?:(?:one |a )?release after|in a release after|after|once|until)\b` +
		`|` + waiter + ` waits (?:on|for) (?:a |an |the |his |that |its |the owner's )?(?:[\w-]+ )?(?:card|tap|pick|release|hold|go|ruling|decision|cutover)\b` +
		`)[^.\n]*)`)
	deniedWait = regexp.MustCompile(`(?i)\bwaits (?:on|for) no\b`)
	safeOnDev  = regexp.MustCompile(`(?i)\b(?:feature flag|flag(?:ged)? (?:off|behind)|behind a (?:flag|gate)|code gate|gated (?:by|behind|on)|inert|nothing (?:calls|runs|reads|uses)\b[^.\n]*\b(?:yet|until #\d+))`)
)

const waiter = `(?:this|it)(?: pr| stack| change| deletion| removal| drop)?`

func UnsafeWait(body string) string {
	if safeOnDev.MatchString(body) {
		return ""
	}
	for _, wait := range waitsLive.FindAllStringSubmatch(body, -1) {
		if !deniedWait.MatchString(wait[1]) {
			return strings.Trim(wait[1], " *")
		}
	}
	return ""
}

func UnsafeOnDev(wait string) string {
	if wait == "" {
		return ""
	}
	return fmt.Sprintf("its body waits to go live (%q) and names no way it is safe on dev: a code gate, a feature flag, or inert until a named caller lands", wait)
}

func DiffHash(diff string) string {
	sum := sha256.Sum256([]byte(blobLine.ReplaceAllString(hunkLines.ReplaceAllString(diff, "@@"), "")))
	return hex.EncodeToString(sum[:])
}

func SameDiff(atFrom, atHead map[string]string) bool {
	return atFrom[DiffFact] != "" && atFrom[DiffFact] == atHead[DiffFact]
}

func CarriedOverrides(atFrom, atHead map[string]string) []string {
	if !SameDiff(atFrom, atHead) {
		return nil
	}
	var carried []string
	for _, grader := range []string{finderKind, fidelityKind} {
		if atHead[machine.OverrideFact(grader)] != "" {
			continue
		}
		if atFrom[machine.OverrideFact(grader)] != "" {
			carried = append(carried, grader)
		} else if grade := cmp.Or(atHead[grader], atFrom[grader]); grade != string(machine.FinderClear) && grade != string(machine.FidelityPass) {
			return nil
		}
	}
	return carried
}

func CarriedOverride(number int, head, from, grader, cites string) string {
	if cites == "accept" {
		cites = ""
	}
	return bus.Override{Grader: grader, PR: number, Head: head, Cites: cites}.Line(fmt.Sprintf("Carried from %s, whose own diff is the same.", short(from)))
}

func OverrideHeads(facts []store.HeadFact, head string) []string {
	var heads []string
	for _, fact := range slices.Backward(facts) {
		if !sameHead(fact.Head, head) && !slices.Contains(heads, fact.Head) {
			heads = append(heads, fact.Head)
		}
	}
	return heads
}

func CarriedBy(tapped, head string, atTap, atHead map[string]string, deltas []store.HeadFact) (by, returned string) {
	since := deltasSince(tapped, head, deltas)
	if SameDiff(atTap, atHead) {
		return cmp.Or(since.carrying(), BySameDiff), ""
	}
	if back := since.returning(); back != "" {
		return "", back
	}
	if atHead[BehaviorFact] == BehaviorChanged && sameHead(atHead[SinceFact], tapped) {
		return "", "fidelity judged a change: " + atHead[ChangedFact]
	}
	if by := since.carrying(); by != "" {
		return by, ""
	}
	if sameHead(atHead[SinceFact], tapped) && atHead[BehaviorFact] == BehaviorSame && (atHead[fidelityFact] == string(machine.FidelityPass) || atHead[machine.OverrideFact(fidelityKind)] != "") {
		return ByFidelity, ""
	}
	return "", unjudged
}

func CarryWhy(by string) string {
	switch by {
	case BySameDiff:
		return "the PR's own diff is the same as at the tapped head"
	case ByFidelity:
		return "fidelity judged no behavior or scope change since the tapped head"
	}
	var words []string
	for class := range strings.SplitSeq(by, classSeparator) {
		words = append(words, carryingWords[class])
	}
	return "every change since the tapped head is " + strings.Join(words, " or ")
}

type delta struct {
	head, class, why string
}

type deltaChain struct {
	links   []delta
	reached bool
}

func deltasSince(tapped, head string, deltas []store.HeadFact) deltaChain {
	var chain deltaChain
	at := head
	for range len(deltas) + 1 {
		if sameHead(at, tapped) {
			chain.reached = true
			return chain
		}
		said := factsAt(at, deltas)
		chain.links = append(chain.links, delta{at, said[DeltaFact], said[DeltaWhyFact]})
		at = said[DeltaFromFact]
		if at == "" {
			return chain
		}
	}
	return chain
}

func factsAt(head string, deltas []store.HeadFact) map[string]string {
	said := map[string]string{}
	for _, fact := range deltas {
		if sameHead(fact.Head, head) {
			said[fact.Kind] = fact.Value
		}
	}
	return said
}

func (c deltaChain) carrying() string {
	if !c.reached {
		return ""
	}
	var classes []string
	for _, link := range c.links {
		if _, carries := carryingWords[link.class]; !carries {
			return ""
		}
		classes = append(classes, link.class)
	}
	slices.Sort(classes)
	return strings.Join(slices.Compact(classes), classSeparator)
}

func (c deltaChain) returning() string {
	for _, link := range c.links {
		if words, returns := returningWords[link.class]; returns {
			return fmt.Sprintf("%s at %s: %s", words, short(link.head), link.why)
		}
	}
	return ""
}

func sameHead(a, b string) bool {
	return a != "" && (strings.HasPrefix(a, b) || strings.HasPrefix(b, a))
}

const (
	NoTap = "no tap"
	NoGo  = "NO-GO-YET no tap is owed: it lands on his standing word, and its go comes next"
)

func Missing(f machine.Facts) []string {
	var out []string
	if f.CI != machine.CISuccess {
		out = append(out, "CI is not green")
	}
	if f.Mergeable != machine.MergeableClean {
		out = append(out, fmt.Sprintf("mergeability is %s", f.Mergeable))
	}
	if len(f.Carries) > 0 {
		carried := make([]string, len(f.Carries))
		for i, number := range f.Carries {
			carried[i] = "#" + strconv.Itoa(number)
		}
		out = append(out, "branch carries "+strings.Join(carried, ", "))
	}
	if !f.Graded() {
		out = append(out, "not graded")
	} else if !f.Cleared() {
		out = append(out, "a grade needs a verdict")
	}
	if !f.Approved {
		out = append(out, "no approving review")
	}
	if f.Tap == machine.TapConditional {
		out = append(out, "tap given "+f.Condition)
	} else if f.StandingWord != "" && !f.Admitted() && !f.HoldAwaitsTap {
		out = append(out, NoGo)
	} else if !f.Admitted() || f.HoldAwaitsTap {
		out = append(out, NoTap)
	}
	if f.Hold != "" && !f.HoldAwaitsTap {
		out = append(out, fmt.Sprintf("held by %s", f.Hold))
	}
	if f.UnsafeWait != "" {
		out = append(out, UnsafeOnDev(f.UnsafeWait))
	}
	if !f.RowClear() && !slices.ContainsFunc(out, func(gate string) bool { return gate != NoTap }) {
		out = append(out, "its row waits on: "+f.RowWait)
	}
	return out
}
