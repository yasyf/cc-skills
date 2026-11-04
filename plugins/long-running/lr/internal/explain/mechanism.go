package explain

import (
	"fmt"
	"regexp"
	"strings"
)

const (
	unproven = "Unproven:"
	cited    = "The run crashed because the cloud runner reads only migration 0 (#384714)."
	refusal  = "lr explain refuses this page: a sentence saying why something happened cites, in it or the sentence before, the record, PR, build, file:line, monitor, notebook or ccn id that proves it, or opens with `" + unproven + "`."
)

var (
	fenced    = regexp.MustCompile("(?s)```.*?```")
	unsaid    = regexp.MustCompile("`[^`\n]+`|\"[^\"\n]*\"|“[^”\n]*”|https?://\\S+")
	breaks    = regexp.MustCompile(`([.!?])\s+|\n+`)
	lead      = regexp.MustCompile(`^[\s>*_#-]*(?:\d+\.\s*)?`)
	mechanism = regexp.MustCompile(`(?i)\b(?:because|caus(?:e|ed|es|ing)|due to|mechanism|root[- ]cause|the flaw|which means|is why|led to|skew|so\b[^.;]*?\b(?:fail|crash|break|error)\w*|likely|probably|presumably)\b`)
	citation  = regexp.MustCompile(`(?i)#\d{3,}\b|/pull/\d+|\bPR \d+\b|\bbuilds?\s+#?\d{3,}\b|/builds/\d+|[\w./-]+\.\w+:\d+\b|\bmonitors?\s+#?\d+\b|/monitors/\d+|\bnotebooks?\s+#?\d+\b|/notebook/\d+|\b(?:ccn|answer|ruling|note|grant|log|investigation) [0-9a-f]{7,}\b`)
)

func Uncited(source []byte) []string {
	var found []string
	previous := ""
	for _, line := range strings.Split(breaks.ReplaceAllString(fenced.ReplaceAllString(string(source), "\n"), "$1\n"), "\n") {
		sentence := strings.TrimSpace(line)
		if sentence == "" {
			continue
		}
		said := lead.ReplaceAllString(unsaid.ReplaceAllString(sentence, " "), "")
		if mechanism.MatchString(said) && !strings.HasPrefix(said, unproven) && !citation.MatchString(previous+" "+sentence) {
			found = append(found, sentence)
		}
		previous = sentence
	}
	return found
}

func refuse(claims []string) error {
	return fmt.Errorf("%s Rewrite each sentence below, then run it again:\n- %s\nexample: %s", refusal, strings.Join(claims, "\n- "), cited)
}
