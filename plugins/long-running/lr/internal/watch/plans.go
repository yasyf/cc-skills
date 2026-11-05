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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	PlanNamespace = "deadline"
	StepNamespace = "step"

	planKind       = "deadline"
	unplannedClass = "deadline with no plan"
	stepKind       = "step"
	missedClass    = "step clock passed"
	ownerRelay     = "owner-relay"
	rowAttr        = "row"
	deliveredAttr  = "delivered"
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
	ownerInWords     = regexp.MustCompile(`\bOwner:[ \t]+\S|\bowner[ \t]+[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b`)
	pastClause       = regexp.MustCompile(`^,?[ \t]+(?:I|we|it|they)[ \t]+(?:(?:had|have|already)[ \t]+)*(?:[a-z]+ed|was|were|had|sent|ran|wrote|got|made|took|saw|read|did|went|found|built|put)\b`)
	recordNumber     = regexp.MustCompile(`(?:^|[^\d.:@/])#?(\d{3,})\b`)
	machineLanes     = []string{Lane, "stall-watch", "desk-runner", lanes.Self}
	kindsOwingNoPlan = []string{"hold", "go", "lift", "owner"}
)

func Unplanned(records []client.Record, now time.Time) []Wait {
	replies := repliesIn(records)
	var waits []Wait
	for _, record := range records {
		if record.Fields.Attrs[deadlineAttr] == "" && !namedDeadline.MatchString(record.Text) {
			continue
		}
		deadline := deadlineOf(record, restatedClocks(records, record))
		if deadline == "" || slices.Contains(record.To, "owner") || record.Kind == "escalation" || slices.Contains(kindsOwingNoPlan, string(record.Kind)) || record.Status == "closed" || now.Sub(record.At) > planWindow || slices.Contains(machineLanes, record.Lane) || planned(record) || replies.settled(record.Seq) {
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
		if !strings.HasSuffix(strings.ToLower(strings.TrimSpace(record.Text[:at[0]])), "since") && !strings.HasPrefix(rest, ":") && !pastClause.MatchString(rest) && !slices.Contains(restated, clockKey(clock)) && stillAhead(clock, rest, record.At) {
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
	if !cites && (reply.Re == 0 || earlier.Re != reply.Re) {
		return false
	}
	if planned(earlier) {
		return true
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
	timed := false
	for key, step := range attrs {
		timed = timed || strings.HasPrefix(key, stepAttr) && leadingClock.MatchString(step)
	}
	return attrs[ownerAttr] != "" && timed
}

type replies struct {
	to        map[int64][]client.Record
	delivered map[string][]client.Record
}

func repliesIn(records []client.Record) replies {
	r := replies{to: map[int64][]client.Record{}, delivered: map[string][]client.Record{}}
	for _, record := range records {
		for _, seq := range slices.Compact(slices.Sorted(slices.Values(append([]int64{record.Re, record.Resolves}, record.AlsoResolves...)))) {
			if seq != 0 {
				r.to[seq] = append(r.to[seq], record)
			}
		}
		for _, delivered := range strings.FieldsFunc(record.Fields.Attrs[deliveredAttr], func(c rune) bool { return c == ',' || c == ' ' }) {
			if row, ok := strings.CutPrefix(delivered, "owner:"); ok {
				r.delivered[row] = append(r.delivered[row], record)
			}
		}
	}
	return r
}

func (r replies) settled(seq int64) bool {
	return slices.ContainsFunc(r.to[seq], func(later client.Record) bool {
		return later.Resolves == seq || slices.Contains(later.AlsoResolves, seq) || later.Re == seq && planned(later)
	})
}

func (r replies) answered(record client.Record, owner string, since time.Time) bool {
	fromOwner := func(later client.Record) bool {
		return later.Seq > record.Seq && later.At.After(since) && sameLane(later.Lane, owner)
	}
	row := record.Fields.Attrs[rowAttr]
	return slices.ContainsFunc(r.to[record.Seq], fromOwner) || row != "" && slices.ContainsFunc(r.delivered[row], fromOwner)
}

type step struct {
	name, clock, what string
	due               time.Time
}

func MissedSteps(records []client.Record, registered []store.Lane, now time.Time) []Wait {
	replies, began := repliesIn(records), generations(registered)
	var waits []Wait
	for _, record := range records {
		owner := record.Fields.Attrs[ownerAttr]
		generation, live := began[laneBase(owner)]
		if !live || record.At.Before(generation) || record.Status == "closed" || replies.settled(record.Seq) {
			continue
		}
		to := []string{owner}
		if !sameLane(owner, ownerRelay) && (slices.Contains(record.To, ownerRelay) || record.Fields.Attrs[rowAttr] != "") {
			to = append(to, ownerRelay)
		}
		since := record.At
		for _, step := range timedSteps(record) {
			if step.due.After(now) {
				break
			}
			if now.Sub(step.due) <= planWindow && !replies.answered(record, owner, since) {
				for _, each := range to {
					waits = append(waits, Wait{
						Kind: stepKind, Repo: StepNamespace, Number: int(record.Seq), Head: step.name, Mover: machine.MoverLane, Since: step.due, To: each, Owner: owner, Key: step.name + " " + each,
						Cause: fmt.Sprintf("%s: %s%s %s=%s %s from %s, owned by %s, passed its clock with no record from %s answering it since %s; %s owes a record with --re %d saying what is done and what is next, or one resolving it", missedClass, record.Name(), rowOf(record), step.name, step.clock, step.what, record.Lane, owner, owner, since.In(pacific).Format("3:04 PM"), owner, record.Seq),
					})
				}
			}
			since = step.due
		}
	}
	return waits
}

func generations(registered []store.Lane) map[string]time.Time {
	retired := map[string]time.Time{}
	for _, lane := range registered {
		if lane.State == store.LaneRetired {
			retired[lane.Name] = lane.Since
		}
	}
	began := map[string]time.Time{}
	for _, lane := range registered {
		if lane.State == store.LaneRetired {
			continue
		}
		family, launched := laneBase(lane.Name), retired[lane.Predecessor]
		if earlier, ok := began[family]; !ok || launched.Before(earlier) {
			began[family] = launched
		}
	}
	return began
}

func NextStep(records []client.Record, now time.Time) time.Time {
	replies := repliesIn(records)
	var next time.Time
	for _, record := range records {
		if record.Fields.Attrs[ownerAttr] == "" || record.Status == "closed" || replies.settled(record.Seq) {
			continue
		}
		for _, step := range timedSteps(record) {
			if step.due.After(now) && (next.IsZero() || step.due.Before(next)) {
				next = step.due
			}
		}
	}
	return next
}

func timedSteps(record client.Record) []step {
	var steps []step
	for key, value := range record.Fields.Attrs {
		clock := leadingClock.FindString(value)
		at, ok := parsedClock(clock)
		if !strings.HasPrefix(key, stepAttr) || !ok {
			continue
		}
		rest := strings.TrimSpace(value[len(clock):])
		steps = append(steps, step{name: key, clock: clock, what: rest, due: stepDue(at, rest, record.At)})
	}
	slices.SortFunc(steps, func(a, b step) int { return a.due.Compare(b.due) })
	return steps
}

func stepDue(at time.Time, rest string, written time.Time) time.Time {
	written = written.In(pacific)
	if named := monthDay.FindStringSubmatch(" " + rest); named != nil {
		if date, err := time.Parse("Jan 2", named[1][:3]+" "+named[2]); err == nil {
			due := time.Date(written.Year(), date.Month(), date.Day(), at.Hour(), at.Minute(), 0, 0, pacific)
			if written.Sub(due) > dateWrap {
				due = due.AddDate(1, 0, 0)
			}
			return due
		}
	}
	due := time.Date(written.Year(), written.Month(), written.Day(), at.Hour(), at.Minute(), 0, 0, pacific)
	if due.Before(written.Truncate(time.Minute)) {
		due = due.AddDate(0, 0, 1)
	}
	return due
}

func rowOf(record client.Record) string {
	if row := record.Fields.Attrs[rowAttr]; row != "" {
		return " row " + row
	}
	return ""
}

func sameLane(a, b string) bool {
	return laneBase(a) == laneBase(b)
}

func laneBase(name string) string {
	if found := numberedLane.FindStringSubmatch(name); found != nil {
		return found[1]
	}
	return name
}
