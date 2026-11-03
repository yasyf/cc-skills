package lanes

import (
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Post struct {
	Kind      string
	To        []string
	Text      string
	Re        int64
	Successor string
	LaneRef   string
	PRs       []int
	Attrs     map[string]string
}

type Deps struct {
	Drive        string
	Checkout     string
	Briefs       string
	HandoffBrief string
	Records      func(since int64) ([]Record, error)
	Who          func(names []string) ([]Row, error)
	Tokens       func(Row) int
	Transcript   func(Row) string
	Post         func(Post) error
	Launch       func(name, model, effort, brief string) error
	Withdraw     func(name string) error
	Wake         func(name string, row Row, page map[string]any) (bool, error)
	PRStates     func(prs []int) (map[int]string, error)
	Held         func() (map[int]bool, error)
	RotateAt     int
}

type Relaunch struct {
	Old string
	New string
	Why string
}

type Outcome struct {
	Relaunched []Relaunch
	Asked      []string
	Held       []string
	Adopted    []string
	Withdrawn  []string
	Woken      []string
	Reassign   []string
}

func Tick(s *store.Store, d Deps, now time.Time, generation int64) (out Outcome, err error) {
	defer recovered(&err)
	now = now.UTC().Truncate(time.Second)
	cursor := must(s.Cursor(CursorName))
	fresh := cursor == 0
	var named []string
	if !fresh {
		desired := must(s.Lanes(live...))
		named = make([]string, 0, len(desired))
		for _, lane := range desired {
			named = append(named, lane.Name)
		}
	}
	rows, err := d.Who(named)
	if err != nil {
		return out, err
	}
	found := Peers(rows)
	records, err := d.Records(cursor)
	if err != nil {
		return out, err
	}
	var adoptable map[string]bool
	if fresh {
		adoptable = map[string]bool{}
		for name, peer := range found {
			if alive(&peer, peer.Seen, now) {
				adoptable[name] = true
			}
		}
	}
	said := fold(s, records, adoptable, d.Briefs)
	out.Adopted = said.adopted
	announce(s, d, said, now)
	probeOwners(s, d, found, now)
	for _, name := range said.hand {
		do(d.Withdraw(name))
		out.Withdrawn = append(out.Withdrawn, name)
	}
	if len(records) > 0 {
		var last int64
		for _, r := range records {
			last = max(last, r.Seq)
		}
		do(s.Advance(CursorName, last))
	}
	taken := map[string]bool{}
	for _, lane := range must(s.AllLanes()) {
		taken[lane.Name] = true
	}
	for name := range found {
		taken[name] = true
	}
	for _, lane := range must(s.Lanes(live...)) {
		act(s, d, lane, found, taken, generation, now, &out)
	}
	unownedDuties(s, d, found, now)
	hourly(s, d, now)
	return out, nil
}

func announce(s *store.Store, d Deps, said folded, now time.Time) {
	for _, one := range said.stood {
		how := ", its PRs to root"
		if one.successor != "" {
			how = " for " + one.successor
		}
		do(d.Post(Post{Kind: "stand-down", To: []string{one.name}, Text: fmt.Sprintf("%s %s stood down%s by record #%d.", pacific(now), one.name, how, one.seq), Successor: one.successor, Re: one.seq, PRs: one.prs}))
	}
	for _, line := range said.dead {
		name, _, _ := strings.Cut(line, " ")
		escalate(s, d, "lane:"+name, line, now)
	}
	verbs := slices.Sorted(slices.Values(Verbs))
	for _, seq := range said.untyped {
		do(d.Post(Post{Kind: "defect", To: []string{Root}, Text: fmt.Sprintf("%s root's decide #%d reaches the supervisor with no registry request, so the registry is unchanged and its lanes are still woken while their tasks read in progress; post `lr request <%s> <lane>` for each lane it names.", pacific(now), seq, strings.Join(verbs, "|")), Re: seq}))
	}
	for _, one := range said.unread {
		do(d.Post(Post{Kind: "defect", To: []string{Root, one.name}, LaneRef: one.name, Text: fmt.Sprintf("%s %s, so the supervisor refused it and holds %s with its PRs, never relaunched or retired, until %s posts again; %s, keep working. Root: read %s with `cci tail --lane %s --since <its wake seq> --oldest` and the registry's heard time, then post it again citing last-read=<seq>.", pacific(now), one.why, one.name, one.name, one.name, one.name, one.name)}))
	}
}

func act(s *store.Store, d Deps, lane store.Lane, found map[string]Peer, taken map[string]bool, generation int64, now time.Time, out *Outcome) {
	var peer *Peer
	if known, ok := found[lane.Name]; ok {
		peer = &known
	}
	last := lane.Heard
	if last.IsZero() {
		last = lane.Since
	}
	silent := now.Sub(last)
	tokens := 0
	if lane.State == store.LaneDesired && lane.Asked.IsZero() && peer != nil && peer.Live && silent < StaleAfter && !slices.Contains(settled, lane.Last) {
		tokens = d.Tokens(peer.Row)
	}
	verdict := Classify(lane, peer, now, tokens, d.RotateAt)
	if peer != nil && (verdict.Kind == CameUp || (lane.State == store.LaneDesired && (verdict.Kind == Ok || verdict.Kind == AskRotate))) {
		do(s.Heartbeat(store.Worker{Name: lane.Name, Generation: generation, Heartbeat: newer(peer.Seen, lane.Heard)}))
	}
	if verdict.Kind == Ok && lane.State == store.LaneDesired && lane.Waiting == 0 && peer != nil && peer.Row.Roster != "" && silent >= StaleAfter {
		wake(s, d, lane, *peer, now, out)
	}
	switch verdict.Kind {
	case CameUp:
		do(s.SetLaneState(lane.Name, store.LaneDesired, now, "", ""))
		if lane.Predecessor != "" && lane.Reason != "" {
			if old, ok := laneOf(s, lane.Predecessor); ok && old.RetiredBy == lane.Name {
				do(d.Post(Post{Kind: "stand-down", To: []string{old.Name}, Text: fmt.Sprintf("%s %s stood down for %s, now up; %s %s.", pacific(now), old.Name, lane.Name, old.Name, lane.Reason), Successor: lane.Name, PRs: lane.PRs}))
			}
		}
	case NoShow:
		hold(s, lane.Name, verdict.Why, now)
		escalate(s, d, "lane:"+lane.Name, fmt.Sprintf("%s; brief %s", verdict.Why, filepath.Join(d.Briefs, lane.Name+".brief.md")), now)
		out.Held = append(out.Held, lane.Name)
	case Unproven:
		hold(s, lane.Name, verdict.Why, now)
		escalate(s, d, "lane:"+lane.Name, fmt.Sprintf("held, not relaunched: it %s; brief %s", verdict.Why, lane.Brief), now)
		out.Held = append(out.Held, lane.Name)
	case StoodDown:
		retire(s, lane.Name, "stand-down", now)
	case Dead:
		if lane.Last == kindLanded {
			retire(s, lane.Name, kindLanded, now)
			return
		}
		relaunch(s, d, lane, found, verdict.Why, taken, now, out)
	case AskRotate:
		do(d.Post(Post{Kind: "ask", To: []string{lane.Name}, Text: fmt.Sprintf("ROTATE: %s is at %s. Write anything not yet in the ledger or cc-notes to a cc-notes doc, post `cci post --lane %s --kind handoff --ccn <doc id> --re <this record>`, then keep working.", lane.Name, verdict.Why, lane.Name)}))
		do(s.SetLaneAsked(lane.Name, now))
		out.Asked = append(out.Asked, lane.Name)
	}
}

func escalate(s *store.Store, d Deps, subject, cause string, now time.Time) {
	if must(s.Escalate(subject, cause, Root, now)) {
		do(d.Post(Post{Kind: "blocker", To: []string{Root}, Text: fmt.Sprintf("%s %s: %s", pacific(now), subject, cause), LaneRef: strings.TrimPrefix(subject, "lane:")}))
	}
}

func relaunch(s *store.Store, d Deps, old store.Lane, found map[string]Peer, why string, taken map[string]bool, now time.Time, out *Outcome) {
	for _, name := range slices.Sorted(maps.Keys(found)) {
		peer := found[name]
		if alive(&peer, peer.Seen, now) && succeeds(name, old.Name) {
			retire(s, old.Name, name, now)
			return
		}
	}
	strikes := 0
	if old.Predecessor != "" && now.Sub(old.Since) < strikeWindow {
		strikes = old.Strikes + 1
	}
	hasBrief := briefExists(old.Brief)
	if strikes >= strikeLimit || !hasBrief || strings.Contains(old.Reason, unreadMark) {
		hold(s, old.Name, why, now)
		var cause string
		switch {
		case strikes >= strikeLimit:
			cause = fmt.Sprintf("its successors died within %d minutes of launch %d times", int(strikeWindow.Minutes()), strikeLimit)
		case !hasBrief:
			cause = fmt.Sprintf("its brief %s is gone", old.Brief)
		default:
			cause = old.Reason + ", so the supervisor refused it, and the lane has since stopped; post the stand-down again citing last-read=<seq>, or relaunch it by hand"
		}
		escalate(s, d, "lane:"+old.Name, fmt.Sprintf("held, not relaunched: %s; it %s", cause, why), now)
		out.Held = append(out.Held, old.Name)
		return
	}
	name := successorName(old.Name, taken)
	taken[name] = true
	path := filepath.Join(d.Briefs, name+".brief.md")
	do(os.MkdirAll(d.Briefs, 0o750))
	transcript := ""
	if peer, ok := found[old.Name]; ok {
		transcript = d.Transcript(peer.Row)
	}
	do(os.WriteFile(path, []byte(must(successorBrief(old, name, why, d, transcript))), 0o600))
	retire(s, old.Name, name, now)
	must(s.WantLane(store.Lane{Name: name, Brief: old.Brief, Model: old.Model, Effort: old.Effort, Role: old.Role, State: store.LaneLaunching, Handoff: old.Handoff, PRs: old.PRs, Predecessor: old.Name, Reason: why, Strikes: strikes}, now))
	do(d.Launch(name, old.Model, old.Effort, path))
	do(d.Post(Post{Kind: "report", To: []string{Root}, LaneRef: name, Text: fmt.Sprintf("%s RELAUNCHED %s as %s: it %s; handoff %s; PRs %s; brief %s", pacific(now), old.Name, name, why, or(old.Handoff, "none, the successor writes it"), or(numberList(old.PRs), "none"), path)}))
	out.Relaunched = append(out.Relaunched, Relaunch{old.Name, name, why})
}

func successorBrief(old store.Lane, name, why string, d Deps, transcript string) (string, error) {
	body, err := os.ReadFile(old.Brief)
	if err != nil {
		return "", err
	}
	drive, checkout := d.Drive, d.Checkout
	start := fmt.Sprintf("Your handoff is `ccn -R %s doc show %s`; read it first.", checkout, old.Handoff)
	if old.Handoff == "" {
		start = fmt.Sprintf("%s left no handoff. Before anything else, spawn one `long-running:lane` subagent, model sonnet, effort xhigh, named `%s-handoff`, from the spawn brief in %s, with transcript %s, reference brief %s, cci drive %s, previous handoff none and drive checkout %s; read the doc it returns.", old.Name, old.Name, d.HandoffBrief, or(transcript, "unknown: find it with cc-transcript list"), old.Brief, drive, checkout)
	}
	owned := "none recorded"
	if len(old.PRs) > 0 {
		owned = strings.ReplaceAll(numberList(old.PRs), " ", ", ")
	}
	step := "all"
	if found := sliceStep.FindSubmatch(body); found != nil {
		step = string(found[1])
	}
	return strings.Join([]string{
		"Replaces: " + old.Name,
		"ccx: place=local",
		fmt.Sprintf("Register slice: lane=%s step=%s", name, step),
		fmt.Sprintf("You are %s and take over %s's work. The lane supervisor launched you because %s %s; %s is stood down on cci, so never SendMessage it.", name, old.Name, old.Name, why, old.Name),
		start,
		fmt.Sprintf("PRs you now own: %s.", owned),
		fmt.Sprintf("If the handoff and %s's records show its deliverable is already complete, post `cci post --drive %s --lane %s --kind done --to root --text '<what it delivered>'` and stop.", old.Name, drive, name),
		"",
		"Before any write (an edit, commit, push, ship, PR comment, or a record that claims work):",
		fmt.Sprintf("  1. From %s, run `drive.py lane-status %s`. Each line is a worktree %s held, with its branch, dirty files, push state, and PR.", checkout, old.Name, old.Name),
		fmt.Sprintf("  2. From %s, never from inside one of those worktrees, run for each worktree path it printed:", checkout),
		`     lsof -n -d cwd 2>/dev/null | awk -v w='<worktree>' '$NF == w || index($NF, w "/") == 1'`,
		fmt.Sprintf("  3. If any line printed, write nothing: post `cci post --drive %s --lane %s --kind blocker --to root --text \"LIVE %s: <worktree> pids <pids>\"` and stop. Never stop or signal those processes.", drive, name, old.Name),
		fmt.Sprintf("  4. With no live process in any worktree, continue in %s's worktree and branch from the state lane-status shows.", old.Name),
		"",
		fmt.Sprintf("When the deliverable is done, post `cci post --drive %s --lane %s --kind done --to root` before your final message; told to stand down after a task, post that `done` when the task is finished. On `ROTATE`, write your state to cc-notes and post `cci post --drive %s --lane %s --kind handoff --ccn <doc id> --re <the ROTATE record>`, then keep working.", drive, name, drive, name),
		"",
		string(body),
	}, "\n"), nil
}

func truncated(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit])
}

