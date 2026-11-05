package update

import (
	"fmt"
	"slices"
	"strings"
)

const (
	foldNone = iota
	foldUnchanged
	foldLists
	foldPRs
	foldLanes
	foldSteps
	foldUnowned
	foldQuiet
	foldFYI
	foldDuties
	foldBlocked
	foldWords
	foldLast = foldWords

	newestFYIs = 3
	fyiChars   = 240
	wordsChars = 240
	stillSince = "No change since %s: %s"
	onItNow    = "On it, nothing blocking: "
	blockedNow = "Waiting on the coordinator: "
)

var foldNames = []string{
	foldUnchanged: "done and unchanged rows to one line per section",
	foldLists:     "what waits on the coordinator, deliveries and owed deletions to short lines",
	foldPRs:       "open PRs to their numbers",
	foldLanes:     "lanes to their blockers and names",
	foldSteps:     "every plan step to one line",
	foldUnowned:   "unowned lanes to counts",
	foldQuiet:     "quiet lanes to their names",
	foldFYI:       fmt.Sprintf("FYIs to the newest %d per group, each cut at %d characters", newestFYIs, fyiChars),
	foldDuties:    "standing duties to one line each",
	foldBlocked:   "blocked lanes to their names",
	foldWords:     fmt.Sprintf("the messages quoted from you to their first %d characters", wordsChars),
}

type folding struct {
	level  int
	before map[string][]string
	since  string
}

func (f folding) at(level int) bool {
	return f.level >= level
}

func (f folding) unchanged(s section, row Row) bool {
	old, known := f.before[snapshotKey(s, row)]
	return known && slices.Equal(old, facets(row))
}

func (f folding) oneLine(s section) bool {
	if !s.step || s.piece {
		return false
	}
	if f.at(foldSteps) {
		return true
	}
	return len(f.before) > 0 && !slices.ContainsFunc(s.rows, func(row Row) bool { return !f.unchanged(s, row) })
}

func (f folding) folds(s section, row Row, isLane bool) bool {
	if !f.at(foldUnchanged) || s.title == asksTitle || s.title == pinnedTitle || (s.step && !isLane) {
		return false
	}
	return row.Done || f.unchanged(s, row)
}

func rowLine(name string, row Row) string {
	line := fmt.Sprintf("- %s: %s; %s", name, cell(row.State), row.Who)
	if row.Blocks == nothingBlocks {
		return line
	}
	return fmt.Sprintf("%s; blocks: %s; next: %s", line, cell(row.Blocks), cell(row.Unblock))
}

func (s section) oneLine() string {
	line := rowLine(s.title, s.rows[0])
	var quiet []string
	for _, row := range s.rows[1:] {
		if row.Who == nobody {
			quiet = append(quiet, row.Name)
		}
	}
	if len(quiet) == 0 {
		return line
	}
	return line + "; quiet: " + strings.Join(quiet, ", ")
}

func closingLine(f folding, whole, stepLines, droppedLines int, droppedAfter string) string {
	var folded []string
	if stepLines > 0 && !f.at(foldSteps) {
		folded = append(folded, fmt.Sprintf("plan steps with no change to one line each (%d)", stepLines))
	}
	for level := foldUnchanged; level <= f.level; level++ {
		folded = append(folded, foldNames[level])
	}
	var said []string
	switch {
	case f.level > foldNone:
		said = append(said, fmt.Sprintf("Folded from %d KB to stay under %d KB: %s.", whole/1000, updateBytes/1000, strings.Join(folded, "; ")))
	case len(folded) > 0:
		said = append(said, "Folded: "+folded[0]+".")
	}
	if droppedLines > 0 {
		said = append(said, fmt.Sprintf("Dropped to fit: the %d lines after %q.", droppedLines, droppedAfter))
	}
	return strings.Join(said, " ")
}

func joined(parts ...[]string) string {
	return strings.Join(slices.Concat(parts...), "\n") + "\n"
}

