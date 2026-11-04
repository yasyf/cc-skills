package update

import (
	"context"
	"fmt"
	"os"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

const (
	milestoneMechanism = "mechanism"
	milestoneFixLive   = "fix-live"
	milestoneNotOurs   = "not-ours"
)

var (
	milestoneLine = regexp.MustCompile(`^(?:\d{1,2}:\d{2} [AP]M )?(MECHANISM|FIX-LIVE|NOT-OURS)\b`)
	scopeLine     = regexp.MustCompile(`\bSCOPE\b`)
)

type Incident struct {
	Name      string
	Fired     Record
	Newest    Record
	Scope     Record
	Mechanism Record
	FixLive   Record
	NotOurs   Record
	Closed    Record
	Open      bool
	work      work
}

func milestone(record Record) string {
	if slices.Contains([]string{milestoneMechanism, milestoneFixLive, milestoneNotOurs}, record.Kind) {
		return record.Kind
	}
	if found := milestoneLine.FindStringSubmatch(record.Text); found != nil {
		return strings.ToLower(found[1])
	}
	return ""
}

func incidentRecord(record Record) bool {
	return record.Kind == kindIncident || (record.Kind == "decide" && closesIncident(record)) || milestone(record) != ""
}

func (v view) incidents(records []Record, drive string) []Incident {
	still := openIncidents(records)
	byName := map[string]*Incident{}
	var names []string
	for _, record := range bySeq(records) {
		if record.Drive != drive {
			continue
		}
		name := incidentRole.ReplaceAllString(incidentName(record), "")
		found := byName[name]
		if found == nil && record.Kind == kindIncident {
			found = &Incident{Name: name, Fired: record}
			byName[name] = found
			names = append(names, name)
		}
		if found == nil {
			continue
		}
		if closesIncident(record) {
			found.Closed = record
		}
		if record.Kind == kindIncident {
			found.Newest = record
			found.Open = found.Open || still[record.Seq]
			if scopeLine.MatchString(record.Text) {
				found.Scope = record
			}
		}
		switch milestone(record) {
		case milestoneMechanism:
			found.Mechanism = record
		case milestoneFixLive:
			found.FixLive = record
		case milestoneNotOurs:
			found.NotOurs = record
		}
	}
	out := make([]Incident, len(names))
	for i, name := range names {
		var lanes []string
		for _, lane := range v.lanes {
			if lane.Name == name || strings.HasPrefix(lane.Name, name+"-") {
				lanes = append(lanes, lane.Name)
			}
		}
		out[i] = *byName[name]
		out[i].work = v.workOf(lanes)
	}
	return out
}

func (i Incident) unowned() bool {
	return i.Open && i.work.who == nobody
}

func (i Incident) state() string {
	switch {
	case i.Open && i.FixLive.Seq != 0:
		return fmt.Sprintf("open, fix live by #%d", i.FixLive.Seq)
	case i.Open && i.NotOurs.Seq != 0:
		return fmt.Sprintf("open, shown not ours by #%d", i.NotOurs.Seq)
	case i.Open && i.Mechanism.Seq != 0:
		return fmt.Sprintf("open since %s, cause found in #%d, no fix live yet", pacific(i.Fired.At), i.Mechanism.Seq)
	case i.Open:
		return fmt.Sprintf("open since %s, cause not found yet", pacific(i.Fired.At))
	case i.FixLive.Seq != 0:
		return fmt.Sprintf("fixed: fix live by #%d, closed by #%d", i.FixLive.Seq, i.Closed.Seq)
	case i.NotOurs.Seq != 0:
		return fmt.Sprintf("closed as not ours by #%d, closed by #%d", i.NotOurs.Seq, i.Closed.Seq)
	}
	return fmt.Sprintf("closed by #%d with no fix-live record", i.Closed.Seq)
}

func (i Incident) closes() string {
	closing := fmt.Sprintf("the coordinator posts INCIDENT %s CLOSED", i.Name)
	switch {
	case !i.Open:
		return "nothing; it is closed"
	case i.FixLive.Seq != 0:
		return fmt.Sprintf("%s citing #%d", closing, i.FixLive.Seq)
	case i.NotOurs.Seq != 0:
		return fmt.Sprintf("%s citing #%d", closing, i.NotOurs.Seq)
	case i.work.who == nobody:
		return fmt.Sprintf("the coordinator starts %s-fix; its FIX-LIVE or NOT-OURS record, then %s", i.Name, closing)
	}
	return fmt.Sprintf("a FIX-LIVE or NOT-OURS record from %s, then %s", i.work.who, closing)
}

func cited(label string, record Record) string {
	if record.Seq == 0 {
		return fmt.Sprintf("%s: none recorded", label)
	}
	return fmt.Sprintf("%s: #%d %s %s: %s", label, record.Seq, pacific(record.At), record.Lane, clip(record.Text))
}

func (i Incident) lines() []string {
	return []string{
		fmt.Sprintf("%s: %s", i.Name, i.state()),
		cited("fired", i.Fired),
		"lane on it: " + i.work.who,
		cited("scope", i.Scope),
		cited("mechanism", i.Mechanism),
		cited("fix live", i.FixLive),
		cited("not ours", i.NotOurs),
		"closes it: " + i.closes(),
	}
}

func incidentTable(incidents []Incident) []string {
	if len(incidents) == 0 {
		return []string{"No incident records since midnight Pacific."}
	}
	ordered := slices.Clone(incidents)
	slices.SortStableFunc(ordered, func(one, other Incident) int {
		rank := func(i Incident) int {
			switch {
			case i.unowned():
				return 0
			case i.Open:
				return 1
			}
			return 2
		}
		return rank(one) - rank(other)
	})
	lines := []string{"| Incident | State | Lane on it | What closes it |", "|---|---|---|---|"}
	for _, i := range ordered {
		lines = append(lines, fmt.Sprintf("| %s | %s | %s | %s |", i.Name, cell(i.state()), i.work.who, cell(i.closes())))
	}
	return lines
}

func readIncidents(ctx context.Context, src Sources, bus string, now time.Time) ([]Incident, error) {
	lanes, err := src.Lanes(ctx)
	if err != nil {
		return nil, err
	}
	records, err := src.Records(ctx, midnight(now))
	if err != nil {
		return nil, err
	}
	incidents := slices.DeleteFunc(slices.Clone(records), func(record Record) bool { return !incidentRecord(record) })
	return newView(lanes, records, waitingOnRoot(records), now).incidents(incidents, bus), nil
}

func IncidentCommands(a *app.App) []*cobra.Command {
	var driveName string
	read := func(c *cobra.Command) ([]Incident, error) {
		opened, err := open(a, driveName)
		if err != nil {
			return nil, err
		}
		defer opened.store.Close()
		return readIncidents(c.Context(), opened.src, opened.entry.Bus(), a.Now())
	}
	status := &cobra.Command{
		Use:   "status SLUG",
		Short: "What fired, the lane on it, its newest scope, mechanism and fix-live records, and what closes it, read from the bus",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			incidents, err := read(c)
			if err != nil {
				return err
			}
			name := incidentRole.ReplaceAllString(args[0], "")
			at := slices.IndexFunc(incidents, func(i Incident) bool { return i.Name == name })
			if at < 0 {
				return fmt.Errorf("no incident record names %s since midnight Pacific; lr incident list shows the ones that do", name)
			}
			fmt.Fprintln(c.OutOrStdout(), strings.Join(incidents[at].lines(), "\n"))
			return nil
		},
	}
	list := &cobra.Command{
		Use:   "list",
		Short: "One row per incident since midnight Pacific with its state, lane and what closes it, unowned first",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			incidents, err := read(c)
			if err != nil {
				return err
			}
			fmt.Fprintln(c.OutOrStdout(), strings.Join(incidentTable(incidents), "\n"))
			return nil
		},
	}
	for _, cmd := range []*cobra.Command{status, list} {
		cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	}
	return []*cobra.Command{status, list}
}