func wake(s *store.Store, d Deps, lane store.Lane, peer Peer, now time.Time, out *Outcome) {
	if slices.Contains(settled, lane.Last) {
		return
	}
	page := lane.Page
	prs := pagePRs(page)
	if len(prs) > 0 && !intersect(prs, lane.PRs) {
		do(s.SetLanePage(lane.Name, nil))
		return
	}
	woke := pageText(page, "woke")
	if woke == "" {
		if must(d.Wake(lane.Name, peer.Row, page)) {
			out.Woken = append(out.Woken, lane.Name)
			if page != nil {
				do(s.SetLanePage(lane.Name, with(page, "woke", store.Stamp(now))))
			}
		}
		return
	}
	wokeAt := must(store.Parse(woke))
	if pageText(page, "asked") != "" || now.Sub(wokeAt) < PageAnswer {
		return
	}
	owned := or(numberList(prs), lane.Name+"'s work")
	do(d.Post(Post{Kind: "ask", To: []string{Root}, LaneRef: lane.Name, PRs: prs, Text: fmt.Sprintf("%s reassign %s: %s has posted nothing since it was woken at %s because %s. Post `<lane> replaces %s` or a decide naming owner=<lane>; it is not woken again for that blocker.", pacific(now), owned, lane.Name, pacific(wokeAt), truncated(pageText(page, "why"), 240), lane.Name)}))
	do(s.SetLanePage(lane.Name, with(page, "asked", store.Stamp(now))))
	out.Reassign = append(out.Reassign, lane.Name)
}