func gapLines(gaps []string) []string {
	if len(gaps) == 0 {
		return nil
	}
	bulleted := make([]string, len(gaps))
	for i, gap := range gaps {
		bulleted[i] = "- " + gap
	}
	return titled(gapsTitle, bulleted)
}

func (d draft) fit(gaps []string) (string, bool) {
	f := folding{before: d.before, since: d.since}
	tail := gapLines(gaps)
	lines, stepLines := d.lines(f)
	whole := len(joined(lines, tail))
	closing := func(droppedLines int, after string) []string {
		line := closingLine(f, whole, stepLines, droppedLines, after)
		if line == "" {
			return nil
		}
		return []string{"", line}
	}
	for len(joined(lines, tail, closing(0, ""))) >= updateBytes && f.level < foldLast {
		f.level++
		lines, stepLines = d.lines(f)
	}
	if len(joined(lines, tail, closing(0, ""))) < updateBytes {
		return joined(lines, tail, closing(0, "")), false
	}
	kept := len(lines)
	after := ""
	for kept > 1 && len(joined(lines[:kept], tail, closing(len(lines)-kept, after))) >= updateBytes {
		kept--
		after = headingBefore(lines, kept)
	}
	return joined(lines[:kept], tail, closing(len(lines)-kept, after)), true
}

func headingBefore(lines []string, end int) string {
	for _, line := range slices.Backward(lines[:end]) {
		if strings.HasPrefix(line, "#") {
			return strings.TrimLeft(line, "# ")
		}
	}
	return lines[0]
}

func groupLines(sections []section, f folding) (lines []string, stepLines int) {
	var expanded []string
	for _, found := range sections {
		if f.oneLine(found) {
			lines = append(lines, found.oneLine())
			stepLines++
			continue
		}
		expanded = append(expanded, "", "### "+found.title)
		expanded = append(expanded, found.lines(f)...)
	}
	return append(lines, expanded...), stepLines
}

func (d draft) lines(f folding) ([]string, int) {
	out := slices.Concat([]string{d.header}, leadLines(d.lead), unownedStepsSection(d.steps), d.overdue, d.orders, titled(workstreamsTitle, d.streams))
	moved := d.moved(f)
	out = append(out, titled(d.sinceTitle, moved)...)
	perGroup := 0
	if f.at(foldFYI) {
		perGroup = newestFYIs
	}
	out = append(out, titled(fyiTitle, fyiLines(d.fyi, perGroup))...)
	words := d.words
	if f.at(foldWords) {
		words = make([]string, len(d.words))
		for i, line := range d.words {
			at := strings.LastIndex(line, " | ")
			words[i] = clipTo(line[:at], wordsChars) + line[at:]
		}
	}
	out = append(out, titled(wordsTitle, words)...)
	out = append(out, titled(unownedTitle, unownedLines(d.sections, f))...)
	stepLines := 0
	for at := 0; at < len(d.sections); {
		found := d.sections[at]
		if found.group == "" {
			out = append(out, titled(found.title, found.lines(f))...)
			if found.title == dutiesTitle {
				out = append(out, titled(toolingTitle, toolingLines(d.programs))...)
				if len(d.reap) > 0 {
					out = append(out, titled(reapTitle, d.reap)...)
				}
			}
			at++
			continue
		}
		end := at + 1
		for end < len(d.sections) && d.sections[end].group == found.group {
			end++
		}
		lines, folded := groupLines(d.sections[at:end], f)
		out = append(out, titled(found.group, lines)...)
		stepLines += folded
		at = end
	}
	for _, found := range d.lists {
		lines := found.lines
		if f.at(foldLists) && found.short != nil {
			lines = found.short
		}
		out = append(out, titled(found.title, lines)...)
	}
	return out, stepLines
}

func (d draft) moved(f folding) []string {
	if len(d.before) == 0 {
		return []string{firstUpdate}
	}
	return sinceLines(d.tracked, d.before, f.at(foldLists))
}
