package grader

import (
	"fmt"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/rules"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func answeredByPick(key, line string, picks []store.Rule, pull string) []rules.Settled {
	_, settled := rules.Settle([]rules.Finding{{Key: key, Line: line}}, picks, pull)
	return settled
}

func settledByPicks(settled []rules.Settled) string {
	named := make([]string, 0, len(settled))
	for _, pick := range settled {
		said := fmt.Sprintf("%s %q", pick.Rule, pick.Words)
		if !strings.Contains(strings.Join(named, ""), said) {
			named = append(named, said)
		}
	}
	return "settled by owner picks: " + strings.Join(named, ", ")
}

func (s *finderPassState) openFindings(findings []finding) ([]finding, []rules.Settled) {
	open := make([]finding, 0, len(findings))
	var settled []rules.Settled
	for _, found := range findings {
		answered := answeredByPick(found.Cite, found.Defect, s.job.Picks, s.job.pull())
		if len(answered) == 0 {
			open = append(open, found)
		}
		settled = append(settled, answered...)
	}
	return open, settled
}

func (s *fidelityPassState) settleByPicks(reply fidelityReply) fidelityReply {
	if reply.Verdict != "missing" {
		return reply
	}
	kept := make([]missingEntry, 0, len(reply.Missing))
	var settled []rules.Settled
	for _, entry := range reply.Missing {
		answered := answeredByPick(entry.Key, entry.Line, s.job.Picks, s.job.pull())
		if len(answered) == 0 {
			kept = append(kept, entry)
		}
		settled = append(settled, answered...)
	}
	if len(settled) == 0 {
		return reply
	}
	reply.Missing, reply.picks = kept, settledByPicks(settled)
	if len(kept) == 0 {
		reply.Verdict = "ok"
		reply.Summary = fmt.Sprintf("%s Every missing point is %s.", reply.Summary, reply.picks)
	}
	return reply
}