func with(page map[string]any, key, value string) map[string]any {
	changed := maps.Clone(page)
	changed[key] = value
	return changed
}

func probeOwners(s *store.Store, d Deps, found map[string]Peer, now time.Time) {
	heard, probes := must(s.Heard()), must(s.Probes())
	for name, probe := range probes {
		if at, ok := heard[name]; ok && at.After(probe.Asked) {
			do(s.Unprobe(name))
			delete(probes, name)
		}
	}
	holds, err := d.Held()
	if errors.Is(err, ErrUnread) {
		return
	}
	do(err)
	retired := map[string]bool{}
	for _, lane := range must(s.Lanes(store.LaneRetired)) {
		retired[lane.Name] = true
	}
	var unowned, wanted []int
	quiet := map[string][]int{}
	for name, prs := range must(s.Owners()) {
		if retired[name] {
			continue
		}
		var open []int
		for _, n := range prs {
			if !holds[n] {
				open = append(open, n)
			}
		}
		if name == Root {
			unowned = open
			wanted = append(wanted, open...)
			continue
		}
		if since, ok := heard[name]; !ok || now.Sub(since) >= liveWithin {
			quiet[name] = open
			wanted = append(wanted, open...)
		}
	}
	slices.Sort(wanted)
	states, err := d.PRStates(slices.Compact(wanted))
	if errors.Is(err, ErrUnread) {
		return
	}
	do(err)
	strandedFresh := []int{}
	for _, n := range unowned {
		key := fmt.Sprintf("%s:%d", Root, n)
		if states[n] != NeedsWork {
			do(s.Unprobe(key))
			continue
		}
		if _, asked := probes[key]; !asked {
			strandedFresh = append(strandedFresh, n)
		}
	}
	if len(strandedFresh) > 0 {
		verb, pronoun := "need", "them"
		if len(strandedFresh) == 1 {
			verb, pronoun = "needs", "it"
		}
		do(d.Post(Post{Kind: "blocker", To: []string{Root}, Text: fmt.Sprintf("%s %s %s work and no lane owns %s: root stood down the lane that did, naming no successor. Name one with a root decide carrying `owner=<lane>`.", pacific(now), numberList(strandedFresh), verb, pronoun)}))
		for _, n := range strandedFresh {
			do(s.Probe(fmt.Sprintf("%s:%d", Root, n), now))
		}
	}
	var lines []string
	for _, name := range slices.Sorted(maps.Keys(quiet)) {
		var work []int
		for _, n := range quiet[name] {
			if states[n] == NeedsWork {
				work = append(work, n)
			}
		}
		if len(work) == 0 {
			continue
		}
		owned := numberList(work)
		probe, asked := probes[name]
		switch {
		case !asked:
			since := "you were named"
			if at, ok := heard[name]; ok {
				since = pacific(at)
			}
			text := fmt.Sprintf("%s %s: you own %s, which still need work, and have posted nothing since %s. Post any record within %d minutes, such as your state, or root is told you are gone.", pacific(now), name, owned, since, int(StaleAfter.Minutes()))
			do(d.Post(Post{Kind: "ask", To: []string{name}, Text: text}))
			do(s.Probe(name, now))
			if peer, ok := found[name]; ok && peer.Row.Roster != "" {
				must(d.Wake(name, peer.Row, map[string]any{"topic": "probe:" + name, "why": text, "text": text, "prs": work}))
			}
		case probe.Dead.IsZero() && now.Sub(probe.Asked) >= StaleAfter:
			do(s.ProbeDead(name, now))
			lines = append(lines, fmt.Sprintf("%s (%s; asked at %s; %s)", name, owned, pacific(probe.Asked), successorNote(s, name)))
		}
	}
	if len(lines) > 0 {
		noun := "lane"
		if len(lines) > 1 {
			noun = "lanes"
		}
		do(d.Post(Post{Kind: "blocker", To: []string{Root}, Text: fmt.Sprintf("%s %d %s own PRs that need work and left the supervisor's direct ask unanswered for %d minutes: %s", pacific(now), len(lines), noun, int(StaleAfter.Minutes()), strings.Join(lines, "; "))}))
	}
}

