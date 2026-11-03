package lanes

import (
	"cmp"
	"crypto/sha256"
	"errors"
	"fmt"
	"log/slog"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
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
	Parent       string
	Checkout     string
	State        string
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
	Desks        func() (map[string]string, error)
	OpenWork     func() ([]Record, error)
	Owed         func() ([]deletions.Owed, error)
	Present      func(rows []deletions.Owed) ([]deletions.Owed, error)
	Gaps         func() ([]string, error)
	Orders       func(s *store.Store) ([]owner.Row, error)
	RotateAt     int
	RetireAfter  time.Duration
	OpenBranches func() (map[string][]int, error)
	Hosts        func(lanes []string) map[string]Host
}

type Relaunch struct {
	Old string
	New string
	Why string
}

type Retired struct {
	Lane string
	To   string
	Why  string
}

type Outcome struct {
	Relaunched []Relaunch
	Retired    []Retired
	Asked      []string
	Held       []string
	Adopted    []string
	Withdrawn  []string
	Woken      []string
}

func Tick(s *store.Store, d Deps, now time.Time, generation int64) (out Outcome, err error) {
	defer recovered(&err)
	now = now.UTC().Truncate(time.Second)
	d.Desks = sync.OnceValues(d.Desks)
	owed := sync.OnceValues(d.Owed)
	d.Owed = func() ([]deletions.Owed, error) {
		rows, err := owed()
		return slices.Clone(rows), err
	}
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
	for i := range rows {
		rows[i].Lane = Canonical(d.Drive, rows[i].Lane)
	}
	found := Peers(rows)
	records, err := d.Records(cursor)
	if err != nil {
		return out, err
	}
	for i := range records {
		records[i].Lane = Canonical(d.Drive, records[i].Lane)
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
	said := fold(s, records, adoptable, d.Briefs, d)
	out.Adopted = append(said.adopted, adoptStanding(s, d, found, now)...)
	announce(s, d, said)
	for _, name := range said.unheard {
		raiseUnheard(s, d, name, now)
	}
	ownedWork(s, d, now)
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
	desired := must(s.Lanes(live...))
	proven := proofs(s, d, desired, found, now)
	for _, lane := range desired {
		act(s, d, lane, found, proven, taken, generation, now, &out)
	}
	retiredTo := map[string][]string{}
	for _, one := range out.Retired {
		retiredTo[one.To] = append(retiredTo[one.To], fmt.Sprintf("%s (it %s)", one.Lane, one.Why))
	}
	for _, to := range slices.Sorted(maps.Keys(retiredTo)) {
		do(d.Post(Post{Kind: "report", To: []string{to}, Text: fmt.Sprintf("RETIRED for owning no PR, holding no standing duty, and posting nothing for %d minutes or more: %s.", int(d.RetireAfter.Minutes()), strings.Join(retiredTo[to], "; "))}))
	}
	claimBranches(s, d, now)
	unownedDuties(s, d, found, now)
	hourly(s, d, now)
	woken, err := chaseUnread(s, d, found, now)
	if err != nil {
		slog.Warn("unread orders not chased this tick", "err", err)
	}
	out.Woken = append(out.Woken, woken...)
	return out, nil
}

func adoptStanding(s *store.Store, d Deps, found map[string]Peer, now time.Time) []string {
	var adopted []string
	for _, name := range slices.Sorted(maps.Keys(standingSessions)) {
		session := sessionFor(name, d.Parent)
		brief := filepath.Join(d.State, session.lane, "brief.md")
		peer, listed := found[session.lane]
		if _, known := laneOf(s, session.lane); known || !listed || !alive(&peer, peer.Seen, now) || !briefExists(brief) {
			continue
		}
		must(s.WantLane(store.Lane{Name: session.lane, Brief: brief, Model: session.model, Effort: session.effort, Role: or(session.role, "lane"), State: store.LaneDesired}, now))
		adopted = append(adopted, session.lane)
	}
	return adopted
}

func announce(s *store.Store, d Deps, said folded) {
	for _, one := range said.stood {
		how := ", its PRs to the coordinator"
		if one.successor != "" {
			how = " for " + one.successor
		}
		do(d.Post(Post{Kind: "stand-down", To: []string{one.name}, Text: fmt.Sprintf("%s stood down%s by record #%d.", one.name, how, one.seq), Successor: one.successor, Re: one.seq, PRs: one.prs}))
	}
	verbs := slices.Sorted(slices.Values(Verbs))
	for _, seq := range said.untyped {
		do(d.Post(Post{Kind: "defect", To: []string{Root}, Text: fmt.Sprintf("The coordinator's decide #%d carries a verb but is no registry request, so the registry is unchanged; post `%s` instead.", seq, Request{Verb: "<" + strings.Join(verbs, "|") + ">", Lane: "<lane>"}.Command()), Re: seq}))
	}
	for _, one := range said.unread {
		do(d.Post(Post{Kind: "defect", To: []string{noticeTo(s, one.name), one.name}, LaneRef: one.name, Text: fmt.Sprintf("%s, so the supervisor refused it and holds %s with its PRs, never relaunched or retired, until %s posts again; %s, keep working. Coordinator: read %s with `cci tail --lane %s --since <its wake seq> --oldest` and the registry's heard time, then post it again citing last-read=<seq>.", one.why, one.name, one.name, one.name, one.name, one.name)}))
	}
	for _, one := range said.owing {
		one.post(s, d)
	}
}

func act(s *store.Store, d Deps, lane store.Lane, found map[string]Peer, proven map[string]Verdict, taken map[string]bool, generation int64, now time.Time, out *Outcome) {
	peer := peerOf(found, lane.Name)
	silent := silence(lane, now)
	tokens := 0
	if lane.State == store.LaneDesired && lane.Asked.IsZero() && peer != nil && peer.Live && silent < StaleAfter && !settledLane(lane) {
		tokens = d.Tokens(peer.Row)
	}
	verdict := Classify(lane, peer, now, tokens, d.RotateAt)
	if peer != nil && (verdict.Kind == CameUp || (lane.State == store.LaneDesired && (verdict.Kind == Ok || verdict.Kind == AskRotate))) {
		do(s.Heartbeat(store.Worker{Name: lane.Name, Generation: generation, Heartbeat: newer(peer.Seen, lane.Heard)}))
	}
	if verdict.Kind == Ok && lane.State == store.LaneDesired && lane.Waiting == 0 && lane.Page != nil && peer != nil && (peer.Row.Roster != "" || peer.Row.Dispatch != "") {
		wake(s, d, lane, *peer, found, taken, now, out)
	}
	if verdict.Kind == Ok && lane.State == store.LaneDesired && lane.Waiting == 0 && lane.Page == nil && peer != nil && peer.Row.Idle && holdsNothing(lane) && silent >= d.RetireAfter {
		retireSilent(s, d, lane, "is an idle teammate on "+peer.Row.Roster+" with no turn running", now, out)
		return
	}
	switch verdict.Kind {
	case CameUp:
		do(s.SetLaneState(lane.Name, store.LaneDesired, now, "", ""))
		if lane.Predecessor != "" && lane.Reason != "" {
			if old, ok := laneOf(s, lane.Predecessor); ok && old.RetiredBy == lane.Name {
				do(d.Post(Post{Kind: "stand-down", To: []string{old.Name}, Text: fmt.Sprintf("%s stood down for %s, now up; %s %s.", old.Name, lane.Name, old.Name, lane.Reason), Successor: lane.Name, PRs: lane.PRs}))
			}
		}
	case NoShow:
		hold(s, lane.Name, verdict.Why, now)
		escalate(s, d, "lane:"+lane.Name, fmt.Sprintf("%s; brief %s", verdict.Why, filepath.Join(d.Briefs, lane.Name+".brief.md")), now)
		out.Held = append(out.Held, lane.Name)
	case Unproven:
		if !needsProof(lane, silent, d.RetireAfter) {
			return
		}
		proof, settled := proven[lane.Name]
		if !settled {
			hold(s, lane.Name, verdict.Why, now)
			escalateTo(s, d, "lane:"+lane.Name, fmt.Sprintf("held, not relaunched or retired: it %s, and it runs on no Sprite whose probe answered; brief %s", verdict.Why, lane.Brief), residueTo(s, d, lane.Name), now)
			out.Held = append(out.Held, lane.Name)
			return
		}
		if proof.Kind == Dead {
			died(s, d, lane, peer, found, proof, silent, taken, now, out)
		}
	case StoodDown:
		if !refusedOwing(s, d, lane.Name, now) {
			retire(s, lane.Name, "stand-down", now)
		}
	case Dead:
		died(s, d, lane, peer, found, verdict, silent, taken, now, out)
	case AskRotate:
		do(d.Post(Post{Kind: "ask", To: []string{lane.Name}, Text: fmt.Sprintf("ROTATE: %s is at %s. Write anything not yet in the ledger or cc-notes to a cc-notes doc, post `cci post --lane %s --kind handoff --ccn <doc id> --re <this record>`, then keep working.", lane.Name, verdict.Why, lane.Name)}))
		do(s.SetLaneAsked(lane.Name, now))
		out.Asked = append(out.Asked, lane.Name)
	}
}

func died(s *store.Store, d Deps, lane store.Lane, peer *Peer, found map[string]Peer, verdict Verdict, silent time.Duration, taken map[string]bool, now time.Time, out *Outcome) {
	if lane.Last == kindLanded {
		if !refusedOwing(s, d, lane.Name, now) {
			retire(s, lane.Name, kindLanded, now)
		}
		return
	}
	if peer != nil && peer.Row.Succeeded && lane.Handoff == "" && lane.Asked.IsZero() {
		if lane.Heard.IsZero() {
			hold(s, lane.Name, unheardDone, now)
			raiseUnheard(s, d, lane.Name, now)
			out.Held = append(out.Held, lane.Name)
			return
		}
		if !refusedOwing(s, d, lane.Name, now) {
			retire(s, lane.Name, retiredWorkerDone, now)
		}
		return
	}
	if !holdsNothing(lane) {
		relaunch(s, d, lane, found, verdict.Why, taken, now, out)
		return
	}
	if silent >= d.RetireAfter {
		retireSilent(s, d, lane, verdict.Why, now, out)
	}
}

func raiseUnheard(s *store.Store, d Deps, name string, now time.Time) {
	cause := fmt.Sprintf("held, not retired: it %s, so nothing proves its work landed; read its Sprite or worktree, then post `%s` or relaunch it", unheardDone, Request{Verb: "done", Lane: name}.Command())
	if must(s.Escalate("lane:"+name, cause, Root, now)) {
		do(d.Post(Post{Kind: "blocker", To: []string{Root}, LaneRef: name, Text: "lane:" + name + ": " + cause}))
	}
}

func branchProbe(lane string) string {
	return "branches:" + lane
}

func onBranch(branch string, names ...string) bool {
	return slices.ContainsFunc(names, func(name string) bool { return branch == name || strings.HasPrefix(branch, name+"/") })
}

func claimBranches(s *store.Store, d Deps, now time.Time) {
	probes := must(s.Probes())
	var unchecked []string
	for _, lane := range must(s.Lanes(store.LaneRetired)) {
		if _, checked := probes[branchProbe(lane.Name)]; !checked {
			unchecked = append(unchecked, lane.Name)
		}
	}
	if len(unchecked) == 0 {
		return
	}
	branches, err := d.OpenBranches()
	if errors.Is(err, ErrUnread) {
		return
	}
	do(err)
	owned := map[int]bool{}
	for _, prs := range must(s.Owners()) {
		for _, n := range prs {
			owned[n] = true
		}
	}
	var orphans []int
	var left []string
	names := slices.Sorted(maps.Keys(branches))
	for _, name := range unchecked {
		base, _ := family(name)
		for _, branch := range names {
			if !onBranch(branch, base, name) {
				continue
			}
			for _, n := range branches[branch] {
				if !owned[n] {
					owned[n] = true
					orphans = append(orphans, n)
					left = append(left, fmt.Sprintf("%s left #%d on %s", name, n, branch))
				}
			}
		}
	}
	if len(orphans) > 0 {
		do(s.Own(Root, orphans))
		do(d.Post(Post{Kind: "report", To: []string{Root}, PRs: orphans, Text: fmt.Sprintf("ORPHANED PRs now owned by the coordinator, since no lane owned them when their lanes retired: %s. Name an owner with `%s`.", strings.Join(left, "; "), Request{Verb: "owner", Lane: "<lane>", PRs: orphans}.Command())}))
	}
	for _, name := range unchecked {
		do(s.Probe(branchProbe(name), now))
	}
}

func holdsNothing(lane store.Lane) bool {
	return lane.Name != Root && len(lane.PRs) == 0 && !isStanding(lane)
}

func settledLane(lane store.Lane) bool {
	return slices.Contains(settled, lane.Last) && (lane.Last != "done" || !isStanding(lane))
}

func isStanding(lane store.Lane) bool {
	return slices.Contains(standingRoles, lane.Role) || must(BriefOf(lane.Brief)).Standing != ""
}

func retireSilent(s *store.Store, d Deps, lane store.Lane, why string, now time.Time, out *Outcome) {
	if refusedOwing(s, d, lane.Name, now) {
		return
	}
	out.Retired = append(out.Retired, Retired{lane.Name, owningDesk(s, d, lane.Name), why})
	retire(s, lane.Name, retiredSilent, now)
}

func (d Deps) coordinatorReportsTo() string {
	if d.Parent != "" {
		return Root + "@" + d.Parent
	}
	return OwnerRelay
}

func escalate(s *store.Store, d Deps, subject, cause string, now time.Time) {
	lane := strings.TrimPrefix(subject, "lane:")
	to := owningDesk(s, d, lane)
	if lane == Root {
		to = d.coordinatorReportsTo()
	}
	escalateTo(s, d, subject, cause, to, now)
}

func escalateTo(s *store.Store, d Deps, subject, cause, to string, now time.Time) {
	if must(s.Escalate(subject, cause, to, now)) {
		do(d.Post(Post{Kind: "blocker", To: []string{to}, Text: fmt.Sprintf("%s: %s", subject, cause), LaneRef: strings.TrimPrefix(subject, "lane:")}))
	}
}

func owningDesk(s *store.Store, d Deps, lane string) string {
	desks := must(d.Desks())
	desk := desks[lane]
	row, known := laneOf(s, lane)
	for seen := map[string]bool{lane: true}; desk == "" && known && row.Predecessor != "" && !seen[row.Predecessor]; {
		seen[row.Predecessor] = true
		desk = desks[row.Predecessor]
		row, known = laneOf(s, row.Predecessor)
	}
	head, found := newest(s, desk)
	if !found || head.Name == lane || !isLive(head.State) {
		return noticeTo(s, lane)
	}
	return head.Name
}

func relaunchCoordinator(s *store.Store, d Deps, old store.Lane, found map[string]Peer, why string, now time.Time, out *Outcome) {
	diedFast := old.Reason != "" && now.Sub(old.Since) < strikeWindow
	if diedFast || !briefExists(old.Brief) {
		hold(s, Root, why, now)
		cause := fmt.Sprintf("its brief %s is gone", old.Brief)
		if diedFast {
			cause = fmt.Sprintf("it died within %d minutes of its relaunch", int(strikeWindow.Minutes()))
		}
		escalate(s, d, "lane:"+Root, fmt.Sprintf("held, not relaunched: %s; it %s", cause, why), now)
		out.Held = append(out.Held, Root)
		return
	}
	path := filepath.Join(d.Briefs, Root+".brief.md")
	do(os.MkdirAll(d.Briefs, 0o750))
	transcript := ""
	if peer, ok := found[Root]; ok {
		transcript = d.Transcript(peer.Row)
	}
	do(os.WriteFile(path, []byte(must(coordinatorBrief(old, why, d, transcript))), 0o600))
	do(s.SetLaneState(Root, store.LaneLaunching, now, "", why))
	must(s.ReleaseWorker(Root))
	do(d.Launch(Root, old.Model, old.Effort, path))
	do(d.Post(Post{Kind: "report", To: []string{d.coordinatorReportsTo()}, LaneRef: Root, Text: fmt.Sprintf("RELAUNCHED the coordinator: it %s; handoff %s; brief %s", why, or(old.Handoff, "none, the new session writes it"), path)}))
	out.Relaunched = append(out.Relaunched, Relaunch{Root, Root, why})
}

func handoffStart(old store.Lane, d Deps, transcript string) string {
	if old.Handoff != "" {
		return fmt.Sprintf("Your handoff is `ccn -R %s doc show %s`; read it first.", d.Checkout, old.Handoff)
	}
	return fmt.Sprintf("%s left no handoff. Before anything else, spawn one `long-running:lane` subagent, model sonnet, effort xhigh, named `%s-handoff`, from the spawn brief in %s, with transcript %s, reference brief %s, cci drive %s, previous handoff none and drive checkout %s; read the doc it returns.", old.Name, old.Name, d.HandoffBrief, or(transcript, "unknown: find it with cc-transcript list"), old.Brief, d.Drive, d.Checkout)
}

func coordinatorBrief(old store.Lane, why string, d Deps, transcript string) (string, error) {
	body, err := os.ReadFile(old.Brief)
	if err != nil {
		return "", err
	}
	return strings.Join([]string{
		"Replaces: " + Root,
		"ccx: place=local",
		fmt.Sprintf("You are %s, this drive's coordinator, in a new session. The lane supervisor launched you from your brief because the last coordinator session %s.", Root, why),
		handoffStart(old, d, transcript),
		"",
		string(body),
	}, "\n"), nil
}

func relaunch(s *store.Store, d Deps, old store.Lane, found map[string]Peer, why string, taken map[string]bool, now time.Time, out *Outcome) {
	if old.Name == Root {
		relaunchCoordinator(s, d, old, found, why, now, out)
		return
	}
	for _, name := range slices.Sorted(maps.Keys(found)) {
		peer := found[name]
		if alive(&peer, peer.Seen, now) && succeeds(name, old.Name) {
			retire(s, old.Name, name, now)
			return
		}
	}
	hasBrief := briefExists(old.Brief)
	if !hasBrief || strings.Contains(old.Reason, unreadMark) {
		hold(s, old.Name, why, now)
		cause := old.Reason + ", so the supervisor refused it, and the lane has since stopped; post the stand-down again citing last-read=<seq>, or relaunch it by hand"
		if !hasBrief {
			cause = fmt.Sprintf("its brief %s is gone", old.Brief)
		}
		escalate(s, d, "lane:"+old.Name, fmt.Sprintf("held, not relaunched: %s; it %s", cause, why), now)
		out.Held = append(out.Held, old.Name)
		return
	}
	if old.Predecessor != "" && old.Reason != "" {
		hold(s, old.Name, why, now)
		to := noticeTo(s, old.Name)
		cause := fmt.Sprintf("held, not relaunched: the supervisor already relaunched it once, when %s %s, and now it %s; brief %s", old.Predecessor, old.Reason, why, old.Brief)
		if must(s.Escalate("lane:"+old.Name, cause, to, now)) {
			do(d.Post(Post{Kind: "defect", To: []string{to}, LaneRef: old.Name, Text: fmt.Sprintf("lane:%s: %s", old.Name, cause)}))
		}
		out.Held = append(out.Held, old.Name)
		return
	}
	name := SuccessorName(old.Name, taken)
	taken[name] = true
	path := filepath.Join(d.Briefs, name+".brief.md")
	do(os.MkdirAll(d.Briefs, 0o750))
	transcript := ""
	if peer, ok := found[old.Name]; ok {
		transcript = d.Transcript(peer.Row)
	}
	do(os.WriteFile(path, []byte(must(successorBrief(old, name, why, d, transcript))), 0o600))
	retire(s, old.Name, name, now)
	must(s.WantLane(store.Lane{Name: name, Brief: old.Brief, Model: old.Model, Effort: old.Effort, Role: old.Role, State: store.LaneLaunching, Handoff: old.Handoff, PRs: old.PRs, Predecessor: old.Name, Reason: why, Launcher: old.Launcher}, now))
	do(d.Launch(name, old.Model, old.Effort, path))
	do(d.Post(Post{Kind: "report", To: []string{owningDesk(s, d, old.Name)}, LaneRef: name, Text: fmt.Sprintf("RELAUNCHED %s as %s: it %s; handoff %s; PRs %s; brief %s", old.Name, name, why, or(old.Handoff, "none, the successor writes it"), or(numberList(old.PRs), "none"), path)}))
	out.Relaunched = append(out.Relaunched, Relaunch{old.Name, name, why})
}

func successorBrief(old store.Lane, name, why string, d Deps, transcript string) (string, error) {
	body, err := os.ReadFile(old.Brief)
	if err != nil {
		return "", err
	}
	drive, checkout := d.Drive, d.Checkout
	start := handoffStart(old, d, transcript)
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
		fmt.Sprintf("Ask, report and raise a blocker only with `cci post --drive %s --lane %s --kind ask|report|blocker`; Orca's `send` and `ask` carry the dispatch lifecycle alone.", drive, name),
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

func wake(s *store.Store, d Deps, lane store.Lane, peer Peer, found map[string]Peer, taken map[string]bool, now time.Time, out *Outcome) {
	if settledLane(lane) {
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
			do(s.SetLanePage(lane.Name, with(page, "woke", store.Stamp(now))))
		}
		return
	}
	wokeAt := must(store.Parse(woke))
	if now.Sub(wokeAt) < PageAnswer || now.Sub(peer.Seen) < PageAnswer {
		return
	}
	do(s.SetLanePage(lane.Name, nil))
	relaunch(s, d, lane, found, fmt.Sprintf("has made no tool call in the %d min since it was woken because %s", int(now.Sub(wokeAt).Minutes()), truncated(pageText(page, "why"), 240)), taken, now, out)
}

func chaseUnread(s *store.Store, d Deps, found map[string]Peer, now time.Time) (woken []string, err error) {
	defer recovered(&err)
	do(s.KeepUnread(live...))
	held := map[string][]store.Unread{}
	for _, order := range must(s.Unread()) {
		held[order.Lane] = append(held[order.Lane], order)
	}
	late, raised := map[string][]string{}, map[string][]string{}
	for _, lane := range must(s.Lanes(live...)) {
		if len(held[lane.Name]) == 0 {
			continue
		}
		peer := found[lane.Name]
		read := newer(peer.LastCall, lane.Heard)
		do(s.ReadUnread(lane.Name, read))
		orders := slices.DeleteFunc(held[lane.Name], func(order store.Unread) bool { return order.At.Before(read.Truncate(time.Second)) })
		if wakeOnOrders(s, d, lane, peer, orders, now) {
			woken = append(woken, lane.Name)
		}
		pastDue := slices.DeleteFunc(slices.Clone(orders), func(order store.Unread) bool { return order.Raised || now.Sub(order.At) < unreadAfter })
		if len(pastDue) > 0 {
			to := noticeTo(s, lane.Name)
			late[to] = append(late[to], fmt.Sprintf("%s holds %s, the oldest for %d min", lane.Name, orderWords(pastDue), int(now.Sub(pastDue[0].At).Minutes())))
			raised[to] = append(raised[to], lane.Name)
		}
	}
	for _, to := range slices.Sorted(maps.Keys(late)) {
		do(d.Post(Post{Kind: "blocker", To: []string{to}, Text: fmt.Sprintf("Addressed orders unread for %d minutes or more, with no tool call or record from the lane since: %s. Relay to the lane through orca-desk, or relaunch it.", int(unreadAfter.Minutes()), strings.Join(late[to], "; "))}))
		for _, name := range raised[to] {
			do(s.MarkUnreadRaised(name))
		}
	}
	return woken, nil
}

func wakeOnOrders(s *store.Store, d Deps, lane store.Lane, peer Peer, orders []store.Unread, now time.Time) bool {
	if !peer.Live || (peer.Row.Roster == "" && peer.Row.Dispatch == "") || settledLane(lane) {
		return false
	}
	due := slices.DeleteFunc(slices.Clone(orders), func(order store.Unread) bool { return order.Woke || now.Sub(order.At) < wakeAfter })
	if len(due) == 0 || !isStanding(lane) || !must(d.Wake(lane.Name, peer.Row, map[string]any{"why": fmt.Sprintf("orders addressed to you are unread: %s.", orderWords(due))})) {
		return false
	}
	do(s.MarkUnreadWoken(lane.Name))
	return true
}

func orderWords(orders []store.Unread) string {
	words := make([]string, 0, len(orders))
	for _, order := range orders {
		words = append(words, fmt.Sprintf("%s's %s #%d", order.Author, order.Kind, order.Seq))
	}
	return strings.Join(words, ", ")
}

func with(page map[string]any, key, value string) map[string]any {
	changed := maps.Clone(page)
	changed[key] = value
	return changed
}

func ownedWork(s *store.Store, d Deps, now time.Time) {
	heard, probes := must(s.Heard()), must(s.Probes())
	holds, err := d.Held()
	if errors.Is(err, ErrUnread) {
		return
	}
	do(err)
	retired := map[string]bool{}
	for _, lane := range must(s.Lanes(store.LaneRetired)) {
		retired[lane.Name] = true
	}
	rows := map[string]store.Lane{}
	for _, lane := range must(s.Lanes(live...)) {
		rows[lane.Name] = lane
	}
	var unowned, ended, wanted []int
	quiet := map[string][]int{}
	for name, prs := range must(s.Owners()) {
		var open []int
		for _, n := range prs {
			if !holds[n] {
				open = append(open, n)
			}
		}
		if retired[name] {
			ended = append(ended, open...)
			wanted = append(wanted, open...)
			continue
		}
		if name == Root {
			unowned = open
			wanted = append(wanted, open...)
			continue
		}
		if since, ok := heard[name]; !ok || now.Sub(since) >= liveWithin || waitsOnOwn(rows[name], open) {
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
	var closed []int
	for _, n := range slices.Concat(unowned, ended) {
		key := fmt.Sprintf("%s:%d", Root, n)
		_, asked := probes[key]
		stranded := states[n] == Broken || (states[n] == NeedsWork && slices.Contains(unowned, n))
		switch {
		case stranded && !asked:
			strandedFresh = append(strandedFresh, n)
		case !stranded && asked:
			do(s.Unprobe(key))
		}
		if states[n] == Closed && slices.Contains(ended, n) {
			closed = append(closed, n)
		}
	}
	if len(closed) > 0 {
		do(s.Disown(closed))
	}
	if len(strandedFresh) > 0 {
		verb, pronoun := "need", "them"
		if len(strandedFresh) == 1 {
			verb, pronoun = "needs", "it"
		}
		do(d.Post(Post{Kind: "blocker", To: []string{Root}, Text: fmt.Sprintf("%s %s work and no lane owns %s: the lane that did ended, naming no successor. Name one with `%s`.", numberList(strandedFresh), verb, pronoun, Request{Verb: "owner", Lane: "<lane>", PRs: strandedFresh}.Command())}))
		for _, n := range strandedFresh {
			do(s.Probe(fmt.Sprintf("%s:%d", Root, n), now))
		}
	}
	for _, name := range slices.Sorted(maps.Keys(quiet)) {
		var work, gone []int
		for _, n := range quiet[name] {
			switch states[n] {
			case NeedsWork, Broken:
				work = append(work, n)
			case Closed:
				gone = append(gone, n)
			}
		}
		if len(gone) > 0 {
			do(s.Disown(gone))
			settleOwner(s, name, now)
		}
		lane, known := rows[name]
		if !known || lane.Page != nil {
			continue
		}
		if lane.Waiting != 0 {
			if !waitsOnOwn(lane, quiet[name]) {
				continue
			}
			work = freshlyBroken(s, quiet[name], states, probes, now)
			if len(work) > 0 {
				do(s.SetLaneWaiting(name, 0, now, ""))
			}
		}
		if len(work) == 0 {
			continue
		}
		text := fmt.Sprintf("you own %s, which still need work", numberList(work))
		do(s.SetLanePage(name, map[string]any{"topic": "work:" + name, "why": text, "text": text, "prs": work}))
	}
}

func waitsOnOwn(lane store.Lane, prs []int) bool {
	gates := strings.Fields(lane.Awaits)
	return slices.ContainsFunc(prs, func(n int) bool { return slices.Contains(gates, fmt.Sprintf("pr:%d", n)) })
}

func freshlyBroken(s *store.Store, prs []int, states map[int]string, probes map[string]store.Probe, now time.Time) []int {
	var broken []int
	for _, n := range prs {
		key := brokenProbe(n)
		_, paged := probes[key]
		switch {
		case states[n] == Broken && !paged:
			do(s.Probe(key, now))
			broken = append(broken, n)
		case states[n] != Broken && paged:
			do(s.Unprobe(key))
		}
	}
	return broken
}

func brokenProbe(n int) string {
	return fmt.Sprintf("broken:%d", n)
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
				since, why = last, "is neither live nor heard"
				if seq := must(s.NewestSeq(name)); seq != 0 {
					why = fmt.Sprintf("is neither live nor heard since its record #%d", seq)
				}
			}
		default:
			if at, ok := heard[head.RetiredBy]; ok && succeeds(head.RetiredBy, name) && now.Sub(at) < StaleAfter {
				continue
			}
			since, why = head.Since, fmt.Sprintf("was retired by %s with no successor", or(head.RetiredBy, "nobody"))
		}
		if now.Sub(since) >= unownedAfter {
			escalate(s, d, "lane:"+name, fmt.Sprintf("standing lane %s (%s) has had no live owner for over %d min: it %s. Launch its successor from %s, or post `lr request stand-down %s --successor <lane> --open '<items>'`; deleting the brief's `Standing:` line ends the duty.", name, must(BriefOf(head.Brief)).Standing, int(unownedAfter.Minutes()), why, head.Brief, name), now)
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
	gaps, err := d.Gaps()
	switch {
	case err != nil:
		if must(s.Escalate(owedSubject, err.Error(), Root, now)) {
			do(d.Post(Post{Kind: "blocker", To: []string{Root}, Text: fmt.Sprintf("The owed-deletions check did not run: %v", err)}))
		}
	case len(gaps) > 0:
		digest := fmt.Sprintf("owed-deletions-%x", sha256.Sum256([]byte(strings.Join(gaps, "\n"))))[:27]
		if must(s.Escalate(owedSubject, digest, Root, now)) {
			do(d.Post(Post{Kind: "blocker", To: []string{Root}, Attrs: map[string]string{"digest": digest}, Text: fmt.Sprintf("%s: %d removes targets have no open owed-deletion task and no exception row: %s. Open one owed-deletion task per target, or anchor an exception row to it with `rulings.py anchor`; `lr deletions check` exits 0 once every target is covered, and this digest is reposted only when the set changes.", digest, len(gaps), strings.Join(gaps, "; "))}))
		}
	}
	overdue(s, d, now)
	start := time.Unix((hour-1)*3600, 0).UTC()
	end := start.Add(time.Hour)
	var words []string
	for _, lane := range must(s.AllLanes()) {
		if lane.Predecessor != "" && lane.Reason != "" && !lane.Since.Before(start) && lane.Since.Before(end) {
			words = append(words, fmt.Sprintf("%s %s, so %s took over", lane.Predecessor, lane.Reason, lane.Name))
		}
	}
	if len(words) > 0 {
		noun := "lane"
		if len(words) > 1 {
			noun = "lanes"
		}
		do(d.Post(Post{Kind: "report", To: []string{Root}, Attrs: map[string]string{"audience": "owner"}, Text: fmt.Sprintf("In the past hour the lane supervisor restarted %d %s with no coordinator action: %s.", len(words), noun, strings.Join(words, "; "))}))
	}
	if orders, err := d.Orders(s); !errors.Is(err, ErrUnread) {
		do(err)
		if len(orders) > 0 {
			do(d.Post(Post{Kind: "report", To: []string{OwnerRelay}, Text: fmt.Sprintf("%d owner orders have no live lane, no open PR and no landed or done record: %s. Restart each one, or run `lr owner mark <row> no-order` on a row that orders nothing; a lane delivering an order names its owner:<row> token in its brief.", len(orders), owner.Tokens(orders))}))
		}
	}
	records, err := d.OpenWork()
	if errors.Is(err, ErrUnread) {
		return
	}
	do(err)
	if depth := queueDepth(records); depth != "" {
		do(d.Post(Post{Kind: "report", To: []string{Root}, Text: fmt.Sprintf("Open work by lane, so no queue grows unseen (retro row 133): %s.", depth)}))
	}
}

func queueDepth(records []Record) string {
	queues := map[string][]Record{}
	for _, r := range records {
		for lane := range r.Addressed() {
			if lane != Root && lane != mainLane && lane != "all" && lane != Self {
				queues[lane] = append(queues[lane], r)
			}
		}
	}
	names := slices.SortedFunc(maps.Keys(queues), func(a, b string) int {
		return cmp.Or(cmp.Compare(len(queues[b]), len(queues[a])), cmp.Compare(a, b))
	})
	workKinds := []string{"defect", "ask", "blocker"}
	lines := make([]string, 0, len(names))
	for _, lane := range names {
		oldest := queues[lane][0]
		tally := map[string]int{}
		for _, r := range queues[lane] {
			tally[r.Kind]++
			if r.At.Before(oldest.At) {
				oldest = r
			}
		}
		counts := make([]string, 0, len(workKinds))
		for _, kind := range workKinds {
			switch tally[kind] {
			case 0:
			case 1:
				counts = append(counts, "1 "+kind)
			default:
				counts = append(counts, fmt.Sprintf("%d %ss", tally[kind], kind))
			}
		}
		lines = append(lines, fmt.Sprintf("%s %s (oldest #%d)", lane, strings.Join(counts, ", "), oldest.Seq))
	}
	return strings.Join(lines, "; ")
}
