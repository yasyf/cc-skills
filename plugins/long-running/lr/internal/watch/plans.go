package watch

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	PlanNamespace = "deadline"

	planKind       = "deadline"
	unplannedClass = "deadline with no plan"
	ownerAttr      = "owner"
	deadlineAttr   = "deadline"
	stepAttr       = "step:"
	clockTime      = `\d{1,2}(?::\d{2})?\s*[AP]\.?M\.?|\d{1,2}:\d{2}`
	planWindow     = time.Hour
	clockWrap      = 12 * time.Hour
	dateWrap       = 183 * 24 * time.Hour
)

var (
	namedDeadline    = regexp.MustCompile(`(?i)\b(?:by|before|until|due(?:[ \t]+(?:by|at))?)[ \t]+(` + clockTime + `)\b`)
	leadingClock     = regexp.MustCompile(`(?i)^(?:` + clockTime + `)\b`)
	monthDay         = regexp.MustCompile(`(?i)^\.?,?[ \t]+(?:on[ \t]+)?((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\.?[ \t]+(\d{1,2})\b`)
	laterDay         = regexp.MustCompile(`(?i)^\.?,?[ \t]+(?:on[ \t]+)?(?:tomorrow|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b`)
	clockLayouts     = []string{"3:04PM", "3PM", "15:04"}
	ownerInWords     = regexp.MustCompile(`\bOwner:[ \t]+\S`)
	recordNumber     = regexp.MustCompile(`(?:^|[^\d.:@/])#?(\d{3,})\b`)
	machineLanes     = []string{Lane, "stall-watch", "desk-runner", lanes.Self}
	kindsOwingNoPlan = []string{"hold", "go", "lift", "owner"}
)

func Unplanned(records []client.Record, now time.Time) []Wait {
	var waits []Wait
	for _, record := range records {
		if record.Fields.Attrs[deadlineAttr] == "" && !namedDeadline.MatchString(record.Text) {
			continue
		}
		deadline := deadlineOf(record, restatedClocks(records, record))
		if deadline == "" || slices.Contains(record.To, "owner") || record.Kind == "escalation" || slices.Contains(kindsOwingNoPlan, string(record.Kind)) || record.Status == "closed" || now.Sub(record.At) > planWindow || slices.Contains(machineLanes, record.Lane) || planned(record) || settled(records, record.Seq) {
			continue
		}
		waits = append(waits, Wait{
			Kind: planKind, Repo: PlanNamespace, Number: int(record.Seq), Mover: machine.MoverLane, Since: record.At, To: record.Lane,
			Cause: fmt.Sprintf("%s: %s from %s names a deadline by %s and carries no owner, steps or times: %s; %s owes a reply carrying --attr %s=<lane> and one --attr %s<n>=<clock> <what> per step", unplannedClass, record.Name(), record.Lane, deadline, clip(record.Text), record.Lane, ownerAttr, stepAttr),
		})
	}
	return waits
}

func deadlineOf(record client.Record, restated []string) string {
	if due := record.Fields.Attrs[deadlineAttr]; due != "" && !slices.Contains(restated, clockKey(due)) {
		return due
	}
	for _, at := range namedDeadline.FindAllStringSubmatchIndex(record.Text, -1) {
		clock, rest := record.Text[at[2]:at[3]], record.Text[at[3]:]
		if !strings.HasSuffix(strings.ToLower(strings.TrimSpace(record.Text[:at[0]])), "since") && !strings.HasPrefix(rest, ":") && !slices.Contains(restated, clockKey(clock)) && stillAhead(clock, rest, record.At) {
			return clock
		}
	}
	return ""
}

func stillAhead(clock, rest string, written time.Time) bool {
	at, ok := parsedClock(clock)
	if !ok {
		return false
	}
	if laterDay.MatchString(rest) {
		return true
	}
	written = written.In(pacific)
	if named := monthDay.FindStringSubmatch(rest); named != nil {
		date, err := time.Parse("Jan 2", named[1][:3]+" "+named[2])
		if err != nil {
			return false
		}
		due := time.Date(written.Year(), date.Month(), date.Day(), at.Hour(), at.Minute(), 0, 0, pacific)
		if written.Sub(due) > dateWrap {
			due = due.AddDate(1, 0, 0)
		}
		return due.After(written)
	}
	due := time.Date(written.Year(), written.Month(), written.Day(), at.Hour(), at.Minute(), 0, 0, pacific)
	if written.Sub(due) > clockWrap {
		due = due.AddDate(0, 0, 1)
	}
	return due.After(written)
}

func parsedClock(clock string) (time.Time, bool) {
	compact := strings.ToUpper(strings.ReplaceAll(strings.Join(strings.Fields(clock), ""), ".", ""))
	for _, layout := range clockLayouts {
		if at, err := time.Parse(layout, compact); err == nil {
			return at, true
		}
	}
	return time.Time{}, false
}

func restatedClocks(records []client.Record, reply client.Record) []string {
	cited := citedRecords(reply.Text)
	if reply.Re == 0 && len(cited) == 0 {
		return nil
	}
	var clocks []string
	for _, earlier := range records {
		if earlier.Seq >= reply.Seq || !restates(reply, earlier, cited) {
			continue
		}
		for _, named := range namedDeadline.FindAllStringSubmatch(earlier.Text, -1) {
			clocks = append(clocks, clockKey(named[1]))
		}
		for _, value := range earlier.Fields.Attrs {
			if key := clockKey(leadingClock.FindString(value)); key != "" {
				clocks = append(clocks, key)
			}
		}
	}
	return clocks
}

func restates(reply, earlier client.Record, cited []int64) bool {
	cites := earlier.Seq == reply.Re || slices.Contains(cited, earlier.Seq)
	if planned(earlier) {
		return cites || reply.Re != 0 && earlier.Re == reply.Re
	}
	return cites && !slices.Contains(kindsOwingNoPlan, string(earlier.Kind))
}

func citedRecords(text string) []int64 {
	var seqs []int64
	for _, named := range recordNumber.FindAllStringSubmatch(text, -1) {
		if seq, err := strconv.ParseInt(named[1], 10, 64); err == nil {
			seqs = append(seqs, seq)
		}
	}
	return seqs
}

func clockKey(clock string) string {
	at, ok := parsedClock(clock)
	if !ok {
		return ""
	}
	return at.Format("15:04")
}

func planned(record client.Record) bool {
	if ownerInWords.MatchString(record.Text) && namedDeadline.MatchString(record.Text) {
		return true
	}
	attrs := record.Fields.Attrs
	steps := 0
	for key, step := range attrs {
		if !strings.HasPrefix(key, stepAttr) {
			continue
		}
		if !leadingClock.MatchString(step) {
			return false
		}
		steps++
	}
	return attrs[ownerAttr] != "" && steps > 0
}

func settled(records []client.Record, seq int64) bool {
	return slices.ContainsFunc(records, func(later client.Record) bool {
		return later.Resolves == seq || slices.Contains(later.AlsoResolves, seq) || later.Re == seq && planned(later)
	})
}
