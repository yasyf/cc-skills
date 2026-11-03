package handoff

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
)

const (
	progressCap = 40_000
	untitled    = "text before the first section"
)

var (
	fence  = regexp.MustCompile("(?m)^(`{3,}|~{3,})")
	level2 = regexp.MustCompile(`(?m)^## (?:[^#\n].*)?$`)
	level3 = regexp.MustCompile(`(?m)^### (?:[^#\n].*)?$`)
)

type mdSection struct {
	heading string
	body    string
}

func (s mdSection) title() string {
	return strings.TrimSpace(strings.TrimLeft(s.heading, "#"))
}

func (s mdSection) text() string {
	if s.heading == "" {
		return strings.Trim(s.body, "\n")
	}
	return strings.Trim(s.heading+"\n"+s.body, "\n")
}

type span struct {
	start int
	end   int
}

func fences(text string) []span {
	var spans []span
	opened, openedAt := "", -1
	for _, match := range fence.FindAllStringIndex(text, -1) {
		mark := text[match[0]:match[1]]
		if openedAt < 0 {
			opened, openedAt = mark, match[0]
			continue
		}
		if mark[0] == opened[0] && len(mark) >= len(opened) {
			spans = append(spans, span{openedAt, match[1]})
			openedAt = -1
		}
	}
	if openedAt >= 0 {
		spans = append(spans, span{openedAt, len(text)})
	}
	return spans
}

func splitSections(text string, heading *regexp.Regexp) (string, []mdSection) {
	spans := fences(text)
	var starts [][]int
	for _, match := range heading.FindAllStringIndex(text, -1) {
		if !slices.ContainsFunc(spans, func(s span) bool { return s.start < match[0] && match[0] < s.end }) {
			starts = append(starts, match)
		}
	}
	if len(starts) == 0 {
		return text, nil
	}
	sections := make([]mdSection, 0, len(starts))
	for at, match := range starts {
		end := len(text)
		if at+1 < len(starts) {
			end = starts[at+1][0]
		}
		sections = append(sections, mdSection{text[match[0]:match[1]], text[min(match[1]+1, end):end]})
	}
	return text[:starts[0][0]], sections
}

func largestSection(sections []mdSection) mdSection {
	largest := sections[0]
	for _, section := range sections[1:] {
		if len(section.text()) > len(largest.text()) {
			largest = section
		}
	}
	return largest
}

func oversized(markdown string) string {
	if len(markdown) <= progressCap {
		return ""
	}
	preamble, candidates := splitSections(markdown, level2)
	if strings.TrimSpace(preamble) != "" {
		candidates = append([]mdSection{{"", preamble}}, candidates...)
	}
	largest := largestSection(candidates)
	within := ""
	if _, parts := splitSections(largest.body, level3); len(parts) > 0 {
		inner := largestSection(parts)
		within = fmt.Sprintf(", most of it `%s` at %d bytes", inner.title(), len(inner.text()))
	}
	title := largest.title()
	if title == "" {
		title = untitled
	}
	return fmt.Sprintf(
		"progress record is %d bytes, over the %d-byte cap: its largest section is `%s` at %d bytes%s; trim that section, then write the record again",
		len(markdown), progressCap, title, len(largest.text()), within,
	)
}
