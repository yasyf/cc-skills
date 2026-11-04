package replay

import (
	"cmp"
	"path"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
)

var (
	readText = []*regexp.Regexp{
		launchedLine,
		endedLine,
		regexp.MustCompile(`\bRELAUNCHED [\w.-]+ as [\w.-]+`),
		regexp.MustCompile(`(?i)\bstand[ -]?down\s+[^.(;:\n]+`),
		regexp.MustCompile(`\b[\w.-]+ replaces [\w.-]+\b`),
		regexp.MustCompile(`\bhandoff [\w.-]+: doc [0-9a-f]{7,40}\b`),
		regexp.MustCompile(`\bowner=[\w.-]+`),
		regexp.MustCompile(`\blast-read=#?\d+\b`),
		regexp.MustCompile(`\bcard-[\w.-]*\w`),
		regexp.MustCompile(`#\d+\b`),
		regexp.MustCompile(`\bPARKED [\w.-]+:`),
		verbNamed,
	}
	verbNamed = regexp.MustCompile(`\b(?:closed|complete|done|park|retired|stop waking|unpark)\b|\bstand-down\b(\s+\w)?`)

	keptFields = []string{"seq", "at", "lane", "kind", "to", "re", "resolves", "also_resolves", "topic"}
	keptAttrs  = []string{"brief", "model", "effort", "role", "successor", "verb", "lane", "last-read", "placement", "successor-of"}
	keptRow    = []string{"lane", "live", "seen_at", "announced_at", "stood_down", "roster"}
)

func briefPath(written string) string {
	return "/scratch/" + path.Base(path.Dir(written)) + "/" + path.Base(written)
}

type span struct {
	start int
	text  string
}

func scrubbedText(text string) string {
	if strings.HasPrefix(text, "SPAWNED ") {
		words := strings.Fields(text)
		return "SPAWNED " + words[1] + " " + briefPath(words[2])
	}
	var found []span
	for _, pattern := range readText {
		for _, match := range pattern.FindAllStringSubmatchIndex(text, -1) {
			if pattern == verbNamed && match[2] >= 0 {
				continue
			}
			found = append(found, span{match[0], text[match[0]:match[1]]})
		}
	}
	slices.SortStableFunc(found, func(a, b span) int { return cmp.Compare(a.start, b.start) })
	kept := make([]string, 0, len(found))
	for _, one := range found {
		if !slices.Contains(kept, one.text) {
			kept = append(kept, one.text)
		}
	}
	joined := strings.Join(kept, "; ")
	if strings.HasSuffix(strings.TrimRight(text, " \t\n\r"), "?") {
		joined += "?"
	}
	return joined
}

func prsOf(refs any) []int {
	found, _ := refs.(map[string]any)
	listed, _ := found["prs"].([]any)
	numbers := make([]int, 0, len(listed))
	for _, ref := range listed {
		text, isText := ref.(string)
		if !isText {
			if number, isNumber := ref.(float64); isNumber {
				numbers = append(numbers, int(number))
			}
			continue
		}
		if number, err := strconv.Atoi(text[strings.LastIndex(text, "#")+1:]); err == nil {
			numbers = append(numbers, number)
		}
	}
	return numbers
}

func scrubbed(record map[string]any) map[string]any {
	out := map[string]any{}
	for _, key := range keptFields {
		if value, found := record[key]; found && value != nil {
			out[key] = value
		}
	}
	text, _ := record["text"].(string)
	out["text"] = scrubbedText(text)
	refs := map[string]any{"prs": prsOf(record["refs"])}
	if found, isMap := record["refs"].(map[string]any); isMap {
		if lanes, listed := found["lanes"].([]any); listed && len(lanes) > 0 {
			refs["lanes"] = lanes
		}
	}
	out["refs"] = refs
	attrs := map[string]any{}
	fields, _ := record["fields"].(map[string]any)
	given, _ := fields["attrs"].(map[string]any)
	for key, value := range given {
		if !slices.Contains(keptAttrs, key) {
			continue
		}
		if written, isText := value.(string); isText && key == "brief" {
			value = briefPath(written)
		}
		attrs[key] = value
	}
	out["fields"] = map[string]any{"attrs": attrs}
	return out
}

func scrubbedPatch(name string, patch *string) *string {
	if patch == nil || (!gates.Manifest(name) && !gates.Lockfile(name)) {
		return nil
	}
	lines := strings.Split(strings.TrimRight(*patch, "\n"), "\n")
	out := make([]string, 0, len(lines))
	for _, line := range lines {
		body := line[min(1, len(line)):]
		switch {
		case strings.HasPrefix(line, "@@"):
			out = append(out, "@@")
		case gates.Manifest(name) && gates.SectionLine(body):
			out = append(out, line)
		default:
			out = append(out, line[:min(1, len(line))]+"x")
		}
	}
	joined := strings.Join(out, "\n") + "\n"
	return &joined
}
