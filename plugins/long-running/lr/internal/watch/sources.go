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
	AskNamespace    = "ask"
	GateNamespace   = "gate"
	RepeatNamespace = "repeat"
	PortCensusTopic = "port-census"

	askKind        = "ask"
	gateKind       = "gate"
	repeatKind     = "repeat"
	portCensusKind = "port-census"
	portEndedAttr  = "ended"

	askWithin    = 15 * time.Minute
	waitWithin   = 10 * time.Minute
	repeatWindow = 2 * time.Hour
	repeatCount  = 3
	askWindow    = 24 * time.Hour
	quoteChars   = 160

	portCensusQuiet = 6 * time.Hour
)

var (
	Window = askWindow

	Kinds = []string{"decide", "decision", "go", "state", "report", "landed", "opened", "head", "answer", "note"}

	Namespaces = []string{AskNamespace, GateNamespace, RepeatNamespace, UnfinishedNamespace, PlanNamespace, StepNamespace}

	pacific = func() *time.Location {
		zone, err := time.LoadLocation("America/Los_Angeles")
		if err != nil {
			panic(err)
		}
		return zone
	}()

	waitsOnPR = regexp.MustCompile(`(?i)\b(?:waits?\s+(?:only\s+)?on|blocked\s+on)\s+#(\d+)|\bwhen\s+#(\d+)\s+lands\b`)
	landedPR  = regexp.MustCompile(`^LANDED\s+#(\d+)\b`)
	notLanes  = []string{root, "main", "team-lead", "stall-watch", "desk-runner"}
	deskLane  = regexp.MustCompile(`^(?:.+-)?desk(?:-\d{1,2})?$`)
)

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

func LandedPRs(records []client.Record) map[string]client.Record {
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
	landed := LandedPRs(records)
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
	subjects := map[string][]string{}
	third := map[string]time.Time{}
	var classes []string
	for _, escalation := range escalations {
		class := classOf(escalation.Cause)
		if class == repeatKind || class == unfinishedKind || class == metaKind || class == unplannedClass || class == missedClass || now.Sub(escalation.Raised) >= repeatWindow || strings.HasPrefix(escalation.Subject, RepeatNamespace) {
			continue
		}
		if subjects[class] == nil {
			classes = append(classes, class)
		}
		if slices.Contains(subjects[class], escalation.Subject) {
			continue
		}
		subjects[class] = append(subjects[class], escalation.Subject)
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
		reached := fmt.Sprintf("repeat: %s reached %d or more subjects within %d hours on %s", class, repeatCount, int(repeatWindow.Hours()), since.In(pacific).Format("Jan 2"))
		const order = "a known defect is fixed the hour it is known, so order one fix lane for the class"
		waits = append(waits, Wait{
			Kind: repeatKind, Repo: RepeatNamespace, Head: class, Mover: machine.MoverRoot, Since: since, To: root,
			Key:   reached + "; " + order,
			Cause: reached + ": " + strings.Join(subjects[class], ", ") + "; " + order,
		})
	}
	return waits
}

func PortCensusWaits(records []client.Record, now time.Time) []Wait {
	var newest *client.Record
	for i, record := range records {
		if record.Kind == "state" && record.Topic == PortCensusTopic {
			newest = &records[i]
		}
	}
	if newest == nil || newest.Fields.Attrs[portEndedAttr] != "" || now.Before(newest.At.Add(portCensusQuiet)) {
		return nil
	}
	return []Wait{{
		Kind: portCensusKind, Repo: UnfinishedNamespace, Head: PortCensusTopic, Mover: machine.MoverRoot, Since: newest.At.Add(portCensusQuiet), To: root,
		Cause: fmt.Sprintf("old-tree port census: %s by %s is the newest walk, so old-tree PRs may be landing with no port; name a live old-tree-port-desk or post its census with %s=<why> if ports have ended", newest.Name(), newest.Lane, portEndedAttr),
	}}
}
