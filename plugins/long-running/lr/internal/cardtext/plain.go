package cardtext

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

const (
	maxOptionSentences = 2
	maxProseCounts     = 3
)

var (
	CorpusRow = regexp.MustCompile(`\b(?:[A-OQ-Z]\d{2,4}|T-[A-Z]{2,}|REG-\d+)\b`)

	unplain = []struct {
		what  *regexp.Regexp
		means string
	}{
		{CorpusRow, "a test corpus row id; name what the test checks in one plain sentence"},
		{regexp.MustCompile(`\bTest[A-Z]\w*|\btest_\w+|\b\w+\.(?:e2e|test|spec)\.tsx?\b|\b\w+_test\.go\b`), "a test's code name; name what the test checks in one plain sentence"},
		{regexp.MustCompile(`(?i)\bbuild #?\d{4,}\b|\bjob [0-9a-f-]{8,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b`), "a build or job name; say what failed or passed"},
		{regexp.MustCompile(`(?i)\b(?:plan (?:step |row )?\d+(?:\.\d+)*|step \d+(?:\.\d+)*|phase \d+)\b`), "a plan step; say what it does for a user"},
		{regexp.MustCompile(`\bm\d{3,}\b|\b\d{4,}@[0-9a-f]{6,}\b|(?i)\b(?:cci|ccn|record|seq|answer|order) #?[0-9a-f]*\d[0-9a-f]*\b|\bcard-[a-z0-9-]+|\bmerge:\d+:\w+`), "a record id"},
		{regexp.MustCompile(`\b(?:dd|sentry)-\d+(?:-\w+)?\b|\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-(?:fix|desk|evidence|comms|relay|watchdog)(?:-\d+)?\b|\b[a-z]+(?:-[a-z]+)+-\d+\b`), "a lane or incident name; say who does it in plain words, or leave it out"},
	}
	codeTerm    = regexp.MustCompile("`[^`\n]+`" + `|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b[A-Za-z_]\w*(?:\.\w+)*\(\)|\b[A-Za-z]\w*\.[A-Za-z]\w*\.[A-Za-z]\w*\b|\b[a-z]\w*\.[A-Z]\w*\b|(?:^|\s)/[a-z][\w/{}.-]*`)
	proseCount  = regexp.MustCompile(`\b\d+(?:\.\d+)?\b`)
	notACount   = regexp.MustCompile(`(?i)#\d+|\b\d+ ?min(?:utes?)? \d+ ?s(?:ec(?:onds?)?)?\b|\b\d{1,2}:\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}(?:,? \d{4})?\b`)
	hash        = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	sentenceEnd = regexp.MustCompile(`[.!?](?:\s+|$)`)
	letters     = regexp.MustCompile(`[^\p{L}\p{N}]+`)
	mdLabel     = regexp.MustCompile(`!?\[([^\]\n]*)\]\([^)\n]*\)`)
	bareURL     = regexp.MustCompile(`<?https?://[^\s>)]+>?`)
)

func Unplain(text string) string {
	shown := bareURL.ReplaceAllString(mdLabel.ReplaceAllString(HiddenCite.ReplaceAllString(text, " "), "$1"), " ")
	for _, rule := range unplain {
		if found := rule.what.FindString(shown); found != "" {
			return fmt.Sprintf("%q is %s", found, rule.means)
		}
	}
	for _, word := range hash.FindAllString(shown, -1) {
		if strings.ContainsAny(word, "0123456789") && strings.ContainsAny(word, "abcdef") {
			return fmt.Sprintf("%q is a commit hash; name the PR instead", word)
		}
	}
	if term := codeTerm.FindString(shown); term != "" {
		return fmt.Sprintf("%q is a code term; say what it does for a user in plain words", strings.TrimSpace(term))
	}
	return ""
}

