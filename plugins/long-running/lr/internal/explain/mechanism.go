package explain

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
)

const (
	unproven = "Unproven:"
	cited    = "The run crashed because the cloud runner reads only migration 0. <!-- cci #384714 -->"
	refusal  = "lr explain refuses this page: a sentence saying why something happened cites, in it or the sentence before, the record, PR, build, file:line, monitor, notebook or ccn id that proves it, in an HTML comment he never sees such as `<!-- cci #384714 -->`, or opens with `" + unproven + "`."
)

var (
	fenced     = regexp.MustCompile("(?s)```.*?```")
	citeAfter  = regexp.MustCompile(`(?s)([.!?])(\s*<!--.*?-->)`)
	unsaid     = regexp.MustCompile("`[^`\n]+`|\"[^\"\n]*\"|“[^”\n]*”|https?://\\S+")
	breaks     = regexp.MustCompile(`([.!?])\s+|\n+`)
	lead       = regexp.MustCompile(`^[\s>*_#-]*(?:\d+\.\s*)?`)
	mechanism  = regexp.MustCompile(`(?i)\b(?:because|caus(?:e|ed|es|ing)|due to|mechanism|root[- ]cause|the flaw|which means|is why|led to|skew|so\b[^.;]*?\b(?:fail|crash|break|error)\w*|likely|probably|presumably)\b`)
	prCite     = `#\d{3,}\b|/pull/\d+|\bPR \d+\b`
	recordCite = `\bbuilds?\s+#?\d{3,}\b|/builds/\d+|[\w./-]+\.\w+:\d+\b|\bmonitors?\s+#?\d+\b|/monitors/\d+|\bnotebooks?\s+#?\d+\b|/notebook/\d+|\bcci #?\d+\b|\b(?:ccn|answer|ruling|note|grant|log|investigation) [0-9a-f]{7,}\b`
	citation   = regexp.MustCompile(`(?i)` + prCite + `|` + recordCite)
	recorded   = regexp.MustCompile(`(?i)` + recordCite)
	prNamed    = regexp.MustCompile(`(?i)` + prCite + `|\bPRs?\b`)
	broke      = regexp.MustCompile(`(?i)\b(?:broke|breaks|redden\w*|(?:went|turned|goes|turns) red)\b`)
)

func Uncited(source []byte) []string {
	var found []string
	previous := ""
	for _, line := range strings.Split(breaks.ReplaceAllString(citeAfter.ReplaceAllString(fenced.ReplaceAllString(string(source), "\n"), "$2$1"), "$1\n"), "\n") {
		sentence := strings.TrimSpace(line)
		if sentence == "" {
			continue
		}
		said := lead.ReplaceAllString(unsaid.ReplaceAllString(sentence, " "), "")
		blames := prNamed.MatchString(sentence) && broke.MatchString(said)
		proof := citation
		if blames {
			proof = recorded
		}
		if (blames || mechanism.MatchString(said)) && !strings.HasPrefix(said, unproven) && !proof.MatchString(previous+" "+sentence) {
			found = append(found, sentence)
		}
		previous = sentence
	}
	return found
}

// Visible is what he reads of a page: its hidden cites stay in the source on disk and never reach him.
func Visible(source []byte) []byte {
	return cardtext.HiddenCite.ReplaceAll(source, nil)
}

func refuse(claims []string) error {
	return fmt.Errorf("%s Rewrite each sentence below, then run it again:\n- %s\nexample: %s", refusal, strings.Join(claims, "\n- "), cited)
}
