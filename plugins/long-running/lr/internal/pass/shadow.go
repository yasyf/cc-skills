package pass

import (
	"fmt"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	runnerPass  = 5 * time.Minute
	shadowMover = "shadow"
	shortHead   = 12
)

var allMovers = []machine.Mover{
	machine.MoverSupervisor, machine.MoverPool, machine.MoverLane, machine.MoverOwner, machine.MoverRoot,
}

type Diff struct {
	Subject string
	Cause   string
}

func Shadow(s *store.Store, snapshot []machine.Facts, now time.Time, post Post) ([]Diff, error) {
	out, err := Run(s, snapshot, now, func([]string, bus.Body) error { return nil }, allMovers, func(desk string) string { return desk })
	if err != nil {
		return nil, err
	}
	diffs, err := Diffs(s, snapshot, out.Verdicts, now)
	if err != nil {
		return nil, err
	}
	for _, diff := range diffs {
		note := bus.Note{Text: fmt.Sprintf("shadow %s: %s", diff.Subject, diff.Cause)}
		if err := post([]string{string(machine.MoverSupervisor)}, note); err != nil {
			return diffs, err
		}
	}
	return diffs, nil
}

func Diffs(s *store.Store, snapshot []machine.Facts, verdicts map[int]machine.Verdict, now time.Time) ([]Diff, error) {
	open, err := s.OpenItems("", 0)
	if err != nil {
		return nil, err
	}
	waiting := map[string]store.Item{}
	for _, item := range open {
		if item.Kind == string(machine.ActionEnqueue) {
			waiting[itemKey(item.Number, item.Head)] = item
		}
	}
	var diffs []Diff
	for _, f := range snapshot {
		cause := disagreement(f, verdicts[f.Number], waiting, now)
		if cause == "" {
			continue
		}
		subject := fmt.Sprintf("#%d at %s", f.Number, f.Head[:shortHead])
		raised, err := s.Escalate(subject, cause, shadowMover, now)
		if err != nil {
			return diffs, err
		}
		if raised {
			diffs = append(diffs, Diff{subject, cause})
		}
	}
	return diffs, nil
}

func disagreement(f machine.Facts, verdict machine.Verdict, waiting map[string]store.Item, now time.Time) string {
	if f.Queue == machine.QueueQueued && !f.Landable() {
		return "the runner queued it; v2 holds it: " + strings.Join(gates.Missing(f), "; ")
	}
	if verdict.State != machine.StateReady && verdict.State != machine.StateEvicted {
		return ""
	}
	item, found := waiting[itemKey(f.Number, f.Head)]
	if !found || now.Sub(item.Created) < runnerPass {
		return ""
	}
	return fmt.Sprintf("v2 would have queued it at %s; the runner has not", item.Created.UTC().Format("15:04Z"))
}

func itemKey(number int, head string) string {
	return fmt.Sprintf("%d/%s", number, head)
}