func countsInProse(body string) string {
	for _, line := range strings.Split(HiddenCite.ReplaceAllString(body, " "), "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "|") {
			continue
		}
		if counts := proseCount.FindAllString(notACount.ReplaceAllString(bareURL.ReplaceAllString(line, " "), " "), -1); len(counts) >= maxProseCounts {
			return fmt.Sprintf("refused: the line %q carries %d counts; put counts in a short table or a line of their own", strings.TrimSpace(line), len(counts))
		}
	}
	return ""
}

func PlainCard(title, body string, options []string) error {
	if len(options) > 0 && (!strings.HasSuffix(strings.TrimSpace(title), "?") || sentences(title) != 1) {
		return fmt.Errorf("refused: the title %q is not one question in one sentence; ask what he decides in one sentence ending with a question mark", title)
	}
	if first := sentenceEnd.Split(strings.TrimSpace(HiddenCite.ReplaceAllString(body, " ")), 2)[0]; same(first, title) {
		return fmt.Errorf("refused: the body opens by repeating the title %q; open it with what the change means for a user", title)
	}
	shownBody := HiddenCite.ReplaceAllString(body, " ")
	if len(options) > 0 && strings.Contains(bareURL.ReplaceAllString(shownBody, " "), "?") {
		return fmt.Errorf("refused: the body asks a second question; a card asks one, in its title")
	}
	paragraphs := strings.Split(strings.TrimSpace(shownBody), "\n\n")
	for at, paragraph := range paragraphs {
		for _, earlier := range paragraphs[:at] {
			if same(paragraph, earlier) {
				return fmt.Errorf("refused: the body says %q twice; say it once", strings.TrimSpace(paragraph))
			}
		}
	}
	if refusal := countsInProse(body); refusal != "" {
		return errors.New(refusal)
	}
	for _, option := range options {
		if count := sentences(option); count > maxOptionSentences {
			return fmt.Errorf("refused: the option %q runs %d sentences; say what picking it does in two at most", option, count)
		}
	}
	for _, said := range append([]string{title, body}, options...) {
		if word := Unplain(said); word != "" {
			return fmt.Errorf("refused: %s, and he reads cards in plain words", word)
		}
	}
	return nil
}

func PlainBlock(card json.RawMessage) error {
	var block struct {
		Title    string `json:"title"`
		Summary  string `json:"summary"`
		Children []struct {
			Type    string `json:"type"`
			MD      string `json:"md"`
			Options []struct {
				Label string `json:"label"`
				MD    string `json:"md"`
			} `json:"options"`
		} `json:"children"`
	}
	if err := json.Unmarshal(card, &block); err != nil {
		return err
	}
	body := []string{block.Summary}
	var options, labels []string
	for _, child := range block.Children {
		switch child.Type {
		case "markdown":
			body = append(body, child.MD)
		case "choice":
			for _, option := range child.Options {
				labels = append(labels, option.Label)
				if option.MD != "" {
					options = append(options, option.MD)
				}
			}
		}
	}
	if word := Unplain(strings.Join(labels, "\n")); word != "" {
		return fmt.Errorf("refused: %s, and he reads cards in plain words", word)
	}
	if len(labels) > 0 && len(options) == 0 {
		options = labels
	}
	return PlainCard(block.Title, strings.Join(body, "\n\n"), options)
}

func Described(text string, checks map[string]string) string {
	return CorpusRow.ReplaceAllStringFunc(text, func(id string) string {
		if said, found := checks[id]; found {
			return "the test that " + said
		}
		return "a test"
	})
}

func sentences(text string) int {
	count := 0
	for _, said := range sentenceEnd.Split(strings.TrimSpace(text), -1) {
		if strings.TrimSpace(said) != "" {
			count++
		}
	}
	return count
}

func same(a, b string) bool {
	a, b = strings.ToLower(letters.ReplaceAllString(a, "")), strings.ToLower(letters.ReplaceAllString(b, ""))
	return a != "" && a == b
}
