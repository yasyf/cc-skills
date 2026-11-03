package lanes

import (
	"fmt"
	"maps"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

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

type folded struct {
	adopted []string
	hand    []string
	dead    []string
	unread  []unread
	stood   []stoodDown
	untyped []int64
}

func takesOver(s *store.Store, name string, old store.Lane, at time.Time) {
	do(s.Own(name, old.PRs))
	if _, known := laneOf(s, old.RetiredBy); isLive(old.State) || !known {
		retire(s, old.Name, name, at)
	}
}

func deadOwner(s *store.Store, name string, numbers []int, at time.Time) string {
	probe, found := must(s.Probes())[name]
	if len(numbers) == 0 || !found || probe.Dead.IsZero() {
		return ""
	}
	return fmt.Sprintf("%s for %s: root named it owner at %s, but it left the supervisor's %s ask unanswered; %s", name, numberList(numbers), pacific(at), pacific(probe.Asked), successorNote(s, name))
}

func grant(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	name, verb, cited := r.Attr("lane"), r.Attr("verb"), r.Attr("last-read")
	row, known := laneOf(s, name)
	switch {
	case verb == "park" && known:
		do(s.SetLaneWaiting(name, r.Seq, time.Time{}, ""))
	case verb == "unpark" && known:
		do(s.SetLaneWaiting(name, 0, at, ""))
	case verb == "done" && known && row.State != store.LaneRetired:
		retire(s, name, "done", at)
	case verb == "owner":
		numbers := r.PRs()
		running := map[string]bool{}
		for _, other := range must(s.Lanes(live...)) {
			running[other.Name] = true
		}
		taken := map[string]bool{}
		for _, held := range must(s.OwnerOf(numbers)) {
			if held != "" && held != name && running[held] {
				taken[held] = true
			}
		}
		if !fresh {
			var refused []unread
			for _, held := range slices.Sorted(maps.Keys(taken)) {
				if why := unreadBy(s, r, held, cited); why != "" {
					refused = append(refused, unread{held, why})
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
		if !fresh {
			if line := deadOwner(s, name, numbers, at); line != "" {
				out.dead = append(out.dead, line)
			}
		}
	case verb == "stand-down" && known:
		successor := r.Attr("successor")
		if !fresh && isLive(row.State) {
			if why := unreadBy(s, r, name, cited); why != "" {
				out.unread = append(out.unread, unread{name, why})
				hold(s, name, why, at)
				return
			}
		}
		if successor == "" && !fresh {
			dropQueuedLaunch(s, row, at, out)
		}
		if row.State != store.LaneRetired {
			retire(s, name, or(successor, Root), at)
		}
		if successor != "" {
			must(s.WantLane(store.Lane{Name: successor, Brief: row.Brief, Model: row.Model, Effort: row.Effort, Role: row.Role, State: store.LaneLaunching, Handoff: row.Handoff, Predecessor: name, Placement: row.Placement}, at))
			do(s.Own(successor, row.PRs))
		} else {
			do(s.Own(Root, row.PRs))
		}
		if !fresh {
			out.stood = append(out.stood, stoodDown{name, successor, row.PRs, r.Seq})
		}
	}
}

func fold(s *store.Store, records []Record, adoptable map[string]bool, briefs string) folded {
	var out folded
	fresh := adoptable != nil
	for _, r := range records {
		at := r.At.UTC()
		if spawn, ok := spawnedBy(r); ok {
			foldSpawn(s, r, spawn, adoptable, fresh, at, briefs, &out)
			continue
		}
		foldRecord(s, r, fresh, at, &out)
	}
	return out
}

func foldSpawn(s *store.Store, r Record, spawn Spawn, adoptable map[string]bool, fresh bool, at time.Time, briefs string, out *folded) {
	name := spawn.Name
	if spawn.SuccessorOf != "" {
		if old, found := laneOf(s, spawn.SuccessorOf); found && old.Name != name {
			takesOver(s, name, old, at)
			if !fresh {
				out.stood = append(out.stood, stoodDown{old.Name, name, old.PRs, r.Seq})
			}
		}
	}
	for _, other := range must(s.Names()) {
		if succeeds(name, other) {
			old, _ := laneOf(s, other)
			takesOver(s, name, old, at)
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
		if must(s.WantLane(store.Lane{Name: name, Brief: spawn.Brief, Model: spawn.Model, Effort: spawn.Effort, Role: spawn.Role, State: state, Placement: spawn.Placement}, at)) {
			out.adopted = append(out.adopted, name)
		}
	}
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

func foldRecord(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	name, kind, text := r.Lane, r.Kind, r.Text
	row, known := laneOf(s, name)
	do(s.LaneHeard(name, at, kind, r.Seq))
	addressed := r.Addressed()
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
	if known && isLive(row.State) {
		foldWait(s, r, name)
	}
	if !fresh && (kind == "blocker" || (kind == "ask" && name == Root)) {
		foldPage(s, r, name, addressed)
	}
	resolved := r.resolved()
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

func foldWait(s *store.Store, r Record, name string) {
	found := ""
	if slices.Contains(waits, r.Kind) {
		found = card.FindString(r.Text)
	}
	var ask int64
	if slices.Contains(waits, r.Kind) && (found != "" || slices.Contains(r.To, Root)) {
		ask = r.Seq
	}
	do(s.SetLaneWaiting(name, ask, time.Time{}, found))
	do(s.SetLanePage(name, nil))
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
		what := "root ask"
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
		do(s.SetLanePage(target, page))
	}
}

func foldOwnerPick(s *store.Store, r Record, fresh bool, at time.Time) {
	marks := picked.FindAllString(r.Text, -1)
	for _, waiter := range must(s.Lanes(live...)) {
		if waiter.Awaits == "" {
			continue
		}
		if !slices.Contains(marks, "("+waiter.Awaits+")") && !slices.Contains(marks, "("+strings.TrimPrefix(waiter.Awaits, "card-")+")") {
			continue
		}
		do(s.SetLaneWaiting(waiter.Name, 0, at, ""))
		if !fresh {
			do(s.SetLanePage(waiter.Name, map[string]any{"topic": fmt.Sprintf("owner:%d", r.Seq), "why": fmt.Sprintf("the owner answered %s, the card you wait on: %s", waiter.Awaits, r.Text), "text": r.Text, "prs": append([]int{}, waiter.PRs...)}))
		}
	}
}

func foldRegistry(s *store.Store, r Record, row store.Lane, known, fresh bool, at time.Time, out *folded) {
	name, kind, text := r.Lane, r.Kind, r.Text
	isRoot := name == Root
	request := isRequest(r)
	if (isRoot || name == mainLane) && kind == kindRequest && r.Addressed()[Self] && !request && (r.Attr("verb") != "" || namesVerb(text)) && !fresh {
		out.untyped = append(out.untyped, r.Seq)
	}
	if isRoot && kind == kindRequest {
		for _, found := range retiredWord.FindAllStringSubmatch(text, -1) {
			if gone, ok := laneOf(s, found[1]); ok && gone.State != store.LaneRetired {
				retire(s, gone.Name, Root, at)
			}
		}
	}
	if request {
		grant(s, r, fresh, at, out)
		return
	}
	if name == runner {
		if found := workerDone.FindStringSubmatch(text); found != nil {
			if ended, ok := laneOf(s, found[1]); ok && ended.State != store.LaneRetired {
				retire(s, ended.Name, "worker-done", at)
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
		retire(s, name, "stand-down", at)
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
	switch {
	case kind == kindLanded:
		foldLanded(s, r, at)
	case kind == "done" && settles:
		retire(s, name, "done", at)
	case settles && (kind == "stopped" || kind == "stand-down" || (kind == "state" && stoppedLine.MatchString(text))):
		retire(s, name, "stand-down", at)
	case kind == "go" && name != Self && withdrawOrder.MatchString(text):
		foldWithdraw(s, r, at)
	case kind == "stand-down" && isRoot:
		foldRootStandDown(s, r, fresh, at, out)
	case kind == "decide" && isRoot:
		foldRootDecide(s, r, fresh, at, out)
	case isRoot:
		foldReplaces(s, r, fresh, at, out)
	}
}

func foldCarried(s *store.Store, r Record, at time.Time) bool {
	addressed := r.Addressed()
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
		if owned, ok := laneOf(s, owner); ok && isLive(owned.State) && len(owned.PRs) == 0 {
			do(s.LaneHeard(owner, at, kindLanded, 0))
		}
	}
}

func dropQueuedLaunch(s *store.Store, stood store.Lane, at time.Time, out *folded) {
	queued := stood
	if stood.State == store.LaneRetired {
		heir, found := laneOf(s, stood.RetiredBy)
		if !found || heir.Predecessor != stood.Name {
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

func foldRootStandDown(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	successor := r.Attr("successor")
	named := map[string]bool{}
	for _, listed := range standDownList.FindAllStringSubmatch(r.Text, -1) {
		for _, word := range laneWord.FindAllString(listed[1], -1) {
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
	delete(named, successor)
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
		if successor == "" && !fresh {
			dropQueuedLaunch(s, gone, at, out)
		}
		if gone.State != store.LaneRetired {
			retire(s, old, or(successor, Root), at)
			if successor != "" {
				must(s.WantLane(store.Lane{Name: successor, Brief: gone.Brief, Model: gone.Model, Effort: gone.Effort, Role: gone.Role, State: store.LaneLaunching, Handoff: gone.Handoff, PRs: gone.PRs, Predecessor: old}, at))
				continue
			}
		}
		if successor == "" {
			do(s.Own(Root, gone.PRs))
		}
	}
}

func foldRootDecide(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	if found := ownerAttr.FindStringSubmatch(r.Text); found != nil {
		numbers := r.PRs()
		do(s.Own(found[1], numbers))
		if !fresh {
			if line := deadOwner(s, found[1], numbers, at); line != "" {
				out.dead = append(out.dead, line)
			}
		}
		return
	}
	if moves := ownerClause.FindAllStringSubmatch(r.Text, -1); len(moves) > 0 {
		for _, move := range moves {
			number, err := strconv.Atoi(move[1])
			if err != nil {
				continue
			}
			target := or(move[2], move[3])
			if _, found := laneOf(s, target); target == Root || found {
				do(s.Own(target, []int{number}))
			}
		}
		return
	}
	foldReplaces(s, r, fresh, at, out)
}

func foldReplaces(s *store.Store, r Record, fresh bool, at time.Time, out *folded) {
	found := replaces.FindStringSubmatch(r.Text)
	if found == nil {
		return
	}
	old, known := laneOf(s, found[2])
	if !known || !isLive(old.State) {
		return
	}
	taker, known := laneOf(s, found[1])
	if !known {
		return
	}
	if !fresh {
		if why := unreadBy(s, r, old.Name, citedIn(r)); why != "" {
			out.unread = append(out.unread, unread{old.Name, why})
			hold(s, old.Name, why, at)
			return
		}
	}
	retire(s, old.Name, taker.Name, at)
	do(s.Own(taker.Name, old.PRs))
}
