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
	describeWindow     = 48 * time.Hour
	describeCells      = 200
	retiredByItsStop   = "stand-down"
	kindStopped        = "stopped"
)

var (
	milestoneLine = regexp.MustCompile(`^(?:\d{1,2}:\d{2} [AP]M )?(MECHANISM|FIX-LIVE|NOT-OURS)\b`)
	scopeLine     = regexp.MustCompile(`\bSCOPE\b`)
	recordLead    = regexp.MustCompile(`^(?:\d{1,2}:\d{2}(?: [AP]M)?:? )?(?:GO fix lane \S+ for class \S+|(?i:INCIDENT) (?:OPEN |REOPENED )?\S+|MECHANISM(?: \S+)?)?\s*:?\s*`)
	sentenceEnd   = regexp.MustCompile(`\. | \| `)
	workNotCause  = regexp.MustCompile(`\blane\b`)
	noDescription = regexp.MustCompile(`^orca-desk:|\bSCOPE\b|\blaunch(?:ed)?\b`)
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
		raw := incidentName(record)
		name := incidentRole.ReplaceAllString(raw, "")
		found := byName[name]
		if found == nil && record.Kind == kindIncident && (incidentSlug.MatchString(record.Text) || incidentRole.MatchString(raw)) {
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
		if parked := v.parked(lanes); out[i].work.who == nobody && len(parked) > 0 {
			out[i].work.who = strings.Join(parked, "; ")
		}
	}
	return out
}

func (v view) parked(lanes []string) []string {
	var parked []string
	for _, name := range unique(lanes) {
		lane := v.byLane[name]
		if slices.Contains(posting, lane.State) && len(lane.PRs) > 0 && !v.live(name) {
			parked = append(parked, fmt.Sprintf("%s, quiet since %s, holding #%d", name, pacificText(lane.Heard), lane.PRs[0]))
		}
		if last := v.latest[name]; lane.State == retired && lane.RetiredBy == retiredByItsStop && last.Kind == kindStopped {
			parked = append(parked, fmt.Sprintf("%s, stopped by #%d with its worker unfinished", name, last.Seq))
		}
	}
	return parked
}

func (i Incident) unowned() bool {
	return i.Open && i.work.who == nobody
}

func (i Incident) state() string {
	closed := "closed"
	if i.Closed.Seq != 0 {
		closed = fmt.Sprintf("closed by #%d", i.Closed.Seq)
	}
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
		return fmt.Sprintf("fixed: fix live by #%d, %s", i.FixLive.Seq, closed)
	case i.NotOurs.Seq != 0:
		return fmt.Sprintf("closed as not ours by #%d, %s", i.NotOurs.Seq, closed)
	}
	return closed + " with no fix-live record"
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

func descriptionRank(record Record) int {
	const incidentLine, report, goLine, mechanism, unranked = 0, 1, 2, 3, 4
	switch found := milestone(record); {
	case found == milestoneMechanism:
		return mechanism
	case found != "" || closesIncident(record) || !slices.Contains(coordinators, record.Lane):
		return unranked
	}
	switch record.Kind {
	case kindIncident:
		return incidentLine
	case "report":
		return report
	case "go":
		return goLine
	}
	return unranked
}

func firstSentence(text string) string {
	sentence := strings.Join(strings.Fields(recordLead.ReplaceAllString(text, "")), " ")
	if end := sentenceEnd.FindStringIndex(sentence); end != nil {
		sentence = sentence[:end[0]]
	}
	return strings.TrimRight(clipTo(sentence, describeCells), ".")
}

func describeIncident(records []Record, name string) string {
	described, best := "", descriptionRank(Record{})
	for _, record := range bySeq(records) {
		rank := descriptionRank(record)
		if rank >= best || noDescription.MatchString(record.Text) {
			continue
		}
		if incidentRole.ReplaceAllString(record.Topic, "") != name && incidentRole.ReplaceAllString(incidentName(record), "") != name {
			continue
		}
		if sentence := firstSentence(record.Text); sentence != "" && !workNotCause.MatchString(sentence) {
			described, best = sentence, rank
		}
	}
	return described
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
	describe := &cobra.Command{
		Use:   "describe SLUG",
		Short: "One sentence on what an incident is, from its own records of the last two days: the coordinator's, else its fix lane's mechanism; exit 1 when none says",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			opened, err := open(a, driveName)
			if err != nil {
				return err
			}
			defer opened.store.Close()
			records, err := opened.src.Records(c.Context(), a.Now().Add(-describeWindow))
			if err != nil {
				return err
			}
			name := incidentRole.ReplaceAllString(args[0], "")
			described := describeIncident(records, name)
			if described == "" {
				return fmt.Errorf("no record describes %s", name)
			}
			fmt.Fprintln(c.OutOrStdout(), described)
			return nil
		},
	}
	commands := []*cobra.Command{status, list, describe}
	for _, cmd := range commands {
		cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	}
	return commands
}
