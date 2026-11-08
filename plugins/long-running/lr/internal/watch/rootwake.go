package watch

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	wakeQuiet = 5 * time.Minute
	wakeAgain = 15 * time.Minute
	wakeDesk  = "orca-desk"
	wakeShown = 8
)

var clockNamed = regexp.MustCompile(`(?i)\b(?:clocks?\s+(?:at\s+)?(\d{1,2}):(\d{2})(?:\s?([AP])M)?|(\d{1,2}):(\d{2})(?:\s?([AP])M)?\s+clock)\b`)

type RootWake struct {
	Subject string
	Line    string
}

func WakeRoot(records, open []client.Record, now time.Time) *RootWake {
	var last client.Record
	for _, record := range records {
		if rootLane(record.Lane) && record.At.After(last.At) {
			last = record
		}
	}
	quiet := now.Sub(last.At)
	if quiet < wakeQuiet {
		return nil
	}
	answered := answeredByRoot(records)
	owed := slices.DeleteFunc(slices.Clone(open), func(record client.Record) bool {
		return answered[record.Seq] || slices.Contains(notLanes, record.Lane) || !slices.ContainsFunc(record.To, rootLane)
	})
	if len(owed) == 0 {
		return nil
	}
	since := "in the last day"
	if !last.At.IsZero() {
		since = fmt.Sprintf("since %s at %s", last.Name(), last.At.In(pacific).Format("3:04 PM"))
	}
	var items []string
	for _, record := range owed[:min(len(owed), wakeShown)] {
		item := fmt.Sprintf("%s %s from %s", record.Kind, record.Name(), record.Lane)
		if due := dueClocks(record.Text, now); len(due) > 0 {
			item += " (clock " + strings.Join(due, ", ") + " due)"
		}
		items = append(items, item+": "+clip(record.Text))
	}
	if more := len(owed) - wakeShown; more > 0 {
		items = append(items, fmt.Sprintf("%d more in `cci digest`", more))
	}
	return &RootWake{
		Subject: fmt.Sprintf("root-wake:%d:%d", last.Seq, (quiet-wakeQuiet)/wakeAgain),
		Line: fmt.Sprintf("%s: relay to %s: WAKE from the stall watch at %s: no root record %s, and %d records to you are open. %s. Answer each now, and keep the turn open while any clock is due.",
			wakeDesk, root, now.In(pacific).Format("3:04 PM"), since, len(owed), strings.Join(items, "; ")),
	}
}

func dueClocks(text string, now time.Time) []string {
	local := now.In(pacific)
	var due []string
	for _, found := range clockNamed.FindAllStringSubmatch(text, -1) {
		hour, minute, meridiem := found[1], found[2], found[3]
		if hour == "" {
			hour, minute, meridiem = found[4], found[5], found[6]
		}
		h, _ := strconv.Atoi(hour)
		m, _ := strconv.Atoi(minute)
		at := clockOn(local, h%12, m, strings.ToUpper(meridiem))
		if !at.After(local) {
			due = append(due, at.Format("3:04 PM"))
		}
	}
	return slices.Compact(due)
}

func clockOn(local time.Time, hour, minute int, meridiem string) time.Time {
	day := func(h int) time.Time {
		return time.Date(local.Year(), local.Month(), local.Day(), h, minute, 0, 0, local.Location())
	}
	switch meridiem {
	case "A":
		return day(hour)
	case "P":
		return day(hour + 12)
	}
	am, pm := day(hour), day(hour+12)
	if pm.Sub(local).Abs() < am.Sub(local).Abs() {
		return pm
	}
	return am
}
