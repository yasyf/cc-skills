package coordinator

import (
	"fmt"
	"io"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
)

const (
	fullShown       = 32000
	overrideWarning = "OVERRIDE-WARNING"
	wholeBatchFile  = "batch-last.txt"
)

type view struct {
	mutes []mute
	watch []int
	full  []string
	width int
	lines int
}

func (v view) wakes(r record) bool {
	return v.watching(r) || v.muteOf(r) < 0
}

func (v view) muteOf(r record) int {
	if cannotBeHidden(r) {
		return -1
	}
	return slices.IndexFunc(v.mutes, func(m mute) bool { return m.hides(r) })
}

func cannotBeHidden(r record) bool {
	return closesAtOnce(r) || r.Kind == kindHold || strings.Contains(r.Text, overrideWarning)
}

func (v view) watching(r record) bool {
	return slices.ContainsFunc(v.watch, func(pr int) bool { return watchedLine(r, pr) })
}

func watchedLine(r record, pr int) bool {
	if r.Kind != kindLanded && !strings.Contains(r.Text, overrideWarning) {
		return false
	}
	return r.names(pr)
}

var prInText = regexp.MustCompile(`#[0-9]+`)

func (r record) names(pr int) bool {
	return slices.Contains(r.PRs, pr) || slices.Contains(prInText.FindAllString(r.Text, -1), "#"+strconv.Itoa(pr))
}

func (v view) print(w io.Writer, batch []record, statuses []ccx.PRStatus, wholeBatch string, now time.Time) error {
	if len(batch) == 0 {
		_, err := fmt.Fprintln(w, "no records; run lr coordinator next again")
		return err
	}
	lines := fold(batch)
	var kept []line
	hidden := make([]int, len(v.mutes))
	muted := 0
	for _, l := range lines {
		if v.watching(l.record) {
			continue
		}
		if i := v.muteOf(l.record); i >= 0 {
			hidden[i] += l.seen
			muted += l.seen
			continue
		}
		kept = append(kept, l)
	}
	slices.SortStableFunc(kept, func(a, b line) int { return v.rank(a.record) - v.rank(b.record) })
	printed := 0
	cut := 0
	for i, l := range kept {
		if v.lines > 0 && printed >= v.lines {
			cut = len(kept) - i
			break
		}
		text := v.fit(l)
		printed += strings.Count(text, "\n") + 1
		if _, err := fmt.Fprintln(w, text); err != nil {
			return err
		}
	}
	if muted > 0 {
		if _, err := fmt.Fprintf(w, "muted %d: %s\n", muted, v.tally(hidden)); err != nil {
			return err
		}
	}
	if err := v.printWatch(w, lines, statuses, now); err != nil {
		return err
	}
	if cut > 0 {
		if _, err := fmt.Fprintf(w, "cut %d records past --lines %d\n", cut, v.lines); err != nil {
			return err
		}
	}
	if muted == 0 && cut == 0 {
		return nil
	}
	if err := writeWholeBatch(wholeBatch, lines); err != nil {
		return err
	}
	_, err := fmt.Fprintf(w, "whole batch: %s\n", wholeBatch)
	return err
}

func (v view) rank(r record) int {
	switch {
	case r.Kind == kindHold:
		return 0
	case v.lines > 0 && cannotBeHidden(r):
		return 1
	}
	return 2
}

func (v view) fit(l line) string {
	if slices.ContainsFunc(v.full, func(kind string) bool { return strings.EqualFold(kind, l.Kind) }) {
		return l.head() + ": " + l.upTo(fullShown) + l.repeats()
	}
	return cutAt(l.render(), v.width, l.Name())
}

func cutAt(text string, limit int, name string) string {
	if limit <= 0 || len(text) <= limit {
		return text
	}
	end := limit
	for end > 0 && !utf8.RuneStart(text[end]) {
		end--
	}
	return fmt.Sprintf("%s%s cut %d bytes; cci show %s --body", text[:end], cutMark, len(text)-end, strings.TrimPrefix(name, "#"))
}

func (v view) tally(hidden []int) string {
	var parts []string
	for i, count := range hidden {
		if count > 0 {
			parts = append(parts, fmt.Sprintf("%d %s", count, v.mutes[i].label()))
		}
	}
	return strings.Join(parts, ", ")
}

func (v view) printWatch(w io.Writer, lines []line, statuses []ccx.PRStatus, now time.Time) error {
	if len(v.watch) == 0 {
		return nil
	}
	if _, err := fmt.Fprintf(w, "watch %s:\n", now.In(pacific).Format("3:04 PM")); err != nil {
		return err
	}
	for _, pr := range v.watch {
		if _, err := fmt.Fprintln(w, statusLine(pr, statuses)); err != nil {
			return err
		}
		for _, l := range lines {
			if !watchedLine(l.record, pr) {
				continue
			}
			if _, err := fmt.Fprintln(w, "  "+cutAt(l.render(), v.width, l.Name())); err != nil {
				return err
			}
		}
	}
	return nil
}

func statusLine(pr int, statuses []ccx.PRStatus) string {
	i := slices.IndexFunc(statuses, func(s ccx.PRStatus) bool { return s.Number == pr })
	if i < 0 {
		return fmt.Sprintf("#%d no status", pr)
	}
	s := statuses[i]
	return fmt.Sprintf("#%d %s queue=%s ci=%s approval=%s mergeable=%s", pr, s.State, s.Queue, s.CI.State, s.Approval.State, s.Mergeability)
}

func writeWholeBatch(path string, lines []line) error {
	var whole strings.Builder
	for _, l := range lines {
		whole.WriteString(l.render())
		whole.WriteByte('\n')
	}
	return os.WriteFile(path, []byte(whole.String()), 0o600)
}
