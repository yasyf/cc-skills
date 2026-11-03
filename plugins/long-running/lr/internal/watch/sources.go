package watch

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	ReleaseNamespace = "release"
	AskNamespace     = "ask"
	GateNamespace    = "gate"
	RepeatNamespace  = "repeat"

	approveKind = "approve"
	askKind     = "ask"
	gateKind    = "gate"

	gateFactHead = "release"
	repeatKind   = "repeat"

	gateWithin   = 5 * time.Minute
	askWithin    = 15 * time.Minute
	waitWithin   = 10 * time.Minute
	repeatWindow = 2 * time.Hour
	repeatCount  = 3
	askWindow    = 24 * time.Hour
	quoteChars   = 160
)

var (
	Window = askWindow

	Kinds = []string{"release", "decide", "decision", "go", "state", "report", "landed", "opened", "head", "answer", "note"}

	Namespaces = []string{ReleaseNamespace, AskNamespace, GateNamespace, RepeatNamespace}

	pacific = func() *time.Location {
		zone, err := time.LoadLocation("America/Los_Angeles")
		if err != nil {
			panic(err)
		}
		return zone
	}()

	releaseID = regexp.MustCompile(`\b(\d{5})\b`)
	ownerWord = regexp.MustCompile(`(?i)\bowner\b`)
	waitsOnPR = regexp.MustCompile(`(?i)\b(?:waits?\s+(?:only\s+)?on|blocked\s+on)\s+#(\d+)|\bwhen\s+#(\d+)\s+lands\b`)
	landedPR  = regexp.MustCompile(`^LANDED\s+#(\d+)\b`)
	notLanes  = []string{root, "main", "team-lead", "stall-watch", "desk-runner"}
	deskLane  = regexp.MustCompile(`^(?:.+-)?desk(?:-\d{1,2})?$`)
)

type GateState string

const (
	GateWaiting  GateState = "waiting"
	GatePlanning GateState = "planning"
	GateSettled  GateState = "settled"
)

type Gate struct {
	State GateState
	Since time.Time
}

var (
	gateWaits = regexp.MustCompile(`waits on approval since (\d{1,2}:\d{2}:\d{2} [AP]M)`)
	gatePlans = regexp.MustCompile(`decides its approve gate when its plans finish`)
)

func ParseGate(firstLine string, now time.Time) (Gate, error) {
	if gatePlans.MatchString(firstLine) {
		return Gate{State: GatePlanning}, nil
	}
	found := gateWaits.FindStringSubmatch(firstLine)
	if found == nil {
		return Gate{State: GateSettled}, nil
	}
	clock, err := time.ParseInLocation("3:04:05 PM", found[1], pacific)
	if err != nil {
		return Gate{}, fmt.Errorf("release gate clock %q: %w", found[1], err)
	}
	local := now.In(pacific)
	since := time.Date(local.Year(), local.Month(), local.Day(), clock.Hour(), clock.Minute(), clock.Second(), 0, pacific)
	if since.After(now) {
		since = since.AddDate(0, 0, -1)
	}
	return Gate{GateWaiting, since}, nil
}

func clip(text string) string {
	text = strings.Join(strings.Fields(text), " ")
	if runes := []rune(text); len(runes) > quoteChars {
		return string(runes[:quoteChars-1]) + "…"
	}
	return text
}

func rootLane(lane string) bool {
	return lane == root || lane == "main"
}

type releaseRow struct {
	id      int
	lane    string
	seq     int64
	order   client.Record
	ordered bool
}

func namesRelease(record client.Record, id int, seq int64) bool {
	if record.Re == seq {
		return true
	}
	return slices.Contains(releaseID.FindAllString(record.Text, -1), strconv.Itoa(id))
}

func orderedReleases(records []client.Record) []releaseRow {
	var rows []releaseRow
	for _, record := range records {
		if record.Kind == "release" {
			if found := releaseID.FindString(record.Text); found != "" {
				id, err := strconv.Atoi(found)
				if err != nil {
					panic(err)
				}
				rows = append(rows, releaseRow{id: id, lane: record.Lane, seq: record.Seq})
			}
			continue
		}
		if !rootLane(record.Lane) || (record.Kind != "decide" && record.Kind != "decision" && record.Kind != "go") || !ownerWord.MatchString(record.Text) {
			continue
		}
		for i := range rows {
			if !rows[i].ordered && namesRelease(record, rows[i].id, rows[i].seq) {
				rows[i].order, rows[i].ordered = record, true
			}
		}
	}
	return slices.DeleteFunc(rows, func(row releaseRow) bool { return !row.ordered })
}

func gateWait(row releaseRow, gate Gate) Wait {
	since := gate.Since
	if row.order.At.After(since) {
		since = row.order.At
	}
	return Wait{
		Kind: approveKind, Repo: ReleaseNamespace, Number: row.id, Mover: machine.MoverLane, Since: since, Within: gateWithin, To: root,
		Cause: fmt.Sprintf("approve overdue: release %d has sat at its approval gate since %s, %d minutes after owner order #%d; %s has not approved it", row.id, gate.Since.In(pacific).Format("3:04 PM"), int(gateWithin.Minutes()), row.order.Seq, row.lane),
	}
}

