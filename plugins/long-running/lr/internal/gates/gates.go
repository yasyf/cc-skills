package gates

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	DiffFact     = "diff"
	BehaviorFact = "behavior"
	SinceFact    = "behavior-since"
	verdictFact  = "verdict"
	ChangedFact  = "what-changed"
	MissingFact  = "gates-missing"
	fidelityFact = "fidelity"

	BehaviorSame    = "same"
	BehaviorChanged = "changed"

	BySameDiff = "same-diff"
	ByFidelity = "fidelity"
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
	safeOnDev = regexp.MustCompile(`(?i)\b(?:feature flag|flag(?:ged)? (?:off|behind)|behind a (?:flag|gate)|code gate|gated (?:by|behind|on)|inert|nothing (?:calls|runs|reads|uses)\b[^.\n]*\b(?:yet|until #\d+))`)
)

const waiter = `(?:this|it)(?: pr| stack| change| deletion| removal| drop)?`

func UnsafeWait(body string) string {
	wait := waitsLive.FindStringSubmatch(body)
	if wait == nil || safeOnDev.MatchString(body) {
		return ""
	}
	return strings.Trim(wait[1], " *")
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

func CarriedBy(tapped string, atTap, atHead map[string]string) string {
	if atTap[DiffFact] != "" && atTap[DiffFact] == atHead[DiffFact] {
		return BySameDiff
	}
	if sameHead(atHead[SinceFact], tapped) && atHead[BehaviorFact] == BehaviorSame && (atHead[fidelityFact] == string(machine.FidelityPass) || atHead[verdictFact] != "") {
		return ByFidelity
	}
	return ""
}

func sameHead(a, b string) bool {
	return a != "" && (strings.HasPrefix(a, b) || strings.HasPrefix(b, a))
}

const NoTap = "no tap"

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
	} else if !f.Tapped() {
		out = append(out, NoTap)
	}
	if f.Hold != "" {
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
