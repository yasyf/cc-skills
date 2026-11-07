package lanes

import (
	"cmp"
	"fmt"
	"maps"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const gateReadFact = "gate-read"

type stoodDown struct {
	name      string
	successor string
	prs       []int
	seq       int64
	by        string
}

type unread struct {
	name   string
	why    string
	asker  string
	retry  Request
	newest int64
}

type refusal struct {
	lane string
	why  string
}

type handed struct {
	from, to string
	prs      []int
}

type unassigned struct {
	record Record
	lane   store.Lane
}

type folded struct {
	adopted    []string
	hand       []string
	unread     []unread
	owing      []owing
	stood      []stoodDown
	ended      []stoodDown
	kept       []stoodDown
	handed     []handed
	untyped    []Record
	unheard    []string
	refused    []refusal
	unassigned []unassigned
	deps       Deps
}

func (out *folded) handOn(s *store.Store, from string, prs []int, fresh bool) {
	own := ours(s, out.deps, prs)
	if len(own) == 0 {
		return
	}
	to := or(defaultOwner(s), Root)
	do(s.Own(to, own))
	if to != Root && !fresh {
		out.handed = append(out.handed, handed{from, to, own})
	}
}

func (out *folded) retireHolding(s *store.Store, name, by string, at time.Time) {
	do(s.Own(name, must(s.HeldBy(name, out.deps.Ledger))))
	retire(s, name, by, at)
}

func (out folded) prsTo(name string) string {
	for _, one := range out.handed {
		if one.from == name {
			return one.to
		}
	}
	return "the coordinator"
}

func takesOver(s *store.Store, d Deps, name string, old store.Lane, at time.Time) string {
	do(s.Own(name, ours(s, d, old.PRs)))
	if _, known := laneOf(s, old.RetiredBy); isLive(old.State) || !known {
		retire(s, old.Name, name, at)
		return old.Role
	}
	return ""
}

func grant(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	name, verb, cited := r.Attr("lane"), r.Attr("verb"), r.Attr("last-read")
	row, known := laneOf(s, name)
	if verb == "done" && known && r.Lane == runner && row.State != store.LaneRetired && out.rotated(s, row, r, fresh, at) {
		return
	}
	switch {
	case verb == "park" && known:
		do(s.SetLaneWaiting(name, r.Seq, time.Time{}, ""))
	case verb == "unpark" && known:
		if fresh {
			do(s.SetLaneWaiting(name, 0, at, ""))
			return
		}
		resume(s, name, fmt.Sprintf("unpark:%d", r.Seq), "the coordinator lifted your park", r, at)
	case verb == "done" && known && r.Lane == runner && (dutyOf(row) != "" || endsAtRotation(row)):
		if row.State != store.LaneRetired && out.owesNothing(s, name, fresh, at) {
			out.retireHolding(s, name, retiredWorkerDone, at)
		}
	case verb == "done" && known && row.State != store.LaneRetired:
		if out.owesNothing(s, name, fresh, at) {
			out.retireHolding(s, name, "done", at)
			do(s.SetLaneStanding(name, ""))
		}
	case verb == "done" && known && row.Standing != "":
		do(s.SetLaneStanding(name, ""))
	case verb == "revive" && known && row.State == store.LaneRetired:
		do(s.ReviveLane(name, at))
		do(s.SetLaneStanding(name, or(row.Standing, inheritedDuty(s, name, row.Brief, ""))))
	case verb == "step" && known:
		do(s.SetLaneStep(name, r.Attr("step")))
	case verb == "owner":
		numbers := r.PRs()
		if !fresh {
			var refused []unread
			for _, stripped := range strippedBy(s, name, numbers) {
				if one, found := unreadBy(s, r, stripped, cited, Request{Verb: verb, Lane: name, PRs: numbers}); found {
					refused = append(refused, one)
				}
			}
			if len(refused) > 0 {
				newest := slices.MaxFunc(refused, func(a, b unread) int { return cmp.Compare(a.newest, b.newest) }).newest
				for i := range refused {
					refused[i].newest = newest
				}
				out.unread = append(out.unread, refused...)
				for _, one := range refused {
					hold(s, one.name, one.why, at)
				}
				return
			}
		}
		do(s.Own(name, numbers))
	case verb == "release":
		var held []int
		for n, owner := range must(s.OwnerOf(r.PRs())) {
			if owner == name {
				held = append(held, n)
			}
		}
		do(s.Disown(held))
	case verb == "stand-down" && known:
		successor := heirOf(s, r.Attr("successor"), name)
		if !fresh && isLive(row.State) {
			if one, found := unreadBy(s, r, name, cited, Request{Verb: verb, Lane: name, Successor: r.Attr("successor"), Open: r.Attr("open")}); found {
				out.unread = append(out.unread, one)
				hold(s, name, one.why, at)
				return
			}
		}
		if successor == "" && row.State != store.LaneRetired && !out.orderedOff(s, name, fresh, at) {
			return
		}
		if successor == "" && !fresh {
			dropQueuedLaunch(s, row, at, out)
		}
		if row.State != store.LaneRetired || successor != "" || slices.Contains(unprovenEnds, row.RetiredBy) {
			retire(s, name, or(successor, Root), at)
			if !fresh {
				out.ended = append(out.ended, stoodDown{name, successor, row.PRs, r.Seq, r.Name()})
			}
		}
		if successor != "" {
			cost := successorCost(row)
			heir := store.Lane{Name: successor, Brief: row.Brief, Model: cost.Model, Effort: cost.Effort, Role: row.Role, State: store.LaneLaunching, Handoff: row.Handoff, Predecessor: name, Placement: row.Placement, Launcher: row.Launcher, Standing: row.Standing, Step: row.Step}
			wantHeir(s, heir, r.Name(), fresh, at)
			do(s.Own(successor, ours(s, out.deps, row.PRs)))
		} else {
			out.handOn(s, name, row.PRs, fresh)
		}
		if !fresh {
			out.stood = append(out.stood, stoodDown{name, successor, row.PRs, r.Seq, r.Name()})
		}
	}
}

func heirOf(s *store.Store, named string, standingDown ...string) string {
	if named == "" || numbered.MatchString(named) {
		return named
	}
	heir, newestNumber := named, 0
	for _, lane := range must(s.Lanes(live...)) {
		if stem, number := family(lane.Name); numbered.MatchString(lane.Name) && stem == named && number > newestNumber && !slices.Contains(standingDown, lane.Name) {
			heir, newestNumber = lane.Name, number
		}
	}
	return heir
}

func wantHeir(s *store.Store, heir store.Lane, by string, fresh bool, at time.Time) {
	if _, known := laneOf(s, heir.Name); known || fresh {
		must(s.WantLane(heir, at))
		return
	}
	heir.Reason = or(heir.Reason, fmt.Sprintf("was stood down for it by record %s", by))
	do(s.Probe(owedLaunch+heir.Name, at))
	must(s.WantLane(heir, at))
}

func (out *folded) rotated(s *store.Store, row store.Lane, r Record, fresh bool, at time.Time) bool {
	if row.Flushed.IsZero() || maxOrder(s, r) != 0 || !endsAtRotation(row) {
		return false
	}
	taken := map[string]bool{}
	for _, name := range must(s.Names()) {
		taken[name] = true
	}
	heir, cost := SuccessorName(row.Name, taken), successorCost(row)
	retire(s, row.Name, heir, at)
	wantHeir(s, store.Lane{Name: heir, Brief: row.Brief, Model: cost.Model, Effort: cost.Effort, Role: row.Role, State: store.LaneLaunching, Handoff: row.Handoff, Predecessor: row.Name, Reason: rotationEnded + " " + row.Handoff, Placement: row.Placement, Launcher: row.Launcher, Standing: row.Standing, Step: row.Step}, r.Name(), fresh, at)
	do(s.Own(heir, ours(s, out.deps, row.PRs)))
	return true
}

func fold(s *store.Store, records []Record, adoptable map[string]bool, briefs string, d Deps) folded {
	out := folded{deps: d}
	fresh := adoptable != nil
	newest := must(s.NewestHeard())
	for _, r := range records {
		at := r.At.UTC()
		late := max(newest.Sub(at), 0)
		if at.After(newest) {
			newest = at
		}
		reviveFixed(s, r, at)
		if spawn, ok := spawnedBy(r); ok {
			foldSpawn(s, r, spawn, adoptable, fresh, at, briefs, &out)
			continue
		}
		if spawn, ok := registeredBy(r, d.State); ok {
			foldRegistration(s, spawn, adoptable, fresh, at, &out)
		}
		foldRecord(s, r, late, fresh, at, &out)
	}
	return out
}

func foldRegistration(s *store.Store, spawn Spawn, adoptable map[string]bool, fresh bool, at time.Time, out *folded) {
	if _, known := laneOf(s, spawn.Name); known || fresh && !adoptable[spawn.Name] {
		return
	}
	must(s.WantLane(store.Lane{Name: spawn.Name, Brief: spawn.Brief, Model: spawn.Model, Effort: spawn.Effort, Role: or(spawn.Role, "lane"), State: store.LaneDesired, Placement: spawn.Placement, Standing: inheritedDuty(s, spawn.Name, spawn.Brief, "")}, at))
	out.adopted = append(out.adopted, spawn.Name)
}

func foldSpawn(s *store.Store, r Record, spawn Spawn, adoptable map[string]bool, fresh bool, at time.Time, briefs string, out *folded) {
	name, role := spawn.Name, spawn.Role
	if mine, found := laneOf(s, name); found && mine.State == store.LaneRetired && mine.Predecessor != "" && mine.RetiredBy == mine.Predecessor {
		return
	}
	duty := inheritedDuty(s, name, spawn.Brief, spawn.SuccessorOf)
	if spawn.SuccessorOf != "" {
		if old, found := laneOf(s, spawn.SuccessorOf); found && old.Name != name {
			role = or(role, takesOver(s, out.deps, name, old, at))
			if !fresh {
				out.stood = append(out.stood, stoodDown{old.Name, name, old.PRs, r.Seq, r.Name()})
			}
		}
	}
	for _, other := range must(s.Names()) {
		if succeeds(name, other) {
			old, _ := laneOf(s, other)
			if !fresh && isLive(old.State) {
				out.stood = append(out.stood, stoodDown{old.Name, name, old.PRs, r.Seq, r.Name()})
			}
			role = or(role, takesOver(s, out.deps, name, old, at))
		}
	}
	if mine, found := laneOf(s, name); found && mine.State == store.LaneLaunching && mine.Reason != "" && resolvedPath(spawn.Brief) != resolvedPath(filepath.Join(briefs, name+".brief.md")) {
		out.hand = append(out.hand, name)
	}
	if !fresh || adoptable[name] {
		state := store.LaneLaunching
		if fresh {
			state = store.LaneDesired
		}
		if must(s.WantLane(store.Lane{Name: name, Brief: spawn.Brief, Model: spawn.Model, Effort: spawn.Effort, Role: or(role, "lane"), State: state, Placement: spawn.Placement, Launcher: r.Lane, Standing: duty}, at)) {
			out.adopted = append(out.adopted, name)
		} else {
			do(s.SetLaneLaunch(name, spawn.Model, spawn.Effort, spawn.Placement))
			if registered, _ := laneOf(s, name); registered.Launcher == "" && registered.Predecessor == "" {
				do(s.SetLaneLauncher(name, r.Lane))
				do(s.SetLaneRole(name, or(role, registered.Role)))
				do(s.SetLaneStanding(name, or(duty, registered.Standing)))
			}
		}
	}
}

func inheritedDuty(s *store.Store, name, brief, successorOf string) string {
	if must(BriefOf(brief)).Standing != "" {
		return ""
	}
	for _, old := range must(s.AllLanes()) {
		if old.Name != name && (old.Name == successorOf || succeeds(name, old.Name)) && isStanding(old) {
			return or(dutyOf(old), old.Role)
		}
	}
	return ""
}

func resolvedPath(path string) string {
	full, err := filepath.Abs(path)
	if err != nil {
		return path
	}
	if linked, err := filepath.EvalSymlinks(full); err == nil {
		return linked
	}
	return full
}

func pageSeqs(page map[string]any) []int64 {
	raw, _ := page["seqs"].([]any)
	seqs := make([]int64, 0, len(raw))
	for _, value := range raw {
		switch number := value.(type) {
		case float64:
			seqs = append(seqs, int64(number))
		case int64:
			seqs = append(seqs, number)
		}
	}
	return seqs
}

func pagePRs(page map[string]any) []int {
	switch raw := page["prs"].(type) {
	case []int:
		return raw
	case []any:
		prs := make([]int, 0, len(raw))
		for _, value := range raw {
			if number, ok := value.(float64); ok {
				prs = append(prs, int(number))
			}
		}
		return prs
	}
	return nil
}

func pageText(page map[string]any, key string) string {
	text, _ := page[key].(string)
	return text
}

func intersect[T comparable](a, b []T) bool {
	for _, value := range a {
		if slices.Contains(b, value) {
			return true
		}
	}
	return false
}

func foldRecord(s *store.Store, r Record, late time.Duration, fresh bool, at time.Time, out *folded) {
	name, kind, text := r.Lane, r.Kind, r.Text
	row, known := laneOf(s, name)
	heard := kind
	if kind == "done" && r.answersAnother() {
		heard = "answer"
	}
	do(s.LaneHeard(name, at, heard, r.Seq))
	if name == runner && kind == "state" {
		if staged := launchStage.FindStringSubmatch(text); staged != nil {
			do(s.SetLaneStaged(staged[1], at))
		}
	}
	if name == runner && strings.HasPrefix(text, "LAUNCHED ") {
		do(s.ClearEscalation(spriteCapacity))
	}
	if r.Origin != "" {
		do(s.LaneDelivered(name, at, r.Origin, late))
	}
	addressed := addressees(s, r)
	if name == Root || name == mainLane {
		do(s.Order(r.Seq))
		for target := range addressed {
			if _, found := laneOf(s, target); found {
				do(s.SetLaneStoppedOn(target, r.Seq))
			}
		}
	} else if known {
		do(s.SetLaneStoppedOn(name, maxOrder(s, r)))
	}
	if known && row.State == store.LaneHeld && at.After(row.Since) {
		do(s.SetLaneState(name, store.LaneDesired, at, "", ""))
		row, known = laneOf(s, name)
	}
	if known && postedAfterItsOwnEnd(r, row, at) {
		do(s.ReviveLane(name, at))
		row, known = laneOf(s, name)
	}
	if known && row.State == store.LaneRetired {
		if standIn, found := laneOf(s, row.RetiredBy); found && postedBeforeItsStandIn(r, row, standIn, at) {
			do(s.ReviveLane(name, at))
			retire(s, standIn.Name, name, at)
			do(s.Own(name, row.PRs))
			if !fresh {
				out.stood = append(out.stood, stoodDown{standIn.Name, name, standIn.PRs, r.Seq, r.Name()})
			}
			row, known = laneOf(s, name)
		}
	}
	if duty := r.Attr(askdesk.StandingAttr); known && duty != "" {
		do(s.ClaimLane(name, roleDesk, duty))
		row, known = laneOf(s, name)
	}
	if known && isLive(row.State) {
		foldWait(s, r, row, out.deps.Repo)
		if kind == "blocker" && r.Attr("turn") == turnDied && !fresh {
			resume(s, name, fmt.Sprintf("turn:%d", r.Seq), "your last turn died on an API error", r, at)
		}
	}
	if !fresh && (kind == "blocker" || (kind == "ask" && name == Root)) {
		foldPage(s, r, name, addressed)
	}
	if !fresh && (isOrder(kind, text) || isMail(r)) {
		foldUnread(s, r, addressed, at)
	}
	foldCardTaker(s, r, addressed)
	if !fresh {
		out.unassigned = append(out.unassigned, foldToRetired(s, r, addressed, at)...)
	}
	if kind == "red" {
		foldRedClass(s, r, out.deps.Repo, at)
	}
	foldGates(s, r, fresh, at)
	resolved := r.resolved()
	if len(resolved) > 0 {
		do(s.ResolveUnread(resolved))
	}
	answers := resolved
	if r.Re != 0 {
		answers = append(slices.Clone(resolved), r.Re)
	}
	if len(answers) > 0 {
		out.unassigned = slices.DeleteFunc(out.unassigned, func(one unassigned) bool { return slices.Contains(answers, one.record.Seq) })
		for _, waiter := range must(s.Answered(answers, live...)) {
			picks, _ := laneOf(s, waiter)
			do(s.SetLaneWaiting(waiter, 0, at, picks.Awaits))
		}
	}
	if name == Root && kind == "state" {
		if found := parked.FindStringSubmatch(text); found != nil {
			if resting, ok := laneOf(s, found[1]); ok && isLive(resting.State) {
				do(s.SetLaneWaiting(resting.Name, r.Seq, time.Time{}, ""))
			}
		}
	}
	var moved []int
	if kind == "head" {
		moved = r.PRs()
		for _, n := range moved {
			do(s.Unprobe(brokenProbe(n)))
		}
	}
	for holder, page := range must(s.Pages()) {
		if intersect(resolved, pageSeqs(page)) || intersect(moved, pagePRs(page)) {
			do(s.SetLanePage(holder, nil))
		}
	}
	if kind == "owner" {
		foldOwnerPick(s, r, fresh, at)
	}
	if name == Self {
		return
	}
	foldRegistry(s, r, row, known, fresh, at, out)
}

func postedAfterItsOwnEnd(r Record, row store.Lane, at time.Time) bool {
	return row.State == store.LaneRetired && slices.Contains(unprovenEnds, row.RetiredBy) && at.After(row.Since) && !endsItself(r)
}

func reviveFixed(s *store.Store, r Record, at time.Time) {
	row, known := laneOf(s, r.Lane)
	if !known || !slices.Contains(fixedLanes, row.Name) || row.State != store.LaneRetired || !at.After(row.Since) {
		return
	}
	if _, succeeded := laneOf(s, row.RetiredBy); succeeded {
		return
	}
	do(s.ReviveLane(row.Name, at))
}

func postedBeforeItsStandIn(r Record, row, standIn store.Lane, at time.Time) bool {
	neverUp := slices.Contains([]store.LaneState{store.LaneLaunching, store.LaneHeld}, standIn.State) && standIn.Heard.IsZero()
	return standIn.Predecessor == row.Name && neverUp && at.After(row.Since) && !endsItself(r) && !strings.HasPrefix(standIn.Reason, rotationEnded)
}

func endsItself(r Record) bool {
	return slices.Contains(settled, r.Kind) || r.Kind == "state" && stoppedLine.MatchString(r.Text)
}

func maxOrder(s *store.Store, r Record) int64 {
	seqs := r.resolved()
	if r.Re != 0 {
		seqs = append(seqs, r.Re)
	}
	if len(seqs) == 0 {
		return 0
	}
	orders := must(s.Orders(seqs))
	if len(orders) == 0 {
		return 0
	}
	return slices.Max(orders)
}

func foldWait(s *store.Store, r Record, row store.Lane, repo string) {
	var ask int64
	var gates []string
	blocked := blockedOn(s, r, row, repo)
	if slices.Contains(waits, r.Kind) && (cardOnOwner(r) || slices.Contains(r.To, Root) || dutyOf(row) == "" || len(blocked) > 0) {
		ask, gates = r.Seq, gatesOf(r)
		for _, n := range blocked {
			gates = withGate(gates, store.PRGate(n))
		}
	}
	for _, unanswered := range card.FindAllString(row.Awaits, -1) {
		if !slices.Contains(gates, unanswered) {
			gates = withGate(gates, store.PickGate+unanswered)
		}
	}
	do(s.SetLaneWaiting(r.Lane, ask, time.Time{}, strings.Join(gates, " ")))
	do(s.SetLanePage(r.Lane, nil))
}

func withGate(gates []string, gate string) []string {
	if slices.Contains(gates, gate) {
		return gates
	}
	return append(gates, gate)
}

func blockedOn(s *store.Store, r Record, lane store.Lane, repo string) []int {
	if r.Kind != "blocked" {
		return nil
	}
	parks := r.PRs()
	named := slices.Clone(parks)
	for _, found := range prMention.FindAllStringSubmatch(r.Text, -1) {
		if number, err := strconv.Atoi(found[1]); err == nil {
			named = append(named, number)
		}
	}
	if len(parks) == 0 {
		parks = named
	}
	var own, blockers []int
	for _, n := range named {
		switch {
		case slices.Contains(own, n) || slices.Contains(blockers, n):
		case slices.Contains(lane.PRs, n):
			if slices.Contains(parks, n) {
				own = append(own, n)
			}
		case openDrivePR(s, repo, n):
			blockers = append(blockers, n)
		}
	}
	if len(blockers) == 0 {
		return nil
	}
	return append(own, blockers...)
}

func openDrivePR(s *store.Store, repo string, number int) bool {
	row, found, err := s.ReviewRow(repo, number)
	do(err)
	read := must(s.Fact(repo, number, "", gateReadFact))
	return found && !prrow.Terminal(row.State) && (read == "" || strings.HasPrefix(read, prrow.StateOpen+" "))
}

func cardOnOwner(r Record) bool {
	return card.MatchString(r.Text) && (slices.Contains(r.To, Root) || slices.Contains(r.To, OwnerRelay))
}

func gatesOf(r Record) []string {
	var gates []string
	for _, n := range r.PRs() {
		gates = append(gates, store.PRGate(n))
	}
	for _, target := range r.Refs.Targets {
		gates = append(gates, "target:"+target)
	}
	for _, lane := range r.Refs.Lanes {
		gates = append(gates, "lane:"+lane)
	}
	if span, err := time.ParseDuration(r.Attr("until")); r.Kind == "blocked" && err == nil {
		gates = append(gates, clockGate+store.Stamp(r.At.Add(span)))
	}
	if cardOnOwner(r) {
		for _, found := range card.FindAllString(r.Text, -1) {
			if !slices.Contains(gates, found) {
				gates = append(gates, found)
			}
		}
	}
	if strings.Contains(r.Text, noRecommendation) {
		gates = append(gates, noRecommendation)
	}
	return gates
}

func firedBy(s *store.Store, r Record) []string {
	var gates []string
	if r.Kind == kindLanded {
		for _, n := range r.PRs() {
			gates = append(gates, store.PRGate(n))
		}
	}
	if slices.Contains(shipped, r.Kind) {
		for _, target := range r.Refs.Targets {
			gates = append(gates, "target:"+target)
		}
	}
	if slices.Contains(laneEnds, r.Kind) && !closesAnItem(s, r) {
		for _, lane := range endedBy(r) {
			gates = append(gates, "lane:"+lane)
		}
	}
	return gates
}

func readMoved(s *store.Store, r Record) []int {
	if !prrow.IsRead(r.Kind, r.Attr("record")) {
		return nil
	}
	state, ci := r.Attr("state"), r.Attr("ci")
	var moved []int
	for _, n := range r.PRs() {
		if must(s.PutFact(r.Attr("repo"), n, "", gateReadFact, strings.Join([]string{state, ci, r.Attr("head")}, " "), r.At)) && (state != prrow.StateOpen || ci == prrow.CIRed || ci == prrow.CIGreen) {
			moved = append(moved, n)
		}
	}
	return moved
}

func endedBy(r Record) []string {
	if r.Lane == Root && r.Kind == "fix-live" {
		return r.To
	}
	return []string{r.Lane}
}

func closesAnItem(s *store.Store, r Record) bool {
	lane, known := laneOf(s, r.Lane)
	return r.Kind == "done" && (r.answersAnother() || known && isStanding(lane))
}

func foldGates(s *store.Store, r Record, fresh bool, at time.Time) {
	fired, moved := firedBy(s, r), readMoved(s, r)
	if len(fired) == 0 && len(moved) == 0 {
		return
	}
	for _, waiter := range must(s.Lanes(live...)) {
		gates := slices.DeleteFunc(strings.Fields(waiter.Awaits), func(gate string) bool { return !slices.Contains(fired, gate) })
		for _, n := range waiter.Blockers() {
			if slices.Contains(moved, n) {
				gates = append(gates, store.PRGate(n))
			}
		}
		if waiter.Name == r.Lane || len(gates) == 0 {
			continue
		}
		if fresh {
			do(s.SetLaneWaiting(waiter.Name, 0, at, ""))
			continue
		}
		source := r.Lane + "'s " + r.Kind
		if prrow.IsRead(r.Kind, r.Attr("record")) {
			source = fmt.Sprintf("GitHub's read of %s (%s, CI %s)", numberList(r.PRs()), r.Attr("state"), r.Attr("ci"))
		}
		resume(s, waiter.Name, fmt.Sprintf("gate:%d", r.Seq), fmt.Sprintf("%s fired %s, the gate you parked on", source, strings.Join(gates, " ")), r, at)
	}
}

func resume(s *store.Store, name, topic, why string, r Record, at time.Time) {
	do(s.SetLaneWaiting(name, 0, at, ""))
	do(s.SetLanePage(name, map[string]any{"topic": topic, "why": why + ", as record " + r.Name() + " says: " + r.Text, "text": r.Text}))
}

func addressees(s *store.Store, r Record) map[string]bool {
	addressed := map[string]bool{}
	for name := range r.Addressed() {
		addressed[addressee(s, name)] = true
	}
	return addressed
}

func addressee(s *store.Store, name string) string {
	if client.Stem(name) == name {
		return must(Holder(s, "", name))
	}
	return name
}

func foldPage(s *store.Store, r Record, name string, addressed map[string]bool) {
	named := r.PRs()
	if r.Kind == "ask" {
		for _, found := range prMention.FindAllStringSubmatch(r.Text, -1) {
			if number, err := strconv.Atoi(found[1]); err == nil {
				named = append(named, number)
			}
		}
	}
	for _, target := range slices.Sorted(maps.Keys(addressed)) {
		if target == name {
			continue
		}
		paged, found := laneOf(s, target)
		if !found || !isLive(paged.State) {
			continue
		}
		var prs []int
		for _, n := range paged.PRs {
			if slices.Contains(named, n) {
				prs = append(prs, n)
			}
		}
		if len(prs) == 0 {
			continue
		}
		topic := or(r.Topic, strconv.FormatInt(r.Seq, 10))
		what := "coordinator ask"
		if r.Kind == "blocker" {
			what = "blocker"
		}
		page := map[string]any{"topic": topic, "why": fmt.Sprintf("%s %s on your PR %s arrived after your last record: %s", what, r.Name(), numberList(prs), r.Text), "text": r.Text, "prs": prs, "seqs": []int64{}}
		var seqs []int64
		if pageText(paged.Page, "topic") == topic {
			page = maps.Clone(paged.Page)
			seqs = pageSeqs(paged.Page)
		}
		page["seqs"] = append(seqs, r.Seq)
		do(s.SetLaneWaiting(target, 0, r.At.UTC(), ""))
		do(s.SetLanePage(target, page))
	}
}

func failedGrade(kind, text string) bool {
	return kind == "review" && (strings.HasPrefix(text, "finder: major") || strings.HasPrefix(text, "fidelity: missing"))
}

func isOrder(kind, text string) bool {
	return slices.Contains(askingKinds, kind) || failedGrade(kind, text)
}

func isMail(r Record) bool {
	return !slices.Contains(machineAuthors, r.Lane) && !slices.Contains(machineKinds, r.Kind) && !strings.HasPrefix(r.Kind, "lr.")
}

func foldUnread(s *store.Store, r Record, addressed map[string]bool, at time.Time) {
	for target := range addressed {
		lane, found := laneOf(s, target)
		if !found && r.Kind == "owner" {
			lane, found = lastOfFamily(s, target)
		}
		if found && lane.Name != r.Lane && lane.Name != Root && (isLive(lane.State) || r.Kind == "owner") {
			do(s.AddUnread(store.Unread{Lane: lane.Name, Seq: r.Seq, Num: r.Name(), At: at, Author: r.Lane, Kind: r.Kind}))
		}
	}
}

func lastOfFamily(s *store.Store, stem string) (store.Lane, bool) {
	last, newestNumber := "", 0
	for _, name := range must(s.Names()) {
		if base, number := family(name); base == stem && number > newestNumber {
			last, newestNumber = name, number
		}
	}
	return laneOf(s, last)
}

func foldCardTaker(s *store.Store, r Record, addressed map[string]bool) {
	cards := card.FindAllString(r.Text, -1)
	if len(cards) == 0 {
		return
	}
	taker := ""
	if r.Lane == Root {
		named := maps.Clone(addressed)
		for _, word := range laneWord.FindAllString(r.Text, -1) {
			named[word] = true
		}
		for _, name := range slices.Sorted(maps.Keys(named)) {
			if lane, found := laneOf(s, name); found && isLive(lane.State) && !DeskLane.MatchString(name) {
				taker = name
				break
			}
		}
	} else if lane, found := laneOf(s, r.Lane); found && lane.Launcher == Root && !DeskLane.MatchString(r.Lane) {
		taker = r.Lane
	}
	if taker == "" {
		return
	}
	for _, named := range cards {
		do(s.TakeCard(named, taker))
	}
}

func foldToRetired(s *store.Store, r Record, addressed map[string]bool, at time.Time) (unreached []unassigned) {
	answer := strings.HasPrefix(r.Topic, CardWakeTopic)
	if isRequest(r) || !answer && !asksOfALane(r) {
		return nil
	}
	assigns := r.Lane == Root || r.Lane == Self
	named := maps.Clone(addressed)
	if assigns {
		for _, pair := range laneAssigned.FindAllStringSubmatch(r.Text, -1) {
			named[addressee(s, pair[1])] = true
		}
	}
	reached := map[string]bool{}
	for _, target := range slices.Sorted(maps.Keys(named)) {
		lane, found := newest(s, target)
		if !found && assigns {
			lane, found = lastOfFamily(s, target)
		}
		if !found || lane.Name == r.Lane || reached[lane.Name] {
			continue
		}
		reached[lane.Name] = true
		if assigns && isLive(lane.State) && lane.Name != target && !addressed[lane.Name] {
			do(s.AddUnread(store.Unread{Lane: lane.Name, Seq: r.Seq, Num: r.Name(), At: at, Author: r.Lane, Kind: r.Kind}))
			continue
		}
		if lane.State != store.LaneRetired {
			continue
		}
		if !answer && !restedOnItsOwnEnd(lane) {
			if assigns && !slices.Contains(fixedLanes, lane.Name) && (lane.Last != "done" || !slices.Contains(r.resolved(), must(s.NewestSeq(lane.Name)))) {
				unreached = append(unreached, unassigned{r, lane})
			}
			continue
		}
		answered := strings.TrimPrefix(r.Topic, CardWakeTopic)
		if taker, took := newest(s, must(s.CardTaker(answered))); answer && took && isLive(taker.State) && taker.Name != lane.Name {
			do(s.SetLanePage(taker.Name, map[string]any{"topic": r.Topic, "why": fmt.Sprintf("the owner answered %s, which root gave you after %s drew it: %s", answered, lane.Name, truncated(r.Text, 240)), "text": fmt.Sprintf("%s from %s: %s", r.Name(), r.Lane, r.Text), "seqs": []int64{r.Seq}}))
			continue
		}
		said := fmt.Sprintf("%s from %s: %s", r.Name(), r.Lane, r.Text)
		page := map[string]any{"topic": retiredMailTopic, "why": fmt.Sprintf("got %s's %s %s after it retired: %s", r.Lane, r.Kind, r.Name(), truncated(r.Text, 240)), "text": said}
		var seqs []int64
		if pageText(lane.Page, "topic") == retiredMailTopic {
			page = maps.Clone(lane.Page)
			page["text"] = pageText(lane.Page, "text") + "\n" + said
			seqs = pageSeqs(lane.Page)
		}
		page["seqs"] = append(seqs, r.Seq)
		do(s.SetLanePage(lane.Name, page))
	}
	return unreached
}

func asksOfALane(r Record) bool {
	asker := r.Lane == Self && r.Kind == "ask" || !slices.Contains(machineAuthors, r.Lane)
	return r.Lane != boardLink && asker && (isOrder(r.Kind, r.Text) || slices.Contains(replies, r.Kind))
}

func restedOnItsOwnEnd(lane store.Lane) bool {
	return !isStanding(lane) && (lane.RetiredBy == retiredSilent || (lane.RetiredBy == retiredStopped && lane.Last == "stopped"))
}

func Cards(text string) []string {
	return card.FindAllString(text, -1)
}

func CardLane(s *store.Store, card string) (lane string, err error) {
	defer recovered(&err)
	taker := must(s.CardTaker(card))
	if DeskLane.MatchString(taker) {
		return "", nil
	}
	return liveHolder(s, taker), nil
}

func PRLane(s *store.Store, pr int) (lane string, err error) {
	defer recovered(&err)
	return liveHolder(s, must(s.OwnerOf([]int{pr}))[pr]), nil
}

func liveHolder(s *store.Store, name string) string {
	if name == "" {
		return ""
	}
	holder := must(Holder(s, "", name))
	if found, ok := laneOf(s, holder); !ok || found.State == store.LaneRetired {
		return ""
	}
	return holder
}

func AnswersCard(attrs map[string]string, cardID string) bool {
	return attrs[CardAttr] == cardID
}

func foldOwnerPick(s *store.Store, r Record, fresh bool, at time.Time) {
	answers := func(found string) bool { return AnswersCard(r.Fields.Attrs, found) }
	for _, built := range must(s.BranchCards()) {
		if answers(built.Card) {
			do(s.AnswerBranchCard(built.Lane, built.Card, at))
		}
	}
	for _, waiter := range must(s.Lanes(live...)) {
		cards := card.FindAllString(waiter.Awaits, -1)
		i := slices.IndexFunc(cards, answers)
		if i < 0 {
			continue
		}
		found := cards[i]
		do(s.SetLaneWaiting(waiter.Name, 0, at, ""))
		if !fresh {
			do(s.SetLanePage(waiter.Name, map[string]any{"topic": fmt.Sprintf("owner:%d", r.Seq), "why": fmt.Sprintf("the owner answered %s, the card you wait on: %s", found, r.Text), "text": r.Text, "prs": append([]int{}, waiter.PRs...)}))
		}
	}
}

func foldRegistry(s *store.Store, r Record, row store.Lane, known, fresh bool, at time.Time, out *folded) {
	name, kind, text := r.Lane, r.Kind, r.Text
	isRoot := name == Root
	request := isRequest(r)
	if (isRoot || name == mainLane) && kind == kindRequest && r.Addressed()[Self] && !request && r.Attr("verb") != "" && !fresh {
		out.untyped = append(out.untyped, r)
	}
	if request {
		grant(s, r, fresh, at, out)
		return
	}
	if name == runner {
		if found := workerEnded.FindStringSubmatch(text); found != nil {
			if ended, ok := laneOf(s, found[1]); ok && ended.State == store.LaneRetired && ended.RetiredBy == retiredStopped {
				out.retireHolding(s, ended.Name, retiredWorkerDone, at)
				return
			}
		}
		if found := launchRefused.FindStringSubmatch(text); found != nil && !fresh {
			out.refused = append(out.refused, refusal{found[2], found[1] + ": " + strings.TrimSpace(found[3])})
			return
		}
		if found := workerDone.FindStringSubmatch(text); found != nil {
			if ended, ok := laneOf(s, found[1]); ok && ended.State != store.LaneRetired {
				if ended.Heard.IsZero() {
					hold(s, ended.Name, unheardDone, at)
					if !fresh {
						out.unheard = append(out.unheard, ended.Name)
					}
					return
				}
				if !out.rotated(s, ended, r, fresh, at) && out.owesNothing(s, ended.Name, fresh, at) {
					out.retireHolding(s, ended.Name, retiredWorkerDone, at)
				}
				return
			}
		}
		if found := withdrawnWork.FindStringSubmatch(text); found != nil {
			if ended, ok := laneOf(s, found[1]); ok && ended.State != store.LaneRetired {
				if out.owesNothing(s, ended.Name, fresh, at) {
					retire(s, ended.Name, "withdrawn", at)
					out.handOn(s, ended.Name, ended.PRs, fresh)
				}
				return
			}
		}
	}
	if kind == "handoff" && known && r.Refs.CCN != "" {
		flushed := time.Time{}
		if !row.Asked.IsZero() && r.Re != 0 {
			flushed = at
		}
		do(s.SetLaneHandoff(name, r.Refs.CCN, flushed))
		endedItself := (row.RetiredBy == retiredWorkerDone || row.RetiredBy == retiredStopped) && row.StoppedOn == 0 && row.Last != "done"
		if row.State == store.LaneRetired && endedItself {
			row.Handoff, row.Flushed = r.Refs.CCN, flushed
			out.rotated(s, row, r, fresh, at)
		}
		return
	}
	settles := known && !slices.Contains(fixedLanes, name) && row.State != store.LaneRetired
	if kind == "handoff" && settles && !r.answersAnother() {
		if out.owesNothing(s, name, fresh, at) {
			retire(s, name, "stand-down", at)
		}
		return
	}
	if found := handedOff.FindStringSubmatch(text); found != nil && name == found[1]+"-handoff" {
		if owner, ok := newest(s, found[1]); ok {
			do(s.SetLaneHandoff(owner.Name, found[2], time.Time{}))
			return
		}
	}
	if slices.Contains(carries, kind) && !isRoot && foldCarried(s, r, at) {
		return
	}
	if slices.Contains(claims, kind) && known && isLive(row.State) && slices.Contains(builders, row.Role) {
		foldClaimed(s, r)
	}
	switch {
	case kind == kindLanded:
		foldLanded(s, r, at)
	case kind == "done" && settles && !r.answersAnother() && !isStanding(row):
		if out.owesNothing(s, name, fresh, at) {
			out.retireHolding(s, name, "done", at)
		}
	case kind == "done" && !known && !isRoot && name != mainLane && name != runner && !r.answersAnother():
		out.handOn(s, name, must(s.HeldBy(name, out.deps.Ledger)), fresh)
	case settles && (kind == "stopped" || kind == "stand-down" || (kind == "state" && stoppedLine.MatchString(text))):
		if !out.rotated(s, row, r, fresh, at) && out.owesNothing(s, name, fresh, at) {
			retire(s, name, "stand-down", at)
		}
	case kind == "go" && name != Self && withdrawOrder.MatchString(text):
		foldWithdraw(s, r, fresh, at, out)
	case kind == "stand-down" && isRoot:
		foldRootStandDown(s, r, fresh, at, out)
	}
}

func foldCarried(s *store.Store, r Record, at time.Time) bool {
	addressed := addressees(s, r)
	var numbers []int
	for n, owner := range must(s.OwnerOf(r.PRs())) {
		if !addressed[owner] {
			numbers = append(numbers, n)
		}
	}
	if len(numbers) == 0 {
		return false
	}
	slices.Sort(numbers)
	for _, other := range must(s.Lanes(store.LaneLaunching)) {
		if other.Name == r.Lane || len(other.PRs) == 0 || other.Heard.After(other.Since) {
			continue
		}
		covered := true
		for _, n := range other.PRs {
			covered = covered && slices.Contains(numbers, n)
		}
		if covered {
			retire(s, other.Name, r.Lane, at)
		}
	}
	do(s.Own(r.Lane, numbers))
	return true
}

func foldClaimed(s *store.Store, r Record) {
	var unowned []int
	for n, owner := range must(s.OwnerOf(r.PRs())) {
		if owner == "" {
			unowned = append(unowned, n)
		}
	}
	do(s.Own(r.Lane, unowned))
}

func foldLanded(s *store.Store, r Record, at time.Time) {
	numbers := r.PRs()
	owners := map[string]bool{}
	var landed []int
	if len(numbers) > 0 {
		for n, owner := range must(s.OwnerOf(numbers)) {
			if owner == r.Lane {
				continue
			}
			landed = append(landed, n)
			if owner != "" {
				owners[owner] = true
			}
		}
	}
	do(s.Disown(landed))
	for owner := range owners {
		settleOwner(s, owner, at)
	}
}

func dropQueuedLaunch(s *store.Store, stood store.Lane, at time.Time, out *folded) {
	queued := stood
	if stood.State == store.LaneRetired {
		heir, found := laneOf(s, stood.RetiredBy)
		if !found || heir.Predecessor != stood.Name || heir.Heard.After(heir.Since) {
			return
		}
		queued = heir
	}
	if queued.State != store.LaneLaunching || queued.Reason == "" {
		return
	}
	retire(s, queued.Name, Root, at)
	out.handOn(s, queued.Name, queued.PRs, false)
	out.hand = append(out.hand, queued.Name)
}

func foldWithdraw(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	withdrawn, found := laneOf(s, withdrawOrder.FindStringSubmatch(r.Text)[1])
	if !found || withdrawn.State != store.LaneLaunching && !neverStarted(withdrawn) {
		return
	}
	retire(s, withdrawn.Name, "withdrawn", at)
	out.handOn(s, withdrawn.Name, withdrawn.PRs, fresh)
}

func settleOwner(s *store.Store, owner string, at time.Time) {
	owned, ok := laneOf(s, owner)
	if !ok || !isLive(owned.State) || len(owned.PRs) > 0 {
		return
	}
	do(s.LaneHeard(owner, at, kindLanded, 0))
}

func foldRootStandDown(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	successor := r.Attr("successor")
	named := map[string]bool{}
	for _, listed := range standDownList.FindAllStringSubmatch(r.Text, -1) {
		before, _, _ := strings.Cut(listed[1], " for ")
		for _, word := range laneWord.FindAllString(before, -1) {
			if _, found := laneOf(s, word); found {
				named[word] = true
			}
		}
	}
	successor = heirOf(s, successor, slices.Collect(maps.Keys(named))...)
	if len(named) == 0 {
		for _, to := range r.To {
			named[to] = true
		}
	}
	heirs := map[string]bool{successor: true}
	for old := range named {
		gone, _ := laneOf(s, old)
		heirs[gone.RetiredBy] = true
		if named[gone.Predecessor] {
			heirs[old] = true
		}
	}
	for heir := range heirs {
		delete(named, heir)
	}
	for _, old := range slices.Sorted(maps.Keys(named)) {
		gone, found := laneOf(s, old)
		if !found {
			continue
		}
		if !fresh && isLive(gone.State) {
			if one, found := unreadBy(s, r, old, citedIn(r), Request{Verb: "stand-down", Lane: old, Successor: successor}); found {
				out.unread = append(out.unread, one)
				hold(s, old, one.why, at)
				continue
			}
		}
		ends := gone.State != store.LaneRetired || slices.Contains(unprovenEnds, gone.RetiredBy)
		if successor == "" && ends && isStanding(gone) {
			if !fresh {
				out.kept = append(out.kept, stoodDown{old, "", gone.PRs, r.Seq, r.Name()})
			}
			continue
		}
		if successor == "" && gone.State != store.LaneRetired && !out.orderedOff(s, old, fresh, at) {
			continue
		}
		if successor == "" && !fresh {
			dropQueuedLaunch(s, gone, at, out)
		}
		if ends {
			retire(s, old, or(successor, Root), at)
			if !fresh {
				out.ended = append(out.ended, stoodDown{old, successor, gone.PRs, r.Seq, r.Name()})
			}
		}
		if successor != "" && gone.State != store.LaneRetired {
			cost := successorCost(gone)
			wantHeir(s, store.Lane{Name: successor, Brief: gone.Brief, Model: cost.Model, Effort: cost.Effort, Role: gone.Role, State: store.LaneLaunching, Handoff: gone.Handoff, PRs: ours(s, out.deps, gone.PRs), Predecessor: old, Launcher: gone.Launcher, Standing: gone.Standing, Step: gone.Step}, r.Name(), fresh, at)
			continue
		}
		if successor == "" {
			out.handOn(s, old, gone.PRs, fresh)
		}
	}
}
