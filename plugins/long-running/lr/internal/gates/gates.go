package gates

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	DiffFact     = "diff"
	BehaviorFact = "behavior"
	SinceFact    = "behavior-since"
	ChangedFact  = "what-changed"
	fidelityFact = "fidelity"

	BehaviorSame    = "same"
	BehaviorChanged = "changed"

	BySameDiff = "same-diff"
	ByFidelity = "fidelity"
)

var (
	hunkLines = regexp.MustCompile(`(?m)^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@`)
	blobLine  = regexp.MustCompile(`(?m)^index [0-9a-f]+\.\.[0-9a-f]+(?: \d+)?\n`)
)

func DiffHash(diff string) string {
	sum := sha256.Sum256([]byte(blobLine.ReplaceAllString(hunkLines.ReplaceAllString(diff, "@@"), "")))
	return hex.EncodeToString(sum[:])
}

func CarriedBy(tapped string, atTap, atHead map[string]string) string {
	if atTap[DiffFact] != "" && atTap[DiffFact] == atHead[DiffFact] {
		return BySameDiff
	}
	if atHead[SinceFact] == tapped && atHead[BehaviorFact] == BehaviorSame && atHead[fidelityFact] == string(machine.FidelityPass) {
		return ByFidelity
	}
	return ""
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
	return out
}
