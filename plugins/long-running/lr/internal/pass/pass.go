package pass

import (
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading"
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

type Owner func(lane string) (string, error)

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
	RowsDesk     = "meta-desk"
	GraderDesk   = "grader-appeals"
	root         = string(machine.MoverRoot)

	clusterSize    = 3
	refusalCluster = 30 * time.Minute
	waitCluster    = time.Hour

	rowsUnread = "REVIEW-ROW-UNREAD"

	ungradedFact = "green-ungraded"
	ungradedLane = time.Hour
	ungradedRoot = 3 * time.Hour
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
	automated  = []machine.Mover{machine.MoverSupervisor, machine.MoverPool}
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

func Run(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, live []machine.Mover, route Route, owner Owner) (Outcome, error) {
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
		if f.CarriedBy != "" && !out.Verdicts[f.Number].State.Terminal() {
			out.Carried = append(out.Carried, f)
		}
	}
	if err := escalateOverdue(s, snapshot, now, tell, live, route, owner, &out); err != nil {
		return out, err
	}
	if slices.Contains(live, machine.MoverSupervisor) {
		if err := escalateClusters(s, snapshot, now, tell, &out); err != nil {
			return out, err
		}
		if err := escalateStuckBases(s, snapshot, byBranch, now, tell, &out); err != nil {
			return out, err
		}
		if err := escalateTappedHolds(s, snapshot, now, tell, &out); err != nil {
			return out, err
		}
		if err := escalateUnreadRows(s, snapshot, now, tell, &out); err != nil {
			return out, err
		}
	}
	if !slices.Contains(live, machine.MoverPool) {
		return out, nil
	}
	if err := escalateUngraded(s, snapshot, now, tell, route, owner, &out); err != nil {
		return out, err
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
	if _, waits := blockerBelow(f, byBranch); verdict.Action == machine.ActionEnqueue && waits {
		return closeOthers(s, f, "", now)
	}
	if err := closeOthers(s, f, verdict.Action, now); err != nil {
		return err
	}
	item, err := s.OpenItem(store.NewItem{Kind: string(verdict.Action), Repo: f.Repo, Number: f.Number, Head: f.Head, Mover: verdict.Mover, Deadline: verdict.Deadline}, now)
	if err != nil {
		return err
	}
	if verdict.Action == machine.ActionTap {
		missing, err := json.Marshal(gates.Missing(f))
		if err != nil {
			return err
		}
		if _, err := s.PutFact(f.Repo, f.Number, f.Head, gates.MissingFact, string(missing), now); err != nil {
			return err
		}
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

func blockerBelow(f machine.Facts, byBranch map[string]machine.Facts) (machine.Facts, bool) {
	for range len(byBranch) + 1 {
		below, open := byBranch[f.Base]
		if !open {
			return machine.Facts{}, false
		}
		if !below.ClearsAbove() {
			return below, true
		}
		f = below
	}
	return machine.Facts{}, false
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

func escalateOverdue(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, live []machine.Mover, route Route, owner Owner, out *Outcome) error {
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
		cause, to := item.Kind+" overdue", root
		switch lane := laneOf(snapshot, item); {
		case deskOwns:
			to = route(desk)
		case rootOwned, lane == root:
		default:
			if to, err = owner(lane); err != nil {
				return err
			}
			if to == root {
				cause = "UNOWNED, " + lane + " is not running: " + cause
			}
		}
		subject := fmt.Sprintf("%s#%d", item.Repo, item.Number)
		body := bus.Overdue(to, subject, cause+said)
		if !deskOwns && !rootOwned && to != root {
			body = bus.Ask{Text: subject + " " + cause + said}
		}
		if err := raise(s, subject, cause, said, to, now, body, post, out); err != nil {
			return err
		}
	}
	return nil
}

func escalateClusters(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, out *Outcome) error {
	if len(snapshot) == 0 {
		return nil
	}
	repo := snapshot[0].Repo
	open, err := s.OpenItems(repo, 0)
	if err != nil {
		return err
	}
	var causes []string
	clusters := map[string][]store.Item{}
	for _, item := range open {
		cause, within, err := clusterCause(s, item)
		if err != nil {
			return err
		}
		if cause == "" || now.Sub(item.Created) < within {
			continue
		}
		if clusters[cause] == nil {
			causes = append(causes, cause)
		}
		clusters[cause] = append(clusters[cause], item)
	}
	for _, cause := range causes {
		items := clusters[cause]
		if len(items) < clusterSize {
			continue
		}
		numbers := make([]string, len(items))
		for i, item := range items {
			numbers[i] = fmt.Sprintf("#%d", item.Number)
		}
		shared := fmt.Sprintf("on %d or more open PRs, first #%d", clusterSize, items[0].Number)
		if err := escalate(s, repo+": "+cause, shared, ": "+strings.Join(numbers, ", "), root, now, post, out); err != nil {
			return err
		}
	}
	return nil
}

func clusterCause(s *store.Store, item store.Item) (string, time.Duration, error) {
	if item.Kind == string(machine.ActionEnqueue) {
		facts, err := s.Facts(item.Repo, item.Number, item.Head)
		if err != nil {
			return "", 0, err
		}
		if said := facts[EnqueuedFact]; said != "" {
			own := regexp.MustCompile(`\b` + strconv.Itoa(item.Number) + `\b`)
			return fmt.Sprintf("the queue said %q for %d minutes", own.ReplaceAllString(said, "<pr>"), int(refusalCluster.Minutes())), refusalCluster, nil
		}
	}
	switch {
	case item.Kind == string(machine.ActionReview):
		return fmt.Sprintf("review waited on a reviewer for %d minutes", int(waitCluster.Minutes())), waitCluster, nil
	case item.Kind == string(machine.ActionRowWait):
		return fmt.Sprintf("a review row waited on its lane for %d minutes", int(waitCluster.Minutes())), waitCluster, nil
	case slices.Contains(automated, item.Mover):
		return fmt.Sprintf("%s waited on the %s for %d minutes", item.Kind, item.Mover, int(waitCluster.Minutes())), waitCluster, nil
	}
	return "", 0, nil
}

func escalateStuckBases(s *store.Store, snapshot []machine.Facts, byBranch map[string]machine.Facts, now time.Time, post Post, out *Outcome) error {
	var bases []machine.Facts
	held := map[int][]string{}
	for _, f := range snapshot {
		if out.Verdicts[f.Number].Action != machine.ActionEnqueue {
			continue
		}
		base, waits := blockerBelow(f, byBranch)
		if !waits || out.Verdicts[base.Number].State.Terminal() {
			continue
		}
		if held[base.Number] == nil {
			bases = append(bases, base)
		}
		held[base.Number] = append(held[base.Number], fmt.Sprintf("#%d", f.Number))
	}
	for _, base := range bases {
		stuck, err := stuckBecause(s, base, now)
		if err != nil {
			return err
		}
		if stuck == "" {
			continue
		}
		subject := fmt.Sprintf("%s#%d", base.Repo, base.Number)
		cause := "stack base stuck at " + base.Head[:min(10, len(base.Head))]
		if err := escalate(s, subject, cause, "; "+stuck+" and holds "+strings.Join(held[base.Number], ", ")+" above it", root, now, post, out); err != nil {
			return err
		}
	}
	return nil
}

func escalateTappedHolds(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, out *Outcome) error {
	for _, f := range snapshot {
		if out.Verdicts[f.Number].State != machine.StateHeld {
			continue
		}
		subject := fmt.Sprintf("%s#%d at %s", f.Repo, f.Number, f.Head[:min(10, len(f.Head))])
		cause := fmt.Sprintf("DECIDE: the owner tapped this head while hold #%d stands; lift the hold, or tell the owner why not", f.HoldSeq)
		if err := escalate(s, subject, cause, "", root, now, post, out); err != nil {
			return err
		}
	}
	return nil
}

func escalateUnreadRows(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, out *Outcome) error {
	var unread []string
	for _, f := range snapshot {
		if f.RowWait == gates.NoRow && f.Tap == machine.TapTapped && !f.Exempt && !out.Verdicts[f.Number].State.Terminal() {
			unread = append(unread, fmt.Sprintf("#%d", f.Number))
		}
	}
	if len(unread) == 0 {
		return nil
	}
	subject := rowsUnread + " " + strings.Join(unread, ", ")
	cause := "the owner tapped these and none has a review row, so none lands until its row is written"
	raised, err := s.Escalate(subject, cause, RowsDesk, now)
	if err != nil || !raised {
		return err
	}
	out.Escalated = append(out.Escalated, Escalated{subject, cause, RowsDesk})
	return post([]string{RowsDesk}, bus.Escalation{Subject: subject, Cause: cause})
}

func stuckBecause(s *store.Store, base machine.Facts, now time.Time) (string, error) {
	open, err := s.OpenItems(base.Repo, base.Number)
	if err != nil {
		return "", err
	}
	if len(open) == 0 {
		return "nobody drives it", nil
	}
	for _, item := range open {
		if !now.Before(item.Deadline) {
			return fmt.Sprintf("its %s waits on the %s past its deadline", item.Kind, item.Mover), nil
		}
	}
	return "", nil
}

func escalateUngraded(s *store.Store, snapshot []machine.Facts, now time.Time, post Post, route Route, owner Owner, out *Outcome) error {
	if len(snapshot) == 0 {
		return nil
	}
	since, err := s.FactsOfKind(snapshot[0].Repo, ungradedFact)
	if err != nil {
		return err
	}
	for _, f := range snapshot {
		if f.Lane == "" || !f.Green() || f.Graded() || f.Draft || f.Foreign() || f.Restackable() || out.Verdicts[f.Number].State.Terminal() {
			continue
		}
		first, seen := since[store.FactKey{Number: f.Number, Head: f.Head}]
		if !seen {
			if _, err := s.PutFact(f.Repo, f.Number, f.Head, ungradedFact, "green", now); err != nil {
				return err
			}
			continue
		}
		if err := tellUngraded(s, f, now.Sub(first), now, post, route, owner, out); err != nil {
			return err
		}
	}
	return nil
}

func tellUngraded(s *store.Store, f machine.Facts, waited time.Duration, now time.Time, post Post, route Route, owner Owner, out *Outcome) error {
	subject := fmt.Sprintf("%s#%d at %s", f.Repo, f.Number, f.Head[:min(10, len(f.Head))])
	detail := ": no " + strings.Join(grading.Missing(f), " or ") + " grade"
	switch {
	case waited >= ungradedRoot:
		return escalate(s, subject, fmt.Sprintf("green and ungraded for %d minutes", int(ungradedRoot.Minutes())), detail, root, now, post, out)
	case waited >= ungradedLane:
		lane, err := owner(f.Lane)
		if err != nil {
			return err
		}
		desk := route(GraderDesk)
		both := func(_ []string, body bus.Body) error { return post(slices.Compact([]string{desk, lane}), body) }
		return escalate(s, subject, fmt.Sprintf("green and ungraded for %d minutes", int(ungradedLane.Minutes())), detail, desk, now, both, out)
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
	return raise(s, subject, cause, detail, to, now, bus.Overdue(to, subject, cause+detail), post, out)
}

func raise(s *store.Store, subject, cause, detail, to string, now time.Time, body bus.Body, post Post, out *Outcome) error {
	raised, err := s.Escalate(subject, cause, to, now)
	if err != nil || !raised {
		return err
	}
	out.Escalated = append(out.Escalated, Escalated{subject, cause + detail, to})
	return post([]string{to}, body)
}

func audience(verdict machine.Verdict, f machine.Facts) string {
	if verdict.Mover == machine.MoverLane {
		return f.Lane
	}
	return string(verdict.Mover)
}