func unownedDuties(s *store.Store, d Deps, found map[string]Peer, now time.Time) {
	heard := must(s.Heard())
	heads := map[string]store.Lane{}
	for _, lane := range must(s.AllLanes()) {
		if must(BriefOf(lane.Brief)).Standing == "" {
			continue
		}
		if head, ok := newest(s, lane.Name); ok {
			heads[head.Name] = head
		}
	}
	for _, name := range slices.Sorted(maps.Keys(heads)) {
		head := heads[name]
		var since time.Time
		var why string
		switch head.State {
		case store.LaneHeld, store.LaneLaunching:
			continue
		case store.LaneDesired:
			var peer *Peer
			last := head.Heard
			if known, ok := found[name]; ok {
				peer = &known
				last = newer(known.Seen, head.Heard)
			}
			if alive(peer, last, now) {
				continue
			}
			since, why = head.Since, "is neither live nor heard since its launch"
			if !last.IsZero() {
				since, why = last, "is neither live nor heard since "+pacific(last)
			}
		default:
			if at, ok := heard[head.RetiredBy]; ok && succeeds(head.RetiredBy, name) && now.Sub(at) < StaleAfter {
				continue
			}
			since, why = head.Since, fmt.Sprintf("was retired by %s at %s with no successor", or(head.RetiredBy, "nobody"), pacific(head.Since))
		}
		if now.Sub(since) >= unownedAfter {
			escalate(s, d, "lane:"+name, fmt.Sprintf("standing lane %s (%s) has had no live owner since %s: it %s. Launch its successor from %s, or post `lr request stand-down %s --successor <lane>`; deleting the brief's `Standing:` line ends the duty.", name, must(BriefOf(head.Brief)).Standing, pacific(since), why, head.Brief, name), now)
		}
	}
}

func hourly(s *store.Store, d Deps, now time.Time) {
	hour := now.Unix() / 3600
	last := must(s.Cursor(hourCursor))
	do(s.Advance(hourCursor, hour))
	if last == 0 || hour <= last {
		return
	}
	start := time.Unix((hour-1)*3600, 0).UTC()
	end := start.Add(time.Hour)
	var words []string
	for _, lane := range must(s.AllLanes()) {
		if lane.Predecessor != "" && lane.Reason != "" && !lane.Since.Before(start) && lane.Since.Before(end) {
			words = append(words, fmt.Sprintf("%s %s, so %s took over", lane.Predecessor, lane.Reason, lane.Name))
		}
	}
	if len(words) == 0 {
		return
	}
	noun := "lane"
	if len(words) > 1 {
		noun = "lanes"
	}
	do(d.Post(Post{Kind: "report", To: []string{Root}, Attrs: map[string]string{"audience": "owner"}, Text: fmt.Sprintf("Between %s and %s the lane supervisor restarted %d %s with no root action: %s.", pacific(start), pacific(end), len(words), noun, strings.Join(words, "; "))}))
}
