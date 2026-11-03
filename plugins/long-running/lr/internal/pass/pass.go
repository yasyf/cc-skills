package pass

import (
	"fmt"
	"slices"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Post func(kind string, to []string, body map[string]any) error

type Act struct {
	Facts   machine.Facts
	Verdict machine.Verdict
}

type Escalated struct {
	Subject string
	Cause   string
	To      machine.Mover
}

type Outcome struct {
	Verdicts  map[int]machine.Verdict
	Opened    []store.Item
	Acts      []Act
	Escalated []Escalated
	Released  map[string][]store.Item
}

const EnqueuedFact = "enqueued"

var (
	escalatesTo = map[machine.Mover]machine.Mover{
		machine.MoverPool:       machine.MoverSupervisor,
		machine.MoverLane:       machine.MoverRoot,
		machine.MoverSupervisor: machine.MoverRoot,
		machine.MoverRoot:       machine.MoverOwner,
		machine.MoverOwner:      machine.MoverOwner,
	}
	samePass = []machine.Action{
		machine.ActionEnqueue, machine.ActionDequeue, machine.ActionRestack,
		machine.ActionWaitCI, machine.ActionWatchQueue,
	}
	liveMovers = map[string]machine.Mover{
		"landing": machine.MoverSupervisor,
		"grading": machine.MoverPool,
		"items":   machine.MoverLane,
	}
)

func Live(movers []string) []machine.Mover {
	live := make([]machine.Mover, 0, len(movers))
	for _, name := range movers {
		if mover, found := liveMovers[name]; found {
			live = append(live, mover)
		}
	}
	return live
}

func Run(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, live []machine.Mover) (Outcome, error) {
	out := Outcome{Verdicts: map[int]machine.Verdict{}, Released: map[string][]store.Item{}}
	now = now.UTC().Truncate(time.Second)
	for _, f := range snapshot {
		if err := classifyOne(s, f, now, post, live, &out); err != nil {
			return out, fmt.Errorf("#%d: %w", f.Number, err)
		}
	}
	if err := escalateOverdue(s, now, post, live, &out); err != nil {
		return out, err
	}
	if !slices.Contains(live, machine.MoverPool) {
		return out, nil
	}
	return out, releaseStale(s, now, post, &out)
}

func classifyOne(s *store.Store, f machine.Facts, now time.Time, post Post, live []machine.Mover, out *Outcome) error {
	verdict := machine.Classify(f)
	out.Verdicts[f.Number] = verdict
	if err := s.PutPR(store.PR{Repo: f.Repo, Number: f.Number, Head: f.Head, Base: f.Base, Lane: f.Lane, State: verdict.State, ReadAt: now}); err != nil {
		return err
	}
	if verdict.State.Terminal() {
		_, err := s.CloseItems(f.Repo, f.Number, now, "")
		return err
	}
	if err := closeOthers(s, f, verdict.Action, now); err != nil {
		return err
	}
	item, err := s.OpenItem(store.NewItem{Kind: string(verdict.Action), Repo: f.Repo, Number: f.Number, Head: f.Head, Mover: verdict.Mover, Deadline: verdict.Deadline}, now)
	if err != nil {
		return err
	}
	if item.Created.Equal(now) {
		out.Opened = append(out.Opened, item)
		if to := audience(verdict, f); verdict.Mover != machine.MoverSupervisor && slices.Contains(live, verdict.Mover) && to != "" {
			body := map[string]any{"item": item.ID, "action": string(verdict.Action), "pr": f.Number, "head": f.Head, "mover": string(verdict.Mover), "deadline": store.Stamp(item.Deadline)}
			if err := post("item", []string{to}, body); err != nil {
				return err
			}
		}
	}
	if verdict.Mover == machine.MoverSupervisor && slices.Contains(samePass, verdict.Action) {
		out.Acts = append(out.Acts, Act{f, verdict})
	}
	return nil
}

func closeOthers(s *store.Store, f machine.Facts, keep machine.Action, now time.Time) error {
	if _, err := s.CloseItems(f.Repo, f.Number, now, string(keep)); err != nil {
		return err
	}
	open, err := s.OpenItems(f.Repo, f.Number)
	if err != nil {
		return err
	}
	for _, item := range open {
		if item.Head == f.Head {
			continue
		}
		if err := s.CloseItem(item.ID, now); err != nil {
			return err
		}
	}
	return nil
}

func escalateOverdue(s *store.Store, now time.Time, post Post, live []machine.Mover, out *Outcome) error {
	overdue, err := s.Overdue(now)
	if err != nil {
		return err
	}
	for _, item := range overdue {
		if !slices.Contains(live, item.Mover) {
			continue
		}
		cause, err := overdueCause(s, item)
		if err != nil {
			return err
		}
		if err := escalate(s, fmt.Sprintf("%s#%d", item.Repo, item.Number), cause, escalatesTo[item.Mover], now, post, out); err != nil {
			return err
		}
	}
	return nil
}

func overdueCause(s *store.Store, item store.Item) (string, error) {
	cause := item.Kind + " overdue"
	if item.Kind != string(machine.ActionEnqueue) {
		return cause, nil
	}
	facts, err := s.Facts(item.Repo, item.Number, item.Head)
	if err != nil {
		return "", err
	}
	if said := facts[EnqueuedFact]; said != "" {
		return cause + "; the queue said " + said, nil
	}
	return cause, nil
}

func releaseStale(s *store.Store, now time.Time, post Post, out *Outcome) error {
	names, err := s.StaleWorkers(now)
	if err != nil {
		return err
	}
	for _, name := range names {
		held, err := s.ReleaseWorker(name)
		if err != nil {
			return err
		}
		out.Released[name] = held
		if err := escalate(s, "worker:"+name, "silent 15 minutes", machine.MoverRoot, now, post, out); err != nil {
			return err
		}
	}
	return nil
}

func escalate(s *store.Store, subject, cause string, to machine.Mover, now time.Time, post Post, out *Outcome) error {
	raised, err := s.Escalate(subject, cause, string(to), now)
	if err != nil || !raised {
		return err
	}
	out.Escalated = append(out.Escalated, Escalated{subject, cause, to})
	return post("escalation", []string{string(to)}, map[string]any{"subject": subject, "cause": cause})
}

func audience(verdict machine.Verdict, f machine.Facts) string {
	if verdict.Mover == machine.MoverLane {
		return f.Lane
	}
	return string(verdict.Mover)
}
