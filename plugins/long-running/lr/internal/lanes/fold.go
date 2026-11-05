package lanes

import (
	"fmt"
	"maps"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type stoodDown struct {
	name      string
	successor string
	prs       []int
	seq       int64
}

type unread struct {
	name string
	why  string
}

type refusal struct {
	lane string
	why  string
}

type folded struct {
	adopted []string
	hand    []string
	unread  []unread
	owing   []owing
	stood   []stoodDown
	untyped []int64
	unheard []string
	refused []refusal
	deps    Deps
}

func takesOver(s *store.Store, name string, old store.Lane, at time.Time) string {
	do(s.Own(name, old.PRs))
	if _, known := laneOf(s, old.RetiredBy); isLive(old.State) || !known {
		retire(s, old.Name, name, at)
		return old.Role
	}
	return ""
}

func grant(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	name, verb, cited := r.Attr("lane"), r.Attr("verb"), r.Attr("last-read")
	row, known := laneOf(s, name)
	switch {
	case verb == "park" && known:
		do(s.SetLaneWaiting(name, r.Seq, time.Time{}, ""))
	case verb == "unpark" && known:
		if fresh {
			do(s.SetLaneWaiting(name, 0, at, ""))
			return
		}
		resume(s, name, fmt.Sprintf("unpark:%d", r.Seq), "the coordinator lifted your park", r, at)
	case verb == "done" && known && row.State != store.LaneRetired:
		if out.owesNothing(s, name, fresh, at) {
			retire(s, name, "done", at)
			do(s.SetLaneStanding(name, ""))
		}
	case verb == "done" && known && row.Standing != "":
		do(s.SetLaneStanding(name, ""))
	case verb == "revive" && known && row.State == store.LaneRetired:
		do(s.ReviveLane(name, at))
		do(s.SetLaneStanding(name, or(row.Standing, inheritedDuty(s, name, row.Brief, ""))))
	case verb == "owner":
		numbers := r.PRs()
		if !fresh {
			var refused []unread
			for _, stripped := range strippedBy(s, name, numbers) {
				if why := unreadBy(s, r, stripped, cited); why != "" {
					refused = append(refused, unread{stripped, why})
				}
			}
			if len(refused) > 0 {
				out.unread = append(out.unread, refused...)
				for _, one := range refused {
					hold(s, one.name, one.why, at)
				}
				return
			}
		}
		do(s.Own(name, numbers))
	case verb == "stand-down" && known:
		successor := r.Attr("successor")
		if !fresh && isLive(row.State) {
			if why := unreadBy(s, r, name, cited); why != "" {
				out.unread = append(out.unread, unread{name, why})
				hold(s, name, why, at)
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
		}
		if successor != "" {
			cost := successorCost(row)
			must(s.WantLane(store.Lane{Name: successor, Brief: row.Brief, Model: cost.Model, Effort: cost.Effort, Role: row.Role, State: store.LaneLaunching, Handoff: row.Handoff, Predecessor: name, Placement: row.Placement, Launcher: row.Launcher, Standing: row.Standing}, at))
			do(s.Own(successor, row.PRs))
		} else {
			do(s.Own(Root, row.PRs))
		}
		if !fresh {
			out.stood = append(out.stood, stoodDown{name, successor, row.PRs, r.Seq})
		}
	}
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
		if spawn, ok := spawnedBy(r); ok {
			foldSpawn(s, r, spawn, adoptable, fresh, at, briefs, &out)
			continue
		}
		foldRecord(s, r, late, fresh, at, &out)
	}
	return out
}

func foldSpawn(s *store.Store, r Record, spawn Spawn, adoptable map[string]bool, fresh bool, at time.Time, briefs string, out *folded) {
	name, role := spawn.Name, spawn.Role
	duty := inheritedDuty(s, name, spawn.Brief, spawn.SuccessorOf)
	if spawn.SuccessorOf != "" {
		if old, found := laneOf(s, spawn.SuccessorOf); found && old.Name != name {
			role = or(role, takesOver(s, name, old, at))
			if !fresh {
				out.stood = append(out.stood, stoodDown{old.Name, name, old.PRs, r.Seq})
			}
		}
	}
	for _, other := range must(s.Names()) {
		if succeeds(name, other) {
			old, _ := laneOf(s, other)
			if !fresh && isLive(old.State) {
				out.stood = append(out.stood, stoodDown{old.Name, name, old.PRs, r.Seq})
			}
			role = or(role, takesOver(s, name, old, at))
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
	do(s.LaneHeard(name, at, kind, r.Seq))
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
	if known && isLive(row.State) {
		foldWait(s, r, row)
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
	if !fresh {
		foldToRetired(s, r, addressed)
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
		for _, waiter := range must(s.Answered(answers, live...)) {
			do(s.SetLaneWaiting(waiter, 0, at, ""))
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
	endsItself := slices.Contains(settled, r.Kind) || r.Kind == "state" && stoppedLine.MatchString(r.Text)
	return row.State == store.LaneRetired && slices.Contains(unprovenEnds, row.RetiredBy) && at.After(row.Since) && !endsItself
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

func foldWait(s *store.Store, r Record, row store.Lane) {
	var ask int64
	awaits := ""
	if slices.Contains(waits, r.Kind) && (cardOnOwner(r) || slices.Contains(r.To, Root) || dutyOf(row) == "") {
		ask, awaits = r.Seq, strings.Join(gatesOf(r), " ")
	}
	do(s.SetLaneWaiting(r.Lane, ask, time.Time{}, awaits))
	do(s.SetLanePage(r.Lane, nil))
}

func cardOnOwner(r Record) bool {
	return card.MatchString(r.Text) && (slices.Contains(r.To, Root) || slices.Contains(r.To, OwnerRelay))
}

func gatesOf(r Record) []string {
	var gates []string
	for _, n := range r.PRs() {
		gates = append(gates, fmt.Sprintf("pr:%d", n))
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
			gates = append(gates, fmt.Sprintf("pr:%d", n))
		}
	}
	if slices.Contains(shipped, r.Kind) {
		for _, target := range r.Refs.Targets {
			gates = append(gates, "target:"+target)
		}
	}
	if slices.Contains(laneEnds, r.Kind) && !closesAnItem(s, r) {
		gates = append(gates, "lane:"+r.Lane)
	}
	return gates
}

func closesAnItem(s *store.Store, r Record) bool {
	lane, known := laneOf(s, r.Lane)
	return r.Kind == "done" && known && isStanding(lane)
}

func foldGates(s *store.Store, r Record, fresh bool, at time.Time) {
	fired := firedBy(s, r)
	if len(fired) == 0 {
		return
	}
	for _, waiter := range must(s.Lanes(live...)) {
		if waiter.Name == r.Lane || !intersect(fired, strings.Fields(waiter.Awaits)) {
			continue
		}
		if fresh {
			do(s.SetLaneWaiting(waiter.Name, 0, at, ""))
			continue
		}
		resume(s, waiter.Name, fmt.Sprintf("gate:%d", r.Seq), "what you parked on happened: "+strings.Join(fired, " "), r, at)
	}
}

func resume(s *store.Store, name, topic, why string, r Record, at time.Time) {
	do(s.SetLaneWaiting(name, 0, at, ""))
	do(s.SetLanePage(name, map[string]any{"topic": topic, "why": why + ", as record #" + strconv.FormatInt(r.Seq, 10) + " says: " + r.Text, "text": r.Text}))
}

func addressees(s *store.Store, r Record) map[string]bool {
	addressed := map[string]bool{}
	for name := range r.Addressed() {
		if client.Stem(name) == name {
			name = must(Holder(s, "", name))
		}
		addressed[name] = true
	}
	return addressed
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
		page := map[string]any{"topic": topic, "why": fmt.Sprintf("%s #%d on your PR %s arrived after your last record: %s", what, r.Seq, numberList(prs), r.Text), "text": r.Text, "prs": prs, "seqs": []int64{}}
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
			do(s.AddUnread(store.Unread{Lane: lane.Name, Seq: r.Seq, At: at, Author: r.Lane, Kind: r.Kind}))
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

func foldToRetired(s *store.Store, r Record, addressed map[string]bool) {
	answer := strings.HasPrefix(r.Topic, CardWakeTopic)
	if !answer && !asksOfALane(r) {
		return
	}
	for _, target := range slices.Sorted(maps.Keys(addressed)) {
		lane, found := newest(s, target)
		if !found || lane.Name == r.Lane || lane.State != store.LaneRetired || (!answer && !restedOnItsOwnEnd(lane)) {
			continue
		}
		said := fmt.Sprintf("#%d from %s: %s", r.Seq, r.Lane, r.Text)
		page := map[string]any{"topic": retiredMailTopic, "why": fmt.Sprintf("got %s's %s #%d after it retired: %s", r.Lane, r.Kind, r.Seq, truncated(r.Text, 240)), "text": said}
		var seqs []int64
		if pageText(lane.Page, "topic") == retiredMailTopic {
			page = maps.Clone(lane.Page)
			page["text"] = pageText(lane.Page, "text") + "\n" + said
			seqs = pageSeqs(lane.Page)
		}
		page["seqs"] = append(seqs, r.Seq)
		do(s.SetLanePage(lane.Name, page))
	}
}

func asksOfALane(r Record) bool {
	asker := r.Lane == Self && r.Kind == "ask" || !slices.Contains(machineAuthors, r.Lane)
	return r.Lane != boardLink && asker && (isOrder(r.Kind, r.Text) || slices.Contains(replies, r.Kind))
}

func restedOnItsOwnEnd(lane store.Lane) bool {
	return !isStanding(lane) && (lane.RetiredBy == retiredSilent || (lane.RetiredBy == retiredStopped && lane.Last == "stopped"))
}

func foldOwnerPick(s *store.Store, r Record, fresh bool, at time.Time) {
	marks, named := picked.FindAllString(r.Text, -1), card.FindAllString(r.Text, -1)
	answers := func(found string) bool {
		return slices.Contains(named, found) || slices.Contains(marks, "("+found+")") || slices.Contains(marks, "("+strings.TrimPrefix(found, "card-")+")")
	}
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
		out.untyped = append(out.untyped, r.Seq)
	}
	if request {
		grant(s, r, fresh, at, out)
		return
	}
	if name == runner {
		if found := workerEnded.FindStringSubmatch(text); found != nil {
			if ended, ok := laneOf(s, found[1]); ok && ended.State == store.LaneRetired && ended.RetiredBy == retiredStopped {
				retire(s, ended.Name, retiredWorkerDone, at)
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
				if out.owesNothing(s, ended.Name, fresh, at) {
					retire(s, ended.Name, retiredWorkerDone, at)
				}
				return
			}
		}
		if found := withdrawnWork.FindStringSubmatch(text); found != nil {
			if ended, ok := laneOf(s, found[1]); ok && ended.State != store.LaneRetired {
				if out.owesNothing(s, ended.Name, fresh, at) {
					retire(s, ended.Name, "withdrawn", at)
					do(s.Own(Root, ended.PRs))
				}
				return
			}
		}
	}
	if kind == "handoff" && known && r.Refs.CCN != "" {
		flushed := time.Time{}
		if !row.Asked.IsZero() {
			flushed = at
		}
		do(s.SetLaneHandoff(name, r.Refs.CCN, flushed))
		return
	}
	settles := known && !isRoot && row.State != store.LaneRetired
	if kind == "handoff" && settles && r.Re == 0 {
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
	case kind == "done" && settles && r.Re == 0 && !isStanding(row):
		if out.owesNothing(s, name, fresh, at) {
			retire(s, name, "done", at)
		}
	case settles && (kind == "stopped" || kind == "stand-down" || (kind == "state" && stoppedLine.MatchString(text))):
		if out.owesNothing(s, name, fresh, at) {
			retire(s, name, "stand-down", at)
		}
	case kind == "go" && name != Self && withdrawOrder.MatchString(text):
		foldWithdraw(s, r, at)
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
		if other.Name == r.Lane || len(other.PRs) == 0 {
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
	if len(numbers) > 0 {
		for _, owner := range must(s.OwnerOf(numbers)) {
			if owner != "" {
				owners[owner] = true
			}
		}
	}
	do(s.Disown(numbers))
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
	do(s.Own(Root, queued.PRs))
	out.hand = append(out.hand, queued.Name)
}

func foldWithdraw(s *store.Store, r Record, at time.Time) {
	withdrawn, found := laneOf(s, withdrawOrder.FindStringSubmatch(r.Text)[1])
	if !found || withdrawn.State != store.LaneLaunching {
		return
	}
	retire(s, withdrawn.Name, "withdrawn", at)
	do(s.Own(Root, withdrawn.PRs))
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
			if why := unreadBy(s, r, old, citedIn(r)); why != "" {
				out.unread = append(out.unread, unread{old, why})
				hold(s, old, why, at)
				continue
			}
		}
		if successor == "" && gone.State != store.LaneRetired && !out.orderedOff(s, old, fresh, at) {
			continue
		}
		if successor == "" && !fresh {
			dropQueuedLaunch(s, gone, at, out)
		}
		if gone.State != store.LaneRetired {
			retire(s, old, or(successor, Root), at)
			if successor != "" {
				cost := successorCost(gone)
				must(s.WantLane(store.Lane{Name: successor, Brief: gone.Brief, Model: cost.Model, Effort: cost.Effort, Role: gone.Role, State: store.LaneLaunching, Handoff: gone.Handoff, PRs: gone.PRs, Predecessor: old, Launcher: gone.Launcher, Standing: gone.Standing}, at))
				continue
			}
		} else if slices.Contains(unprovenEnds, gone.RetiredBy) {
			retire(s, old, or(successor, Root), at)
		}
		if successor == "" {
			do(s.Own(Root, gone.PRs))
		}
	}
}
