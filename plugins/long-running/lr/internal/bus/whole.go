package bus

import (
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
)

const (
	WholeFact     = "finder-whole"
	WholeOwed     = "owed"
	WholeAsked    = "finder: whole"
	WholeReadText = "over whole "
	wholeRead     = "read"
)

var (
	textPR      = regexp.MustCompile(`\b([\w.-]+/[\w.-]+)#(\d+)\b|(?:^|[^\w/])#(\d{4,})\b`)
	regradeWord = regexp.MustCompile(`(?i)\b` + regexp.QuoteMeta(finderDesk[0]) + `:\s*re-?grade\b([^;.]*)`)
	wholeOrder  = regexp.MustCompile(`(?i)\bwhole[- ](?:diff|pass)`)
)

func WholeAsks(r client.Record, repo string) []string {
	if !slices.Contains(r.To, finderDesk[0]) {
		return nil
	}
	switch string(r.Kind) {
	case "decide", "defect":
		if client.Stem(r.Lane) != rootLane {
			return nil
		}
		if wholeOrder.MatchString(r.Text) {
			return askedPRs(r, repo)
		}
		var prs []string
		for _, order := range regradeWord.FindAllStringSubmatch(r.Text, -1) {
			prs = append(prs, textPRs(order[1], repo)...)
		}
		return unique(prs)
	case "ask":
		if !strings.Contains(strings.ToLower(r.Text), WholeAsked) {
			return nil
		}
		return askedPRs(r, repo)
	}
	return nil
}

func askedPRs(r client.Record, repo string) []string {
	if len(r.Refs.PRs) == 0 {
		return textPRs(r.Text, repo)
	}
	prs := make([]string, 0, len(r.Refs.PRs))
	for _, named := range r.Refs.PRs {
		if named.Repo == "" || strings.EqualFold(named.Repo, repo) {
			prs = append(prs, strconv.Itoa(named.Number))
		}
	}
	return prs
}

func textPRs(text, repo string) []string {
	var named, bare []string
	for _, found := range textPR.FindAllStringSubmatch(text, -1) {
		switch {
		case found[3] != "":
			bare = append(bare, found[3])
		case strings.EqualFold(found[1], repo):
			named = append(named, found[2])
		}
	}
	if len(named) > 0 {
		return unique(named)
	}
	return unique(bare)
}

func unique(list []string) []string {
	kept := make([]string, 0, len(list))
	for _, entry := range list {
		if !slices.Contains(kept, entry) {
			kept = append(kept, entry)
		}
	}
	return kept
}

func wholeFacts(r client.Record, repo string) []Fact {
	if string(r.Kind) == "review" && delegated(r.Lane, finderDesk) && strings.Contains(r.Text, WholeReadText) {
		return factsPerPR(r, repo, "", WholeFact, wholeRead)
	}
	var out []Fact
	for _, pr := range WholeAsks(r, repo) {
		if number, err := strconv.Atoi(pr); err == nil {
			out = append(out, Fact{number, "", WholeFact, WholeOwed})
		}
	}
	return out
}
