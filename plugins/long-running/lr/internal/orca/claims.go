package orca

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var (
	briefSession   = regexp.MustCompile(`\bsess_[0-9a-z]{20,}\b`)
	briefThread    = regexp.MustCompile(`slack\.com/archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})`)
	noSlackGrant   = regexp.MustCompile(`(?i)\bno\b[^.\n]{0,40}\bgrant\b`)
	answerSearched = regexp.MustCompile("ccn answer search\\s+[\"'`]?[^<\\s\"'`]")
)

func briefClaims(text string) []string {
	sessions, threads := briefSession.FindAllString(text, -1), briefThread.FindAllStringSubmatch(text, -1)
	claims := make([]string, 0, len(sessions)+len(threads))
	for _, session := range sessions {
		claims = append(claims, "session:"+session)
	}
	for _, thread := range threads {
		claims = append(claims, "thread:"+thread[1]+"/"+thread[2]+"."+thread[3])
	}
	slices.Sort(claims)
	return slices.Compact(claims)
}

func (o Order) grantWarning(text string) string {
	if !noSlackGrant.MatchString(text) || answerSearched.MatchString(text) {
		return ""
	}
	return "brief " + o.Brief + " says no Slack grant covers its work but quotes no `ccn answer search <subject>`; search the owner's answers by subject and quote what each returns"
}

func (p Poster) claimedBeside(ctx context.Context, bus, lane string, claims []string) []string {
	var warnings []string
	for _, claim := range claims {
		result, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"cci", "who", "--drive", bus, "--ref", claim, "--json", "--fields", "lane"}})
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("cci who --ref %s did not read (%v), so nothing shows whether a live lane already works it", claim, err))
			continue
		}
		for line := range strings.Lines(result.Stdout) {
			var peer struct {
				Lane string `json:"lane"`
			}
			if json.Unmarshal([]byte(line), &peer) == nil && peer.Lane != "" && peer.Lane != lane {
				warnings = append(warnings, fmt.Sprintf("%s already claims %s, so %s launches beside it; hand the work to %s or say why both run", peer.Lane, claim, lane, peer.Lane))
			}
		}
	}
	return warnings
}

func (p Poster) claim(ctx context.Context, bus, lane string, claims []string) {
	if len(claims) == 0 {
		return
	}
	argv := []string{"cci", "claim", "--for", lane + "@" + bus}
	for _, claim := range claims {
		argv = append(argv, "--ref", claim)
	}
	if _, err := p.Runner.Run(ctx, shell.Command{Argv: argv}); err != nil {
		fmt.Fprintf(p.Out, "WARN %s did not claim %s: %v\n", lane, strings.Join(claims, ", "), err)
	}
}
