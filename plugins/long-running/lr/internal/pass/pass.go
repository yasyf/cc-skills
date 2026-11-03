package pass

import (
	"fmt"
	"slices"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Post func(to []string, body bus.Body) error

type Act struct {
	Facts   machine.Facts
	Verdict machine.Verdict
}

type Escalated struct {
	Subject string
	Cause   string
	To      string
}

type Route func(desk string) string

type Landing struct {
	Facts    machine.Facts
	LastOpen time.Time
}

type Outcome struct {
	Verdicts  map[int]machine.Verdict
	Opened    []store.Item
	Landed    []Landing
	Carried   []machine.Facts
	Acts      []Act
	Escalated []Escalated
	Released  map[string][]store.Item
	Unposted  []error
}

const (
	EnqueuedFact = "enqueued"
	BoardDesk    = "board-refresher"
	GraderDesk   = "grader-appeals"
	root         = string(machine.MoverRoot)
)

var (
	deskOf = map[machine.Action]string{
		machine.ActionTap:     BoardDesk,
		machine.ActionGrade:   GraderDesk,
		machine.ActionVerdict: GraderDesk,
	}
	rootOwns = []machine.Action{machine.ActionEnqueue, machine.ActionDequeue, machine.ActionWatchQueue, machine.ActionWaitCI, machine.ActionLift}
	lanesOwn = []machine.Action{machine.ActionFixRed, machine.ActionRestack, machine.ActionReview}
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

func Run(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, live []machine.Mover, route Route) (Outcome, error) {
	out := Outcome{Verdicts: map[int]machine.Verdict{}, Released: map[string][]store.Item{}}
	now = now.UTC().Truncate(time.Second)
	tell := func(to []string, body bus.Body) error {
		if err := post(to, body); err != nil {
			out.Unposted = append(out.Unposted, err)
		}
		return nil
	}
	byBranch := make(map[string]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		byBranch[f.Branch] = f
	}
	known, err := knownRows(s, snapshot)
	if err != nil {
		return out, err
	}
	for _, f := range snapshot {
		if err := classifyOne(s, f, byBranch, now, tell, live, &out); err != nil {
			return out, fmt.Errorf("#%d: %w", f.Number, err)
		}
		if row, seen := known[f.Number]; seen && row.State != machine.StateLanded && out.Verdicts[f.Number].State == machine.StateLanded {
			out.Landed = append(out.Landed, Landing{f, row.ReadAt})
		}
		if f.TapFrom != "" && !out.Verdicts[f.Number].State.Terminal() {
			out.Carried = append(out.Carried, f)
		}
	}
	if err := escalateOverdue(s, snapshot, now, tell, live, route, &out); err != nil {
		return out, err
	}
	if !slices.Contains(live, machine.MoverPool) {
		return out, nil
	}
	err = releaseStale(s, now, tell, route, &out)
	return out, err
}

func knownRows(s *store.Store, snapshot []machine.Facts) (map[int]store.PR, error) {
	if len(snapshot) == 0 {
		return nil, nil
	}
	rows, err := s.PRs(snapshot[0].Repo)
	if err != nil {
		return nil, err
	}
	known := make(map[int]store.PR, len(rows))
	for _, row := range rows {
		known[row.Number] = row
	}
	return known, nil
}

func classifyOne(s *store.Store, f machine.Facts, byBranch map[string]machine.Facts, now time.Time, post Post, live []machine.Mover, out *Outcome) error {
	verdict := machine.Classify(f)
	if verdict.Action == machine.ActionWatchQueue && heldBelow(f, byBranch) {
		verdict = machine.Verdict{State: machine.StateAwaitingTap, Mover: machine.MoverSupervisor, Action: machine.ActionDequeue, Deadline: machine.SupervisorClock}
	}
	out.Verdicts[f.Number] = verdict
	if err := s.PutPR(store.PR{Repo: f.Repo, Number: f.Number, Head: f.Head, Base: f.Base, Lane: f.Lane, State: verdict.State, ReadAt: now}); err != nil {
		return err
	}
	if !verdict.OpensItem() {
		_, err := s.CloseItems(f.Repo, f.Number, now, "")
		return err
	}
	if verdict.Action == machine.ActionEnqueue && waitsBelow(f, byBranch) {
		return closeOthers(s, f, "", now)
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
			body := bus.Item{Item: item.ID, Action: string(verdict.Action), PR: f.Number, Head: f.Head, Mover: string(verdict.Mover), Deadline: store.Stamp(item.Deadline)}
			if err := post([]string{to}, body); err != nil {
				return err
			}
		}
	}
	if verdict.Mover == machine.MoverSupervisor && slices.Contains(samePass, verdict.Action) {
		out.Acts = append(out.Acts, Act{f, verdict})
	}
	return nil
}

func waitsBelow(f machine.Facts, byBranch map[string]machine.Facts) bool {
	for range len(byBranch) + 1 {
		below, open := byBranch[f.Base]
		if !open {
			return false
		}
		if !below.ClearsAbove() {
			return true
		}
		f = below
	}
	return false
}

func heldBelow(f machine.Facts, byBranch map[string]machine.Facts) bool {
	for range len(byBranch) + 1 {
		below, open := byBranch[f.Base]
		if !open {
			return false
		}
		if below.Hold != "" {
			return true
		}
		f = below
	}
	return false
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

func escalateOverdue(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, live []machine.Mover, route Route, out *Outcome) error {
	overdue, err := s.Overdue(now)
	if err != nil {
		return err
	}
	for _, item := range overdue {
		action := machine.Action(item.Kind)
		desk, deskOwns := deskOf[action]
		rootOwned := slices.Contains(rootOwns, action)
		if !deskOwns && !rootOwned && !slices.Contains(lanesOwn, action) {
			continue
		}
		if !slices.Contains(live, gateOf(item.Mover)) {
			continue
		}
		said, err := queueSaid(s, item)
		if err != nil {
			return err
		}
		to := laneOf(snapshot, item)
		if deskOwns {
			to = route(desk)
		}
		if rootOwned {
			to = root
		}
		if err := escalate(s, fmt.Sprintf("%s#%d", item.Repo, item.Number), item.Kind+" overdue", said, to, now, post, out); err != nil {
			return err
		}
	}
	return nil
}

func gateOf(mover machine.Mover) machine.Mover {
	if mover == machine.MoverRoot || mover == machine.MoverOwner {
		return machine.MoverSupervisor
	}
	return mover
}

func laneOf(snapshot []machine.Facts, item store.Item) string {
	at := slices.IndexFunc(snapshot, func(f machine.Facts) bool { return f.Repo == item.Repo && f.Number == item.Number })
	if at < 0 || snapshot[at].Lane == "" {
		return root
	}
	return snapshot[at].Lane
}

func queueSaid(s *store.Store, item store.Item) (string, error) {
	if item.Kind != string(machine.ActionEnqueue) {
		return "", nil
	}
	facts, err := s.Facts(item.Repo, item.Number, item.Head)
	if err != nil {
		return "", err
	}
	if said := facts[EnqueuedFact]; said != "" {
		return "; the queue said " + said, nil
	}
	return "", nil
}

func releaseStale(s *store.Store, now time.Time, post Post, route Route, out *Outcome) error {
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
		if err := escalate(s, "worker:"+name, "silent 15 minutes", "", route(GraderDesk), now, post, out); err != nil {
			return err
		}
	}
	return nil
}

func escalate(s *store.Store, subject, cause, detail, to string, now time.Time, post Post, out *Outcome) error {
	raised, err := s.Escalate(subject, cause, to, now)
	if err != nil || !raised {
		return err
	}
	out.Escalated = append(out.Escalated, Escalated{subject, cause + detail, to})
	return post([]string{to}, bus.Overdue(to, subject, cause+detail))
}

func audience(verdict machine.Verdict, f machine.Facts) string {
	if verdict.Mover == machine.MoverLane {
		return f.Lane
	}
	return string(verdict.Mover)
}
