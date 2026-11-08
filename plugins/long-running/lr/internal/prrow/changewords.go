package prrow

import (
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	changeOwed = "Not written yet: the lane that made this PR has been asked to say in its description what a user will see change."
	PlanForms  = "Plan: step <n>, Plan: owner:m<row>, Plan: cc-notes task <id> or Plan: bug list item <n>"
)

var (
	paragraphBreak = regexp.MustCompile(`\n\s*\n`)
	hexWord        = regexp.MustCompile(`\b[0-9a-f]{7,}\b`)
	recordWords    = regexp.MustCompile(`(?i)\b(?:record #\d+|cci \d+|exit(?:s| code)? \d+|[a-z][a-z0-9]*(?:-[a-z0-9]+)+ brief)\b`)
	codeTerm       = regexp.MustCompile("`[^`\n]+`" + `|\w+::\w+|(?:^|[\s(])/[a-z][\w{}/-]*|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b[A-Za-z_][\w.]*\(\)|\b[A-Za-z][\w.-]*/[A-Za-z][\w.-]*/[A-Za-z][\w./-]*|\b[\w.-]+/[\w.-]+\.(?:go|rs|ts|tsx|js|py|sql|sh|json|ya?ml|toml)\b`)
)

// ChangeWords is what changes for a user as he reads it on a card: the
// Summary with its plan lines left to Against the plan, or, when that leaves
// no plain sentence, that the row's lane owes one.
func ChangeWords(row store.ReviewRow) string {
	if ChangeFlaw(row.ForUsers) != "" {
		return changeOwed
	}
	return userParagraphs(row.ForUsers)
}

// CardGaps names each line a PR card cannot draw from the row: what it changes
// for a user, its plan row and its plain-words ELI5.
func CardGaps(row store.ReviewRow) []string {
	var gaps []string
	if flaw := ChangeFlaw(row.ForUsers); flaw != "" {
		gaps = append(gaps, "what it changes for a user: "+flaw)
	}
	if flaw := PlanFlaw(row.PlanLine); flaw != "" {
		gaps = append(gaps, "the plan row: "+flaw)
	}
	if ELI5Words(row) == "" {
		gaps = append(gaps, "its plain-words ELI5: the reviewer bot has posted none and the body has no Context section")
	}
	return gaps
}

// BodyGaps is CardGaps for a PR body before it reaches the ledger.
func BodyGaps(body string) []string {
	var row store.ReviewRow
	ApplyBody(&row, body)
	return CardGaps(row)
}

// ChangeFlaw names why a Summary cannot stand as What changes, empty when it can.
func ChangeFlaw(forUsers string) string {
	if forUsers == "" {
		return "its PR body has no Summary"
	}
	said := userParagraphs(forUsers)
	if said == "" {
		return "its Summary holds only plan text"
	}
	paragraphs := paragraphBreak.Split(said, -1)
	for at, paragraph := range paragraphs {
		if slices.Contains(paragraphs[:at], paragraph) {
			return "its Summary says the same paragraph twice"
		}
	}
	return unplainIn("Summary", said)
}

func PlanFlaw(line string) string {
	if line == "" {
		return "its PR body has no Plan: line; write " + PlanForms
	}
	if !planNamed.MatchString(line) {
		return fmt.Sprintf("its Plan: line %q names no plan step, owner:m<row> row, cc-notes task or bug list item; write %s", line, PlanForms)
	}
	return ""
}

func unplainIn(name, said string) string {
	if found := recordWords.FindString(said); found != "" {
		return fmt.Sprintf("its %s carries %q, a record a user never sees", name, found)
	}
	for _, word := range hexWord.FindAllString(said, -1) {
		if strings.ContainsAny(word, "0123456789") && strings.ContainsAny(word, "abcdef") {
			return fmt.Sprintf("its %s carries the hash %s", name, word)
		}
	}
	if found := codeTerm.FindString(cardtext.Link.ReplaceAllString(said, " ")); found != "" {
		return fmt.Sprintf("its %s carries %s, a code term a user never sees", name, strings.TrimLeft(found, " \t\n("))
	}
	if word := cardtext.Unplain(said); word != "" {
		return fmt.Sprintf("its %s carries %s", name, word)
	}
	return ""
}

func userParagraphs(forUsers string) string {
	return strings.TrimSpace(paragraphBreak.ReplaceAllString(planLine.ReplaceAllString(strings.ReplaceAll(forUsers, "\r\n", "\n"), ""), "\n\n"))
}
