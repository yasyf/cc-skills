package watch

import (
	"fmt"
	"regexp"
	"slices"
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
)

var (
	namedDeadline     = regexp.MustCompile(`(?i)\b(?:by|before|until|due(?:[ \t]+(?:by|at))?)[ \t]+(` + clockTime + `)\b`)
	leadingClock      = regexp.MustCompile(`(?i)^(?:` + clockTime + `)\b`)
	machineLanes      = []string{Lane, "stall-watch", "desk-runner", lanes.Self}
	ordersOwingNoPlan = []string{"hold", "go", "lift"}
)

func Unplanned(records []client.Record, now time.Time) []Wait {
	var waits []Wait
	for _, record := range records {
		deadline := deadlineOf(record)
		if deadline == "" || slices.Contains(record.To, "owner") || record.Kind == "escalation" || slices.Contains(ordersOwingNoPlan, string(record.Kind)) || record.Status == "closed" || now.Sub(record.At) > planWindow || slices.Contains(machineLanes, record.Lane) || planned(record.Fields.Attrs) || settled(records, record.Seq) {
			continue
		}
		waits = append(waits, Wait{
			Kind: planKind, Repo: PlanNamespace, Number: int(record.Seq), Mover: machine.MoverLane, Since: record.At, To: record.Lane,
			Cause: fmt.Sprintf("%s: %s from %s names a deadline by %s and carries no owner, steps or times: %s; %s owes a reply carrying --attr %s=<lane> and one --attr %s<n>=<clock> <what> per step", unplannedClass, record.Name(), record.Lane, deadline, clip(record.Text), record.Lane, ownerAttr, stepAttr),
		})
	}
	return waits
}

func deadlineOf(record client.Record) string {
	if due := record.Fields.Attrs[deadlineAttr]; due != "" {
		return due
	}
	for _, at := range namedDeadline.FindAllStringSubmatchIndex(record.Text, -1) {
		if !strings.HasSuffix(strings.ToLower(strings.TrimSpace(record.Text[:at[0]])), "since") && !strings.HasPrefix(record.Text[at[3]:], ":") {
			return record.Text[at[2]:at[3]]
		}
	}
	return ""
}

func planned(attrs map[string]string) bool {
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
		return later.Resolves == seq || slices.Contains(later.AlsoResolves, seq) || later.Re == seq && planned(later.Fields.Attrs)
	})
}
