package rules

import (
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const answersPrefix = "answers:"

type Finding struct {
	Key  string `json:"key"`
	Line string `json:"line"`
}

type Settled struct {
	Finding
	Rule  string `json:"rule"`
	Words string `json:"words"`
}

func Settle(findings []Finding, picks []store.Rule, pull string) (open []Finding, settled []Settled) {
	for _, finding := range findings {
		pick, answered := pickFor(finding, picks, pull)
		if !answered {
			open = append(open, finding)
			continue
		}
		settled = append(settled, Settled{finding, pick.ID, pick.Words})
	}
	return open, settled
}

func pickFor(finding Finding, picks []store.Rule, pull string) (store.Rule, bool) {
	line := strings.ToLower(finding.Line)
	for _, pick := range picks {
		answers, isPick := strings.CutPrefix(pick.CheckRef, answersPrefix)
		if pick.CheckKind != "predicate" || !isPick || answers == "" || !slices.Contains(pick.PRs, pull) {
			continue
		}
		if answers == finding.Key || strings.Contains(line, strings.ToLower(answers)) {
			return pick, true
		}
	}
	return store.Rule{}, false
}
