package prrow

import (
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	changeOwed = "Not written yet: the lane that made this PR has been asked to say in its description what a user will see change."
	PlanForms  = "Plan: step <n>, Plan: owner:m<row>, Plan: cc-notes task <id> or Plan: bug list item <n>"
)

var (
	paragraphBreak = regexp.MustCompile(`\n\s*\n`)
	portWord       = regexp.MustCompile(`(?i)\bport`)
	newTree        = regexp.MustCompile(`(?i)\bnew tree\b`)
	loopBreaker    = regexp.MustCompile(`(?i)\bloop[- ]breakers?\b`)
	resolverStays  = regexp.MustCompile(`(?i)\bLLM\b[^.\n]*\bresolver\b[^.\n]*\b(?:stays|remains|is kept|keeps)\b|\bkeeps?\b[^.\n]*\bLLM\b[^.\n]*\bresolver\b`)
	hexWord        = regexp.MustCompile(`\b[0-9a-f]{7,}\b`)
	recordWords    = regexp.MustCompile(`(?i)\b(?:record #\d+|cci \d+|exit(?:s| code)? \d+|[a-z][a-z0-9]*(?:-[a-z0-9]+)+ brief)\b`)
	archFence      = regexp.MustCompile("(?s)```arch[ \t]*\n(.*?)\n[ \t]*```")
	addsFunction   = regexp.MustCompile(`(?i)\b(?:adds?|new)\b[^.\n]{0,40}\b(?:builtins?|functions?)\b`)
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
// for a user, its plan row, its show-me and its plain-words ELI5.
func CardGaps(row store.ReviewRow) []string {
	var gaps []string
	if flaw := ChangeFlaw(row.ForUsers); flaw != "" {
		gaps = append(gaps, "what it changes for a user: "+flaw)
	}
	if flaw := portFlaw(row); flaw != "" {
		gaps = append(gaps, "that it is a port: "+flaw)
	}
	if flaw := PlanFlaw(row.PlanLine); flaw != "" {
		gaps = append(gaps, "the plan row: "+flaw)
	}
	if flaw := showMeFlaw(row); flaw != "" {
		gaps = append(gaps, "its show-me: "+flaw)
	}
	if ELI5Words(row) == "" {
		gaps = append(gaps, "its plain-words ELI5: the reviewer bot has posted none and the body has no Context section")
	}
	if said := row.ForUsers + "\n" + ELI5Words(row); loopBreaker.MatchString(said) && !resolverStays.MatchString(said) {
		gaps = append(gaps, "that the LLM resolver stays: it names a loop breaker, so its Summary or Context says in plain words that the LLM loop resolver building the initial DAG stays")
	}
	return gaps
}

// ShowMe is the PR body's Show me section: a picture of the change, as a
// screenshot or gif, or a fenced arch block lr reply draws as the card's
// picture.
func ShowMe(row store.ReviewRow) string {
	return section(row.Description, "Show me")
}

// ShowMeWords is the Show me section with its arch block taken out, since the
// card shows that block as its picture.
func ShowMeWords(row store.ReviewRow) string {
	return strings.TrimSpace(archFence.ReplaceAllString(ShowMe(row), ""))
}

// ShowMeDiagram is the Show me section's arch doc, nil when it has none.
func ShowMeDiagram(row store.ReviewRow) json.RawMessage {
	found := archFence.FindStringSubmatch(ShowMe(row))
	if found == nil {
		return nil
	}
	return json.RawMessage(found[1])
}

const archExample = "```arch\n" + `{"arch":1,"name":"inbox","title":"Where an answer goes","nodes":[{"id":"sub","kind":"service","name":"subagent","role":"writes its answer"},{"id":"row","kind":"store","name":"inbox row","role":"one per answer","medium":"disk"}],"edges":[{"from":"sub","to":"row"}]}` + "\n```"

func showMeFlaw(row store.ReviewRow) string {
	const drawn = "show him the change: a fenced arch block (arch/v1) the card draws as its picture, such as " + archExample + ", a screenshot or gif when the change is visual, or a sentence when that reads best"
	shown := ShowMe(row)
	if shown == "" {
		return "its PR body has no Show me section; " + drawn
	}
	if doc := ShowMeDiagram(row); doc != nil {
		if problems := arch.Problems(doc); len(problems) > 0 {
			return "its Show me arch block does not draw: " + strings.Join(problems, "; ") + "; a block that draws: " + archExample
		}
	}
	if addsFunction.MatchString(row.ForUsers+"\n"+section(row.Description, "Changes")) && !strings.Contains(shown, "```") {
		return "it adds a function, and its Show me section has no real Sand API call and its result in a code block"
	}
	return ""
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

func portFlaw(row store.ReviewRow) string {
	if !strings.HasPrefix(row.Port, PortOf) {
		return ""
	}
	lines := strings.SplitN(userParagraphs(row.ForUsers), "\n", 3)
	if !portWord.MatchString(lines[0]) {
		return fmt.Sprintf("it is a %s, and its Summary's first line does not say it is a port", row.Port)
	}
	if strings.Contains(row.ForUsers, "?") && (len(lines) < 2 || !newTree.MatchString(lines[1])) {
		return "its Summary asks him something, and its second line does not say what differs in the new tree that forces the choice"
	}
	return ""
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