func ReleaseGates(s *store.Store, records []client.Record, status func(id int) (Gate, error), now time.Time) ([]Wait, error) {
	var waits []Wait
	for _, row := range orderedReleases(records) {
		facts, err := s.Facts(ReleaseNamespace, row.id, gateFactHead)
		if err != nil {
			return nil, err
		}
		if facts[gateKind] == string(GateSettled) {
			continue
		}
		gate, err := status(row.id)
		if err != nil {
			return nil, err
		}
		if _, err := s.PutFact(ReleaseNamespace, row.id, gateFactHead, gateKind, string(gate.State), now); err != nil {
			return nil, err
		}
		if gate.State == GateWaiting {
			waits = append(waits, gateWait(row, gate))
		}
	}
	return waits, nil
}

func answeredByRoot(records []client.Record) map[int64]bool {
	answered := map[int64]bool{}
	for _, record := range records {
		if rootLane(record.Lane) && record.Re != 0 {
			answered[record.Re] = true
		}
	}
	return answered
}

func OpenAsks(asks, records []client.Record, now time.Time) []Wait {
	answered := answeredByRoot(records)
	var waits []Wait
	for _, ask := range asks {
		if slices.Contains(notLanes, ask.Lane) || answered[ask.Seq] || now.Sub(ask.At) >= askWindow {
			continue
		}
		if !slices.ContainsFunc(ask.To, rootLane) {
			continue
		}
		waits = append(waits, Wait{
			Kind: askKind, Repo: AskNamespace, Number: int(ask.Seq), Mover: machine.MoverRoot, Since: ask.At, Within: askWithin, To: root,
			Cause: fmt.Sprintf("ask overdue: #%d from %s is open and unanswered: %s", ask.Seq, ask.Lane, clip(ask.Text)),
		})
	}
	return waits
}

func landedPRs(records []client.Record) map[string]client.Record {
	landed := map[string]client.Record{}
	for _, record := range records {
		if record.Kind != "landed" {
			continue
		}
		if found := landedPR.FindStringSubmatch(record.Text); found != nil {
			if _, known := landed[found[1]]; !known {
				landed[found[1]] = record
			}
		}
		for _, pr := range record.Refs.PRs {
			if _, known := landed[strconv.Itoa(pr.Number)]; !known {
				landed[strconv.Itoa(pr.Number)] = record
			}
		}
	}
	return landed
}

func waitedPRs(text string) []string {
	matches := waitsOnPR.FindAllStringSubmatch(text, -1)
	prs := make([]string, 0, len(matches))
	for _, found := range matches {
		prs = append(prs, found[1]+found[2])
	}
	return prs
}

func LandedWaits(records []client.Record) []Wait {
	landed := landedPRs(records)
	type key struct{ lane, pr string }
	waiting := map[key]client.Record{}
	var order []key
	for _, record := range records {
		if (record.Kind != "state" && record.Kind != "report") || slices.Contains(notLanes, record.Lane) || deskLane.MatchString(record.Lane) {
			continue
		}
		for _, pr := range waitedPRs(record.Text) {
			k := key{record.Lane, pr}
			if _, seen := waiting[k]; !seen {
				order = append(order, k)
			}
			waiting[k] = record
		}
	}
	var waits []Wait
	for _, k := range order {
		said := waiting[k]
		gate, done := landed[k.pr]
		if !done {
			continue
		}
		since := said.At
		if gate.At.After(since) {
			since = gate.At
		}
		if movedOn(records, k.lane, k.pr, said.Seq, since) {
			continue
		}
		number, err := strconv.Atoi(k.pr)
		if err != nil {
			panic(err)
		}
		waits = append(waits, Wait{
			Kind: gateKind, Repo: GateNamespace, Number: number, Head: k.lane, Mover: machine.MoverLane, Since: since, Within: waitWithin, To: k.lane,
			Cause: fmt.Sprintf("gate overdue: %s said at #%d that work waits on #%s, and #%s landed at #%d; open that work and post OPENED or HEAD, or post why it still waits", k.lane, said.Seq, k.pr, k.pr, gate.Seq),
		})
	}
	return waits
}

func movedOn(records []client.Record, lane, pr string, seq int64, since time.Time) bool {
	return slices.ContainsFunc(records, func(record client.Record) bool {
		if record.Lane != lane || record.Seq <= seq {
			return false
		}
		switch record.Kind {
		case "opened", "head":
			return !record.At.Before(since)
		case "state", "report":
			return !slices.Contains(waitedPRs(record.Text), pr)
		}
		return false
	})
}

func classOf(cause string) string {
	class, _, _ := strings.Cut(cause, ";")
	class, _, _ = strings.Cut(class, ":")
	return class
}

func Repeats(escalations []store.Escalation, now time.Time) []Wait {
	subjects := map[string]map[string]bool{}
	third := map[string]time.Time{}
	var classes []string
	for _, escalation := range escalations {
		class := classOf(escalation.Cause)
		if class == repeatKind || now.Sub(escalation.Raised) >= repeatWindow || strings.HasPrefix(escalation.Subject, RepeatNamespace) {
			continue
		}
		if subjects[class] == nil {
			subjects[class] = map[string]bool{}
			classes = append(classes, class)
		}
		subjects[class][escalation.Subject] = true
		if len(subjects[class]) == repeatCount {
			third[class] = escalation.Raised
		}
	}
	var waits []Wait
	for _, class := range classes {
		since, repeated := third[class]
		if !repeated {
			continue
		}
		waits = append(waits, Wait{
			Kind: repeatKind, Repo: RepeatNamespace, Head: class, Mover: machine.MoverRoot, Since: since, To: root,
			Cause: fmt.Sprintf("repeat: %s reached %d or more subjects within %d hours on %s; a known defect is fixed the hour it is known, so order one fix lane for the class", class, repeatCount, int(repeatWindow.Hours()), since.In(pacific).Format("Jan 2")),
		})
	}
	return waits
}
