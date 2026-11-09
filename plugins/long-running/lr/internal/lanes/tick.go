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
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/awake"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/livetasks"
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
	Drive                 string
	Repo                  string
	Ledger                string
	Parent                string
	Checkout              string
	State                 string
	Briefs                string
	Bin                   string
	HandoffBrief          string
	Records               func(since int64) ([]Record, error)
	Who                   func(names []string) ([]Row, error)
	Tokens                func(Row) int
	Transcript            func(Row) string
	Post                  func(Post) error
	Launch                func(name, model, effort, brief, key string) error
	Start                 func(name, model, effort, brief string) error
	Withdraw              func(name string) error
	Wake                  func(name string, row Row, page map[string]any) (bool, error)
	PRStates              func(prs []int) (map[int]string, error)
	Held                  func() (map[int]bool, error)
	Desks                 func() (map[string]string, error)
	OpenWork              func() ([]Record, error)
	Owed                  func() ([]deletions.Owed, error)
	Present               func(rows []deletions.Owed) ([]deletions.Owed, error)
	PublishNotes          func(lane string) error
	Gaps                  func() ([]string, error)
	Orders                func(s *store.Store) ([]owner.Row, error)
	Undelivered           func() ([]owner.Row, error)
	Promises              func() ([]Promise, error)
	RotateAt              int
	RetireAfter           time.Duration
	OpenBranches          func(s *store.Store) (map[string][]int, error)
	Ours                  func(s *store.Store, prs []int) ([]int, error)
	Sprites               func(lanes []string) (map[string]Sprite, error)
	Hosts                 func(lanes []string, sprites map[string]Sprite) map[string]Host
	LastWords             func(sprite string) string
	Carried               func(lane string) (Carried, error)
	BuildRecommendedOrder string
}

type Carried struct {
	Commit string
	At     time.Time
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
	undelivered := sync.OnceValues(d.Undelivered)
	d.Undelivered = func() ([]owner.Row, error) {
		rows, err := undelivered()
		return slices.Clone(rows), err
	}
	promises := sync.OnceValues(d.Promises)
	d.Promises = func() ([]Promise, error) {
		promised, err := promises()
		return slices.Clone(promised), err
	}
	cursor := must(s.Cursor(CursorName))
	fresh := cursor == 0
	var named []string
	var running []store.Lane
	if !fresh {
		running = must(s.Lanes(store.LaneLaunching, store.LaneDesired, store.LaneHeld))
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
	announce(s, d, said, now)
	for _, one := range said.refused {
		refusedRelaunch(s, d, one, now, &out)
	}
	for _, name := range said.unheard {
		raiseUnheard(s, d, name, now)
	}
	ownedWork(s, d, now)
	clocksRanOut(s, now)
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
	for _, one := range said.revived {
		revive(s, d, one, found, now)
	}
	launchOwedHeirs(s, d, found, now, &out)
	taken := map[string]bool{}
	for _, lane := range must(s.AllLanes()) {
		taken[lane.Name] = true
	}
	for name := range found {
		taken[name] = true
	}
	for _, one := range said.unfinished {
		lane, _ := laneOf(s, one.lane)
		relaunch(s, d, lane, found, one.why, "", taken, now, &out)
	}
	desired := must(s.Lanes(live...))
	proven := proofs(s, d, desired, found, now)
	for _, lane := range desired {
		act(s, d, lane, found, proven, taken, generation, now, &out)
	}
	relaunchNeverStarted(s, d, running, found, taken, now, &out)
	tellRetired(s, d, running, said.stood, now)
	retiredTo := map[string][]string{}
	for _, one := range out.Retired {
		retiredTo[one.To] = append(retiredTo[one.To], fmt.Sprintf("%s (it %s)", one.Lane, one.Why))
	}
	for _, to := range slices.Sorted(maps.Keys(retiredTo)) {
		do(d.Post(Post{Kind: "report", To: []string{to}, Text: fmt.Sprintf("RETIRED for owning no PR, holding no standing duty, and posting nothing for %d minutes or more: %s.", int(d.RetireAfter.Minutes()), strings.Join(retiredTo[to], "; "))}))
	}
	claimBranches(s, d, now)
	reachRetired(s, d, taken, now, &out)
	openAnswered(s, d, found, taken, now, &out)
	keepDuties(s, d, found, taken, now, &out)
	hourly(s, d, now)
	orphanOrders(s, d, now)
	alarmRowWaits(s, d, now)
	missedPromises(s, d, now)
	woken, err := chaseUnread(s, d, found, out.Woken, taken, now, &out)
	if err != nil {
		slog.Warn("unread orders not chased this tick", "err", err)
	}
	out.Woken = append(out.Woken, woken...)
	return out, nil
}

func tellRetired(s *store.Store, d Deps, running []store.Lane, told []stoodDown, now time.Time) {
	for _, was := range running {
		lane, _ := laneOf(s, was.Name)
		endedItself := slices.Contains([]string{"done", "stopped", "stand-down", "handoff"}, lane.Last)
		if lane.State != store.LaneRetired || lane.RetiredBy == "stand-down" && !endedItself || slices.ContainsFunc(told, func(one stoodDown) bool { return one.name == lane.Name }) {
			continue
		}
		post := Post{Kind: "stand-down", To: []string{lane.Name}, Text: fmt.Sprintf("%s stood down: the supervisor retired it (%s).", lane.Name, lane.RetiredBy), PRs: lane.PRs}
		if lane.Last == "stopped" {
			post.Text = fmt.Sprintf("%s stopped, so mail to it passes on. If it is parked on a wait, keep waiting: its next record makes it live again.", lane.Name)
		}
		if heir, ok := laneOf(s, lane.RetiredBy); ok && heir.Name != Root {
			post.Successor, post.PRs, post.Text = heir.Name, was.PRs, fmt.Sprintf("%s stood down for %s.", lane.Name, heir.Name)
			if heir.Predecessor == lane.Name {
				post.PRs = heir.PRs
			}
			if heir.Predecessor == lane.Name && heir.Reason != "" {
				post.Text = fmt.Sprintf("%s stood down for %s; %s %s.", lane.Name, heir.Name, lane.Name, heir.Reason)
			}
			post.Text = withCarried(post.Text, carriedWork(d, lane, now))
		}
		do(d.Post(post))
	}
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

func announce(s *store.Store, d Deps, said folded, now time.Time) {
	for _, one := range said.stood {
		how := ", its PRs to " + said.prsTo(one.name)
		if one.successor != "" {
			how = " for " + one.successor
		}
		lane, _ := laneOf(s, one.name)
		do(d.Post(Post{Kind: "stand-down", To: []string{one.name}, Text: withCarried(fmt.Sprintf("%s stood down%s by record %s.", one.name, how, one.by), carriedWork(d, lane, now)), Successor: one.successor, Re: one.seq, PRs: one.prs}))
	}
	for _, one := range said.kept {
		successor, done := Request{Verb: "stand-down", Lane: one.name, Successor: "<successor>", Open: "<item>; <item>"}, Request{Verb: "done", Lane: one.name}
		do(d.Post(Post{Kind: "ask", To: []string{Root}, LaneRef: one.name, Re: one.seq, Text: fmt.Sprintf("%s is a standing lane and stand-down %s names no successor, so the supervisor kept it live. Name a successor with `%s`, or say the lane is done with `%s`.", one.name, one.by, successor.Command(), done.Command())}))
	}
	if len(said.ended) > 0 {
		lines := make([]string, len(said.ended))
		for i, one := range said.ended {
			lines[i] = fmt.Sprintf("%s by stand-down %s, its PRs to %s", one.name, one.by, said.prsTo(one.name))
			if one.successor != "" {
				lines[i] = fmt.Sprintf("%s by stand-down %s, for %s", one.name, one.by, one.successor)
			}
		}
		do(d.Post(Post{Kind: "report", To: []string{OwnerRelay}, Text: "RETIRED " + strings.Join(lines, "; ") + "."}))
	}
	for _, one := range said.handed {
		do(d.Post(handedOn(one.to, []string{one.from}, one.prs)))
	}
	verbs := slices.Sorted(slices.Values(Verbs))
	for _, record := range said.untyped {
		do(d.Post(Post{Kind: "defect", To: []string{Root}, Text: fmt.Sprintf("The coordinator's decide %s carries a verb but is no registry request, so the registry is unchanged; post `%s` instead.", record.Name(), Request{Verb: "<" + strings.Join(verbs, "|") + ">", Lane: "<lane>"}.Command()), Re: record.Seq}))
	}
	for _, one := range said.unread {
		to := []string{noticeTo(s, one.name), one.name}
		if !slices.Contains(to, one.asker) {
			to = append(to, one.asker)
		}
		one.retry.LastRead = strconv.FormatInt(one.newest, 10)
		do(d.Post(Post{Kind: "defect", To: to, LaneRef: one.name, Text: fmt.Sprintf("%s, so the supervisor refused it and holds %s with its PRs, never relaunched or retired, until %s posts again; %s, keep working. %s: read %s with `cci tail --lane %s --since <its wake seq> --oldest` through #%d, then run `%s`.", one.why, one.name, one.name, one.name, one.asker, one.name, one.name, one.newest, one.retry.Command())}))
	}
	for _, one := range said.owing {
		one.post(s, d)
	}
	for _, one := range said.unassigned {
		text := fmt.Sprintf("ASSIGNMENT-UNREAD cci %s names %s, retired at %s with no successor, so no lane reads it; name a live lane or relaunch it", one.record.Name(), one.lane.Name, one.lane.Since.In(pacificZone).Format("Jan 2 3:04 PM"))
		if must(s.Escalate(fmt.Sprintf("assignment:%d:%s", one.record.Seq, one.lane.Name), text, Root, now)) {
			do(d.Post(Post{Kind: blockerKind, To: []string{Root}, LaneRef: one.lane.Name, Re: one.record.Seq, Text: text}))
		}
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
	if verdict.Kind == Ok && lane.State == store.LaneDesired && lane.Page == nil && peer != nil && peer.Live && lane.Name != Root && !isStanding(lane) && (!holdsNothing(lane) || lane.Waiting != 0) && (lane.Last == "handoff" || !settledLane(lane)) && now.Sub(newer(peer.Seen, lane.Heard)) >= PageAnswer {
		stalled(s, d, lane, *peer, found, taken, now, out)
		return
	}
	switch verdict.Kind {
	case CameUp:
		do(s.SetLaneState(lane.Name, store.LaneDesired, now, "", ""))
		do(s.Unprobe(launchTry + lane.Name))
		if coordinates(lane.Name) && lane.Reason != "" {
			do(d.Post(Post{Kind: "report", To: []string{d.coordinatorReportsTo()}, LaneRef: lane.Name, Text: fmt.Sprintf("RELAUNCHED the coordinator: %s is live in cci who; it was relaunched because it %s", lane.Name, lane.Reason)}))
		}
	case NoShow:
		if lane.Placement == placedOnSprite {
			lost(s, d, lane, found, Verdict{Lost, fmt.Sprintf("has posted no record and is not live %d minutes after its launch", int(launchWindow.Minutes()))}, taken, now, out)
			return
		}
		retryLaunch(s, d, lane, verdict.Why, now, out)
	case Unproven:
		proof, settled := proven[lane.Name]
		if proof.Kind == Lost {
			lost(s, d, lane, found, proof, taken, now, out)
			return
		}
		if !needsProof(lane, silent, d.RetireAfter) {
			return
		}
		if !settled {
			if lane.Placement == placedOnSprite && isStanding(lane) {
				return
			}
			if peer != nil && peer.Row.Unverified != "" && lane.Name != Root {
				askState(s, d, lane, verdict.Why, now)
				return
			}
			if dutyOf(lane) != "" {
				relaunch(s, d, lane, found, verdict.Why, "", taken, now, out)
				return
			}
			hold(s, lane.Name, verdict.Why, now)
			escalateTo(s, d, "lane:"+lane.Name, fmt.Sprintf("held, not relaunched or retired: it %s, and it runs on no Sprite whose probe answered; brief %s", verdict.Why, lane.Brief), residueTo(s, d, lane.Name), now)
			out.Held = append(out.Held, lane.Name)
			return
		}
		switch proof.Kind {
		case Dead:
			died(s, d, lane, peer, found, proof, silent, taken, now, out)
		case Unanswered:
			unanswered(s, d, lane, peer, found, proof, silent, taken, now, out)
		}
	case StoodDown:
		if !refusedOwing(s, d, lane.Name, now) {
			retire(s, lane.Name, "stand-down", now)
		}
	case Dead:
		died(s, d, lane, peer, found, verdict, silent, taken, now, out)
	case AskRotate:
		do(d.Post(Post{Kind: "ask", To: []string{lane.Name}, Text: fmt.Sprintf("ROTATE: %s is at %s. Write anything not yet in the ledger or cc-notes to a cc-notes doc, post `cci post --lane %s --kind handoff --ccn <doc id> --re <this record>`, %s.", lane.Name, verdict.Why, lane.Name, afterHandoff(lane))}))
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
		if coordinates(lane.Name) {
			relaunch(s, d, lane, found, "posted worker_done, and a drive always keeps its coordinator", "", taken, now, out)
			return
		}
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
		relaunch(s, d, lane, found, verdict.Why, "", taken, now, out)
		return
	}
	if silent >= d.RetireAfter {
		retireSilent(s, d, lane, verdict.Why, now, out)
	}
}

func stalled(s *store.Store, d Deps, lane store.Lane, peer Peer, found map[string]Peer, taken map[string]bool, now time.Time, out *Outcome) {
	if transcript := d.Transcript(peer.Row); transcript != "" {
		if tasks, err := livetasks.Read(transcript, now); err == nil && len(tasks) > 0 {
			return
		}
	}
	quiet := int(now.Sub(newer(peer.Seen, lane.Heard)).Minutes())
	if lane.Last == "handoff" {
		relaunch(s, d, lane, found, fmt.Sprintf("wrote its handoff at %s and has made no tool call in the %d min since", pacific(lane.Heard), quiet), "", taken, now, out)
		return
	}
	text := fmt.Sprintf("SILENT %s is live in Orca but has made no tool call in %d min and runs no wait, holding %s; relaunch it from its handoff or brief %s, or stand it down", lane.Name, quiet, or(numberList(lane.PRs), "no PR"), lane.Brief)
	if must(s.Escalate(fmt.Sprintf("silent:%s:%d", lane.Name, must(s.NewestSeq(lane.Name))), "silent", Root, now)) {
		do(d.Post(Post{Kind: "blocker", To: []string{Root}, LaneRef: lane.Name, Text: text}))
	}
}

func askState(s *store.Store, d Deps, lane store.Lane, why string, now time.Time) {
	do(d.Post(Post{Kind: "go", To: []string{orcaDesk}, Text: fmt.Sprintf("%s: relay to %s: This lane %s. Post your state now with `cci post --drive %s --lane %s --kind state`: what you hold and what you wait on. With no record in %d min the supervisor relaunches this lane from its brief and handoff.", orcaDesk, lane.Name, strings.TrimSuffix(why, nothingProves), d.Drive, lane.Name, int(relayAnswer.Minutes()))}))
	do(s.Probe(relayProbe(lane.Name), now))
}

func unanswered(s *store.Store, d Deps, lane store.Lane, peer *Peer, found map[string]Peer, proof Verdict, silent time.Duration, taken map[string]bool, now time.Time, out *Outcome) {
	do(s.Unprobe(relayProbe(lane.Name)))
	failed := spentRelaunches(s, d, lane, found, proof.Why, taken, now)
	if failed == "" && !briefExists(lane.Brief) {
		failed = fmt.Sprintf("its brief %s is gone, and it %s", lane.Brief, proof.Why)
	}
	if failed == "" || holdsNothing(lane) || lane.Last == kindLanded {
		died(s, d, lane, peer, found, proof, silent, taken, now, out)
		return
	}
	hold(s, lane.Name, proof.Why, now)
	escalateTo(s, d, "lane:"+lane.Name, "held, not relaunched: "+failed, residueTo(s, d, lane.Name), now)
	out.Held = append(out.Held, lane.Name)
}

func lost(s *store.Store, d Deps, lane store.Lane, found map[string]Peer, verdict Verdict, taken map[string]bool, now time.Time, out *Outcome) {
	to := []string{must(MetaDesk(s))}
	if launcher, known := newest(s, lane.Launcher); known && isLive(launcher.State) && launcher.Name != to[0] {
		to = append([]string{launcher.Name}, to...)
	}
	report := func(outcome string) {
		do(d.Post(Post{Kind: "report", To: to, LaneRef: lane.Name, Text: fmt.Sprintf("LANE-LOST %s: it %s, and its dispatch never settled; %s.", lane.Name, verdict.Why, outcome)}))
	}
	if spent := spentRelaunches(s, d, lane, found, verdict.Why, taken, now); spent != "" {
		hold(s, lane.Name, verdict.Why, now)
		out.Held = append(out.Held, lane.Name)
		report("held, not relaunched: " + spent)
		return
	}
	newestSeq := "none"
	if seq := must(s.NewestSeq(lane.Name)); seq != 0 {
		newestSeq = fmt.Sprintf("#%d", seq)
	}
	kept := "any edit it never pushed stays on that Sprite"
	if lane.Placement == placedOnSprite {
		kept = "any edit it made after its Sprite last pushed its work to `" + awake.WorkRefs + lane.Name + "` stays on that Sprite"
	}
	partial := fmt.Sprintf("Partial work: %s was lost mid-task, since it %s; it left PRs %s, handoff %s and newest record %s, and %s.", lane.Name, verdict.Why, or(numberList(lane.PRs), "none"), or(lane.Handoff, "none"), newestSeq, kept)
	successor := relaunch(s, d, lane, found, verdict.Why, partial, taken, now, out)
	if successor == "" {
		report("held, not relaunched; its lane:" + lane.Name + " record says why")
		return
	}
	report("relaunched as " + successor + " from its brief")
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
	branches, err := d.OpenBranches(s)
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
		to, owner := Root, "the coordinator"
		if member := defaultOwner(s); member != "" {
			to, owner = member, member+", the default owner"
		}
		do(s.Own(to, orphans))
		do(d.Post(Post{Kind: "report", To: []string{Root}, PRs: orphans, Text: fmt.Sprintf("ORPHANED PRs now owned by %s, since no lane owned them when their lanes retired: %s. Name an owner with `%s`.", owner, strings.Join(left, "; "), Request{Verb: "owner", Lane: "<lane>", PRs: orphans}.Command())}))
	}
	for _, name := range unchecked {
		do(s.Probe(branchProbe(name), now))
	}
}

func holdsNothing(lane store.Lane) bool {
	return lane.Name != Root && len(lane.PRs) == 0 && !isStanding(lane)
}

func settledLane(lane store.Lane) bool {
	if lane.Last == "handoff" {
		return !lane.Flushed.IsZero() && !lane.Flushed.Before(lane.Heard)
	}
	return slices.Contains(settled, lane.Last) && (lane.Last != "done" || !isStanding(lane))
}

func isStanding(lane store.Lane) bool {
	return slices.Contains(standingRoles, lane.Role) || dutyOf(lane) != ""
}

const communicatorDuty = "relays the owner's words to the drive and the drive's answers to him"

func dutyOf(lane store.Lane) string {
	if lane.RetiredBy == retiredDutyEnded {
		return ""
	}
	if client.Stem(lane.Name) == OwnerRelay {
		return communicatorDuty
	}
	if slices.Contains(oneShotRoles, lane.Role) {
		return ""
	}
	return or(lane.Standing, must(BriefOf(lane.Brief)).Standing)
}

func endsAtRotation(lane store.Lane) bool {
	brief := must(BriefOf(lane.Brief))
	return brief.Desk || brief.Standing != ""
}

func afterHandoff(lane store.Lane) string {
	if endsAtRotation(lane) {
		return "then post a `stopped` record and end your session; the supervisor launches your successor from that handoff"
	}
	return "then keep working"
}

func dutyEnd(lane store.Lane) string {
	if lane.Standing != "" {
		return fmt.Sprintf("run `lr request done %s`, which ends the duty it took from the lane it succeeds", lane.Name)
	}
	return fmt.Sprintf("delete the `Standing:` line in %s", lane.Brief)
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
	must(s.ReleaseWorker(Root))
	out.Relaunched = append(out.Relaunched, Relaunch{Root, Root, why})
	if err := launchOrRetry(d, Root, old.Model, old.Effort, path, fmt.Sprintf("%s%s:%d", launchKey, Root, now.Unix())); err != nil {
		startDirectly(s, d, store.Lane{Name: Root, Model: old.Model, Effort: old.Effort}, path, fmt.Sprintf("the orca runner refused its relaunch twice, last with %s", err), why, now, out)
		return
	}
	do(s.SetLaneState(Root, store.LaneLaunching, now, "", why))
	do(d.Post(Post{Kind: "report", To: []string{d.coordinatorReportsTo()}, LaneRef: Root, Text: fmt.Sprintf("RELAUNCH ORDERED for the coordinator: it %s; it counts as relaunched once cci who shows it live; handoff %s; brief %s", why, or(old.Handoff, "none, the new session writes it"), path)}))
}

func handoffStart(old store.Lane, d Deps, transcript string) string {
	if old.Handoff != "" {
		return fmt.Sprintf("Your handoff is `ccn -R %s doc show %s`; read it first.", d.Checkout, old.Handoff)
	}
	return fmt.Sprintf("%s left no handoff. Before anything else, spawn one `long-running:lane` subagent, model sonnet, effort high, named `%s-handoff`, from the spawn brief in %s, with transcript %s, reference brief %s, cci drive %s, previous handoff none and drive checkout %s; read the doc it returns.", old.Name, old.Name, d.HandoffBrief, or(transcript, "unknown: find it with cc-transcript list"), old.Brief, d.Drive, d.Checkout)
}

func coordinatorBrief(old store.Lane, why string, d Deps, transcript string) (string, error) {
	body, err := os.ReadFile(old.Brief)
	if err != nil {
		return "", err
	}
	return strings.Join([]string{
		"Replaces: " + Root,
		"ccx: place=local why=mac-state",
		fmt.Sprintf("You are %s, this drive's coordinator, in a new session. The lane supervisor launched you from your brief because the last coordinator session %s.", Root, why),
		handoffStart(old, d, transcript),
		"",
		string(body),
	}, "\n"), nil
}

func relaunch(s *store.Store, d Deps, old store.Lane, found map[string]Peer, why, partial string, taken map[string]bool, now time.Time, out *Outcome) string {
	return launchHeir(s, d, old, found, why, partial, spentRelaunches(s, d, old, found, why, taken, now), taken, now, out)
}

func reachRetired(s *store.Store, d Deps, taken map[string]bool, now time.Time, out *Outcome) {
	var waiting []store.Lane
	var names []string
	pages := must(s.Pages())
	for _, name := range slices.Sorted(maps.Keys(pages)) {
		if pageText(pages[name], "topic") != retiredMailTopic {
			continue
		}
		if lane, found := laneOf(s, name); found && lane.State == store.LaneRetired {
			waiting, names = append(waiting, lane), append(names, name)
		}
	}
	if len(waiting) == 0 {
		return
	}
	rows := must(d.Who(names))
	for i := range rows {
		rows[i].Lane = Canonical(d.Drive, rows[i].Lane)
	}
	found := Peers(rows)
	for _, lane := range waiting {
		peer := peerOf(found, lane.Name)
		if peer != nil && answersAWake(peer.Row) {
			if must(d.Wake(lane.Name, peer.Row, lane.Page)) {
				do(s.SetLanePage(lane.Name, nil))
				out.Woken = append(out.Woken, lane.Name)
			}
			continue
		}
		launchHeir(s, d, lane, found, pageText(lane.Page, "why"), retiredMailBrief(lane), "", taken, now, out)
		do(s.SetLanePage(lane.Name, nil))
	}
}

func answersAWake(row Row) bool {
	reachable := row.Dispatch != "" || row.Roster != ""
	return reachable && (row.Live || (row.Unverified != "" && !row.HostGone))
}

func retiredMailBrief(lane store.Lane) string {
	seqs := pageSeqs(lane.Page)
	shows := make([]string, len(seqs))
	for i, seq := range seqs {
		shows[i] = fmt.Sprintf("`cci show %d`", seq)
	}
	return fmt.Sprintf("Act on what reached %s while it was gone; any record of yours naming a card here acknowledges his answer to it, and %s prints each one whole:\n%s\nIts worktree may be gone, since gc removes one that is clean and pushed: its records name each branch and PR it pushed, so cut a fresh worktree from those.", lane.Name, strings.Join(shows, " and "), pageText(lane.Page, "text"))
}

func coordinates(name string) bool {
	base, _ := family(name)
	return base == Root
}

func launchHeir(s *store.Store, d Deps, old store.Lane, found map[string]Peer, why, partial, spent string, taken map[string]bool, now time.Time, out *Outcome) string {
	if coordinates(old.Name) {
		relaunchCoordinator(s, d, old, found, why, now, out)
		return Root
	}
	for _, name := range slices.Sorted(maps.Keys(found)) {
		peer := found[name]
		if alive(&peer, peer.Seen, now) && (succeeds(name, old.Name) || name == Stem(old.Name)) {
			retire(s, old.Name, name, now)
			do(d.Post(Post{Kind: "stand-down", To: []string{old.Name}, Text: withCarried(fmt.Sprintf("%s stood down for %s, already up; %s %s.", old.Name, name, old.Name, why), carriedWork(d, old, now)), Successor: name, PRs: old.PRs}))
			return name
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
		return ""
	}
	if spent != "" {
		hold(s, old.Name, why, now)
		to := noticeTo(s, old.Name)
		if isStanding(old) {
			to = must(MetaDesk(s))
		}
		cause := "held, not relaunched: " + spent
		if must(s.Escalate("lane:"+old.Name, cause, to, now)) {
			kind := "defect"
			if isStanding(old) {
				kind = "blocker"
			}
			do(d.Post(Post{Kind: kind, To: []string{to}, LaneRef: old.Name, Text: fmt.Sprintf("lane:%s: %s", old.Name, cause)}))
		}
		out.Held = append(out.Held, old.Name)
		return ""
	}
	old.PRs = ours(s, d, old.PRs)
	if last := lastWorked(d, old); len(old.PRs) == 0 && !isStanding(old) && !last.IsZero() && now.Sub(last) > abandonedAfter {
		hold(s, old.Name, why, now)
		escalateTo(s, d, "lane:"+old.Name, fmt.Sprintf("held, not relaunched: it owns no PR and its last record or carried work is from %s, over %d hours ago; relaunch it from %s if its work is still wanted, or withdraw it with `orca-desk: withdraw %s`; it %s", pacific(last), int(abandonedAfter.Hours()), old.Brief, old.Name, why), Root, now)
		out.Held = append(out.Held, old.Name)
		return ""
	}
	name := SuccessorName(old.Name, taken)
	taken[name] = true
	work := carriedWork(d, old, now)
	path := writeSuccessorBrief(d, old, found, name, why, partial, work)
	cost := successorCost(old)
	if err := launchOrRetry(d, name, cost.Model, cost.Effort, path, launchKey+name); err != nil {
		hold(s, old.Name, why, now)
		escalateTo(s, d, "lane:"+old.Name, fmt.Sprintf("held, not relaunched: its relaunch as %s was refused twice, last with %s; it %s; brief %s", name, err, why, path), must(MetaDesk(s)), now)
		out.Held = append(out.Held, old.Name)
		return ""
	}
	to := owningDesk(s, d, old.Name)
	if isStanding(old) {
		to = must(MetaDesk(s))
	}
	retire(s, old.Name, name, now)
	must(s.WantLane(store.Lane{Name: name, Brief: old.Brief, Model: cost.Model, Effort: cost.Effort, Role: old.Role, State: store.LaneLaunching, Handoff: old.Handoff, PRs: old.PRs, Predecessor: old.Name, Reason: why, Placement: old.Placement, Launcher: old.Launcher, Standing: old.Standing, Step: old.Step}, now))
	do(d.Post(Post{Kind: "report", To: []string{to}, LaneRef: name, Text: withCarried(fmt.Sprintf("RELAUNCHED %s as %s: it %s; handoff %s; PRs %s; brief %s", old.Name, name, why, or(old.Handoff, "none, the successor writes it"), or(numberList(old.PRs), "none"), path), work)}))
	out.Relaunched = append(out.Relaunched, Relaunch{old.Name, name, why})
	return name
}

func launchOwedHeirs(s *store.Store, d Deps, found map[string]Peer, now time.Time, out *Outcome) {
	posted := must(s.Heard())
	for _, key := range slices.Sorted(maps.Keys(must(s.Probes()))) {
		name, owed := strings.CutPrefix(key, owedLaunch)
		if !owed {
			continue
		}
		heir, known := laneOf(s, name)
		_, listed := found[name]
		_, up := posted[name]
		if !known || heir.State != store.LaneLaunching || listed || up {
			do(s.Unprobe(key))
			continue
		}
		if alias := liveAlias(found, name, heir.Predecessor, now); alias != "" {
			retire(s, name, alias, now)
			do(s.Unprobe(key))
			continue
		}
		old, _ := laneOf(s, heir.Predecessor)
		err := fmt.Errorf("its predecessor's brief %s is gone", old.Brief)
		if briefExists(old.Brief) {
			err = launchOrRetry(d, name, heir.Model, heir.Effort, writeSuccessorBrief(d, old, found, name, heir.Reason, "", carriedWork(d, old, now)), launchKey+name)
		}
		do(s.Unprobe(key))
		if err == nil {
			continue
		}
		hold(s, name, heir.Reason, now)
		to := must(MetaDesk(s))
		if isStanding(heir) {
			to = Root
		}
		escalateTo(s, d, "lane:"+name, fmt.Sprintf("held, not launched: %s is standing in for %s, which %s, and its launch failed: %s", name, old.Name, heir.Reason, err), to, now)
		out.Held = append(out.Held, name)
	}
}

func liveAlias(found map[string]Peer, stem, predecessor string, now time.Time) string {
	if numbered.MatchString(stem) {
		return ""
	}
	for _, name := range slices.Sorted(maps.Keys(found)) {
		peer := found[name]
		if base, _ := family(name); base == stem && name != predecessor && numbered.MatchString(name) && alive(&peer, peer.Seen, now) {
			return name
		}
	}
	return ""
}

func revive(s *store.Store, d Deps, one revival, found map[string]Peer, now time.Time) {
	why := unrevivable(s, one.lane, peerOf(found, one.lane))
	if one.revived {
		why = launchRevived(s, d, one, found, now)
	}
	if why == "" {
		return
	}
	do(d.Post(Post{Kind: "report", To: []string{one.by}, LaneRef: one.lane, Re: one.seq, Text: fmt.Sprintf("REVIVE REFUSED: %s did not launch: %s.", one.lane, why)}))
}

func unrevivable(s *store.Store, name string, peer *Peer) string {
	row, known := laneOf(s, name)
	if !known {
		return "the registry has no lane by that name"
	}
	if row.State == store.LaneHeld {
		return fmt.Sprintf("it is held, not retired: %s. A revive brings back only a retired lane", row.Reason)
	}
	heard := "it was never heard"
	if !row.Heard.IsZero() {
		heard = fmt.Sprintf("it was last heard %s (%s)", pacific(row.Heard), row.Last)
	}
	session := "a live session answers for it"
	if peer == nil || !peer.Live {
		session = "no live session answers for it, so the supervisor's liveness check relaunches or holds it, not a revive"
	}
	return fmt.Sprintf("it is %s, not retired, so the registry counts it running; %s, and %s. A revive brings back only a retired lane", row.State, heard, session)
}

func launchRevived(s *store.Store, d Deps, one revival, found map[string]Peer, now time.Time) string {
	lane, _ := laneOf(s, one.lane)
	if peer := peerOf(found, lane.Name); peer != nil && peer.Live {
		return ""
	}
	err := fmt.Errorf("its brief %s is gone", lane.Brief)
	if briefExists(lane.Brief) {
		err = d.Launch(lane.Name, lane.Model, lane.Effort, lane.Brief, fmt.Sprintf("%s%s:revive:%d", launchKey, lane.Name, one.seq))
	}
	if err != nil {
		hold(s, lane.Name, "its revive did not launch: "+err.Error(), now)
		return fmt.Sprintf("%v, so the supervisor holds it", err)
	}
	do(s.SetLaneState(lane.Name, store.LaneLaunching, now, "", ""))
	return ""
}

func launchOrRetry(d Deps, name, model, effort, brief, key string) error {
	if err := d.Launch(name, model, effort, brief, key); err == nil {
		return nil
	}
	return d.Launch(name, model, effort, brief, key)
}

func retryLaunch(s *store.Store, d Deps, lane store.Lane, why string, now time.Time, out *Outcome) {
	brief := filepath.Join(d.Briefs, lane.Name+".brief.md")
	if !briefExists(brief) {
		brief = lane.Brief
	}
	reason := or(lane.Reason, why)
	if _, retried := must(s.Probes())[launchTry+lane.Name]; !retried {
		do(s.Probe(launchTry+lane.Name, now))
		err := d.Launch(lane.Name, lane.Model, lane.Effort, brief, fmt.Sprintf("%s%s:try:%d", launchKey, lane.Name, now.Unix()))
		if err == nil {
			do(s.SetLaneState(lane.Name, store.LaneLaunching, now, "", reason))
			return
		}
		startDirectly(s, d, lane, brief, fmt.Sprintf("it was %s, and its second launch through the orca runner was refused with %s", why, err), reason, now, out)
		return
	}
	startDirectly(s, d, lane, brief, fmt.Sprintf("twice it was %s through the orca runner", why), reason, now, out)
}

func startDirectly(s *store.Store, d Deps, lane store.Lane, brief, mechanism, reason string, now time.Time, out *Outcome) {
	do(s.Unprobe(launchTry + lane.Name))
	to := owningDesk(s, d, lane.Name)
	if coordinates(lane.Name) {
		to = d.coordinatorReportsTo()
	}
	if err := d.Start(lane.Name, lane.Model, lane.Effort, brief); err != nil {
		hold(s, lane.Name, mechanism, now)
		out.Held = append(out.Held, lane.Name)
		do(d.Post(Post{Kind: "blocker", To: []string{to}, LaneRef: lane.Name, Text: fmt.Sprintf("%s did not start: %s, so the supervisor started it directly with `lr launch start`, which failed with %s; brief %s", lane.Name, mechanism, err, brief)}))
		return
	}
	do(s.SetLaneState(lane.Name, store.LaneLaunching, now, "", reason))
	do(d.Post(Post{Kind: "report", To: []string{to}, LaneRef: lane.Name, Text: fmt.Sprintf("%s did not start: %s, so the supervisor started it directly with `lr launch start`; it counts as started once cci who shows it live; brief %s", lane.Name, mechanism, brief)}))
}

func refusedRelaunch(s *store.Store, d Deps, refused refusal, now time.Time, out *Outcome) {
	lane, found := laneOf(s, refused.lane)
	if !found || lane.State != store.LaneLaunching || lane.Reason == "" {
		return
	}
	path, retried := filepath.Join(d.Briefs, lane.Name+".brief.md"), "relaunch-retried:"+lane.Name
	if strings.Contains(refused.why, SpriteLimit) {
		hold(s, lane.Name, refused.why, now)
		escalateTo(s, d, spriteCapacity, "the org is at its Sprite running limit, so the supervisor holds each relaunch the provider refuses for it instead of retrying into a full org; free running Sprites, then relaunch the lanes `lr lanes` reads as held from their briefs", Root, now)
		out.Held = append(out.Held, lane.Name)
		return
	}
	if _, again := must(s.Probes())[retried]; !again {
		do(s.Probe(retried, now))
		if d.Launch(lane.Name, lane.Model, lane.Effort, path, fmt.Sprintf("%s%s:retry:%d", launchKey, lane.Name, lane.Since.Unix())) == nil {
			return
		}
	}
	hold(s, lane.Name, refused.why, now)
	out.Held = append(out.Held, lane.Name)
	if held, _ := laneOf(s, lane.Name); neverStarted(held) {
		return
	}
	escalateTo(s, d, "lane:"+lane.Name, fmt.Sprintf("held, not launched: the runner refused the supervisor's relaunch as %s twice, last with %s; brief %s", lane.Name, refused.why, path), must(MetaDesk(s)), now)
}

func relaunchNeverStarted(s *store.Store, d Deps, running []store.Lane, found map[string]Peer, taken map[string]bool, now time.Time, out *Outcome) {
	for _, was := range running {
		if was.State != store.LaneHeld {
			continue
		}
		lane, _ := laneOf(s, was.Name)
		if peer := peerOf(found, lane.Name); !neverStarted(lane) || strings.Contains(lane.Reason, SpriteLimit) || peer != nil && peer.Live {
			continue
		}
		if !isStanding(lane) {
			escalateTo(s, d, "lane:"+lane.Name, fmt.Sprintf("never started: %s. Relaunch it from %s, or withdraw it with `orca-desk: withdraw %s`", lane.Reason, filepath.Join(d.Briefs, lane.Name+".brief.md"), lane.Name), must(MetaDesk(s)), now)
			continue
		}
		worked := lane.Predecessor
		for seen := map[string]bool{lane.Name: true}; !seen[worked]; {
			seen[worked] = true
			before, known := laneOf(s, worked)
			if !known || !before.Heard.IsZero() || before.Predecessor == "" {
				break
			}
			worked = before.Predecessor
		}
		left := fmt.Sprintf("read its records with `cci tail --drive %s --lane %s --oldest`", d.Drive, worked)
		if lane.Placement == placedOnSprite {
			left += fmt.Sprintf(" and fetch `%s%s`, which holds any edit its Sprite pushed", awake.WorkRefs, worked)
		}
		relaunch(s, d, lane, found, "never started: "+lane.Reason, fmt.Sprintf("Partial work: %s never started, so the work it took over is still %s's: %s.", lane.Name, worked, left), taken, now, out)
	}
}

func writeSuccessorBrief(d Deps, old store.Lane, found map[string]Peer, name, why, partial, work string) string {
	path := filepath.Join(d.Briefs, name+".brief.md")
	do(os.MkdirAll(d.Briefs, 0o750))
	transcript := ""
	if peer, ok := found[old.Name]; ok {
		transcript = d.Transcript(peer.Row)
	}
	do(os.WriteFile(path, []byte(must(successorBrief(old, name, why, partial, work, d, transcript))), 0o600))
	return path
}

func handLaunchedSuccessor(d Deps, old store.Lane, found map[string]Peer, why string, taken map[string]bool, now time.Time) string {
	if !briefExists(old.Brief) {
		return fmt.Sprintf("a brief you write, since its brief %s is gone", old.Brief)
	}
	return writeSuccessorBrief(d, old, found, SuccessorName(old.Name, taken), why, "", carriedWork(d, old, now))
}

const abandonedAfter = 24 * time.Hour

func lastWorked(d Deps, lane store.Lane) time.Time {
	if lane.Placement != placedOnSprite {
		return lane.Heard
	}
	carried, err := d.Carried(lane.Name)
	if err != nil || carried.At.Before(lane.Heard) {
		return lane.Heard
	}
	return carried.At
}

func carriedWork(d Deps, lane store.Lane, now time.Time) string {
	if lane.Placement != placedOnSprite {
		return ""
	}
	ref := awake.WorkRefs + lane.Name
	carried, err := d.Carried(lane.Name)
	switch {
	case err != nil:
		return fmt.Sprintf("`%s` did not read: %v", ref, err)
	case carried.Commit == "":
		return fmt.Sprintf("`%s` holds no carried work", ref)
	}
	return fmt.Sprintf("`%s` holds its last carried work, %s from %s, %d min old", ref, carried.Commit[:min(12, len(carried.Commit))], pacific(carried.At), int(now.Sub(carried.At).Minutes()))
}

func withCarried(text, work string) string {
	if work == "" {
		return text
	}
	return strings.TrimSuffix(text, ".") + "; " + work + "."
}

func successorBrief(old store.Lane, name, why, partial, work string, d Deps, transcript string) (string, error) {
	body, err := os.ReadFile(old.Brief)
	if err != nil {
		return "", err
	}
	body = WithDuty(body, old.Standing)
	drive, checkout := d.Drive, d.Checkout
	start := handoffStart(old, d, transcript)
	before := []string{
		"Before any write (an edit, commit, push, ship, PR comment, or a record that claims work):",
		fmt.Sprintf("  1. From %s, run `%s lane-status %s --held`. Each line is a worktree %s made, with its branch, dirty files, push state, and PR; `no worktree found` means it made none.", checkout, filepath.Join(d.Bin, "drive.py"), old.Name, old.Name),
		fmt.Sprintf("  2. Run `cci who --drive %s --lane %s --json --fields lane,live,status,status_at`. It prints every live session bound as %s or answering under it as an alias, yours included, with its lane and harness status.", drive, old.Name, old.Name),
		fmt.Sprintf("  3. If it prints a live session whose lane is `%s` and whose status is not `idle`, %s has a turn running and may still write: write nothing, post `cci post --drive %s --lane %s --kind blocker --to root --text \"LIVE %s: status <status> since <status_at>\"` and stop. Never stop, signal or close that session or its terminal.", old.Name, old.Name, drive, name, old.Name),
		fmt.Sprintf("  4. An idle session or none writes nothing, even with its claude and shell still open in its worktree: continue in %s's worktree and branch from the state lane-status shows; the trees of the PRs it only named print without `--held`.", old.Name),
	}
	if old.Placement == placedOnSprite {
		start = fmt.Sprintf("Before anything else, read %s's records with `cci tail --drive %s --lane %s --oldest`.", old.Name, drive, old.Name)
		if old.Handoff != "" {
			start = fmt.Sprintf("Your handoff is `ccn doc show %s`; read it first, then %s's records with `cci tail --drive %s --lane %s --oldest`.", old.Handoff, old.Name, drive, old.Name)
		}
		ref := awake.WorkRefs + old.Name
		before = []string{fmt.Sprintf("%s's worktree and transcript stayed on its Sprite, which pushed each checkout's unpushed commits and uncommitted edits, its linked worktrees' included, to `%s` on that checkout's origin every 5 minutes they changed, on %s and on each repo it cloned; on %s at your launch, %s. In a fresh clone of each, run `git fetch origin %s:%s`; a repo where that ref is missing held no unpushed work. The commit's message names each branch it carried and that branch's tip, its first parent is the HEAD it had, and its tree is its working tree with the edits it never committed. Each `worktree <path> <commit>` line names a linked worktree's own commit, built the same way. A checkout stopped mid-rebase adds `rebase <branch> onto <onto> from <original head>` and the rebase's `done` and `todo` picks: its commit's first parent holds the commits rewritten so far and its tree the stopped pick's resolution, so finish the rebase by committing that tree on that parent and picking each `todo` commit after it. Start from that ref and its pushed branches and PRs, keep its edits off any branch until you commit them yourself, and redo only what neither holds.", old.Name, ref, d.Repo, d.Repo, work, ref, ref)}
	}
	owned := "none recorded"
	if len(old.PRs) > 0 {
		owned = strings.ReplaceAll(numberList(old.PRs), " ", ", ")
	}
	step := "all"
	if found := sliceStep.FindSubmatch(body); found != nil {
		step = string(found[1])
	}
	finished := fmt.Sprintf("If the handoff and %s's records show its deliverable is already complete, post `cci post --drive %s --lane %s --kind done --to root --text '<what it delivered>'` and stop.", old.Name, drive, name)
	if duty := dutyOf(old); duty != "" {
		finished = fmt.Sprintf("Your duty is standing (%s): loop on it, and end your session only as a `ROTATE` record asks, never with worker_done before one while your brief keeps its `Standing:` line.", duty)
	}
	place := "ccx: place=remote"
	switch {
	case MacDeskNeed(old.Name) != "":
		place = "ccx: place=local why=" + MacDeskNeed(old.Name)
	case DeskOnMac(body):
		place = string(macNeed.FindSubmatch(body)[0])
	case macBrief.Match(body) && dutyOf(old) == "" && !endsAtRotation(old):
		place = "ccx: place=local why=predecessor-worked-here"
	}
	lines := []string{
		"Replaces: " + old.Name,
		place,
		fmt.Sprintf("Register slice: lane=%s step=%s", name, step),
		fmt.Sprintf("You are %s and take over %s's work. The lane supervisor launched you because %s %s; %s is stood down on cci, so never SendMessage it.", name, old.Name, old.Name, why, old.Name),
		start,
		fmt.Sprintf("PRs you now own: %s.", owned),
		fmt.Sprintf("Ask, report and raise a blocker only with `cci post --drive %s --lane %s --kind ask|report|blocker`; Orca's `send` and `ask` carry the dispatch lifecycle alone.", drive, name),
		finished,
		"",
	}
	lines = append(lines, before...)
	lines = append(lines,
		"",
		fmt.Sprintf("When the deliverable is done, post `cci post --drive %s --lane %s --kind done --to root` before your final message; told to stand down after a task, post that `done` when the task is finished. On `ROTATE`, write your state to cc-notes and post `cci post --drive %s --lane %s --kind handoff --ccn <doc id> --re <the ROTATE record>`, %s.", drive, name, drive, name, afterHandoff(old)),
		"",
		string(body),
	)
	if partial != "" {
		lines = append([]string{partial}, lines...)
	}
	return strings.Join(lines, "\n"), nil
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
	if parked := pageText(page, "parked"); parked != "" && now.Before(must(store.Parse(parked))) {
		return
	}
	if until := parkedUntil(d.Transcript(peer.Row), now); now.Before(until) {
		do(s.SetLanePage(lane.Name, with(page, "parked", store.Stamp(until))))
		return
	}
	do(s.SetLanePage(lane.Name, nil))
	relaunch(s, d, lane, found, fmt.Sprintf("has made no tool call in the %d min since it was woken because %s", int(now.Sub(wokeAt).Minutes()), truncated(pageText(page, "why"), 240)), "", taken, now, out)
}

var lrWait = regexp.MustCompile(`(?:^|[\s/])lr wait (\d+)\b`)

func parkedUntil(transcript string, now time.Time) time.Time {
	var until time.Time
	if transcript == "" {
		return until
	}
	tasks, err := livetasks.Read(transcript, now)
	if err != nil {
		return until
	}
	for _, task := range tasks {
		if wait := lrWait.FindStringSubmatch(task.Command); task.Kind == "Bash" && wait != nil {
			until = newer(until, task.At.Add(time.Duration(must(strconv.Atoi(wait[1])))*time.Second))
		}
	}
	return until
}

func chaseUnread(s *store.Store, d Deps, found map[string]Peer, already []string, taken map[string]bool, now time.Time, out *Outcome) (woken []string, err error) {
	defer recovered(&err)
	forwardOwnerWords(s, d)
	do(s.KeepUnread(live...))
	held := map[string][]store.Unread{}
	for _, order := range must(s.Unread()) {
		held[order.Lane] = append(held[order.Lane], order)
	}
	for _, lane := range must(s.Lanes(live...)) {
		if len(held[lane.Name]) == 0 {
			continue
		}
		peer := found[lane.Name]
		read := newer(peer.LastCall, lane.Heard)
		do(s.ReadUnread(lane.Name, read))
		orders := slices.DeleteFunc(held[lane.Name], func(order store.Unread) bool { return order.At.Before(read.Truncate(time.Second)) })
		if slices.Contains(already, lane.Name) {
			do(s.MarkUnreadWoken(lane.Name))
		} else if wakeOnUnread(s, d, lane, peer, orders, now) {
			woken = append(woken, lane.Name)
		}
		orders = slices.DeleteFunc(orders, func(order store.Unread) bool { return !unreadOrder(order) })
		if isStanding(lane) && lane.Role != RoleMeta && len(orders) > 0 && now.Sub(orders[0].At) >= deskLostAfter {
			lost(s, d, lane, found, Verdict{Lost, fmt.Sprintf("is a standing desk that took none of its %d orders, %s, the oldest for %d min", len(orders), orderWords(orders), int(now.Sub(orders[0].At).Minutes()))}, taken, now, out)
			continue
		}
		pastDue := slices.DeleteFunc(slices.Clone(orders), func(order store.Unread) bool { return order.Raised || now.Sub(order.At) < unreadAfter })
		if len(pastDue) == 0 {
			continue
		}
		oldest := int(now.Sub(pastDue[0].At).Minutes())
		do(d.Post(Post{Kind: "go", To: []string{orcaDesk}, Text: fmt.Sprintf("%s: relay to %s: Orders addressed to you are unread: %s, the oldest for %d min. Read them with `cci tail --mail` and act on them.", orcaDesk, lane.Name, orderWords(pastDue), oldest)}))
		do(s.MarkUnreadRaised(lane.Name))
	}
	return woken, nil
}

func forwardOwnerWords(s *store.Store, d Deps) {
	for _, order := range must(s.Unread()) {
		lane, _ := laneOf(s, order.Lane)
		if order.Kind != "owner" || isLive(lane.State) {
			continue
		}
		to, ended := routeOf(s, lane.Name), fmt.Sprintf("retired (%s)", lane.RetiredBy)
		if lane.State == store.LaneHeld {
			to, ended = noticeTo(s, lane.Name), "was held"
		}
		if to != order.Author {
			do(d.Post(Post{Kind: "ask", To: []string{to}, Re: order.Seq, Text: fmt.Sprintf("%s's owner record %s reached %s after it %s, so it is yours to carry: read it with `cci show %s`.", order.Author, order.Name(), lane.Name, ended, strings.TrimPrefix(order.Name(), "#"))}))
		}
	}
}

func wakeOnUnread(s *store.Store, d Deps, lane store.Lane, peer Peer, records []store.Unread, now time.Time) bool {
	standing := isStanding(lane)
	due := slices.DeleteFunc(slices.Clone(records), func(record store.Unread) bool {
		patience := mailAfter
		if (standing && unreadOrder(record)) || lane.Placement == placedOnSprite {
			patience = wakeAfter
		}
		return record.Woke || now.Sub(record.At) < patience
	})
	if len(due) == 0 {
		return false
	}
	if why := unwakeable(lane, peer, due); why != "" {
		logDeclined(s, lane, why, due)
		return false
	}
	if !must(d.Wake(lane.Name, peer.Row, map[string]any{"why": fmt.Sprintf("records addressed to you are unread: %s.", orderWords(due))})) {
		return false
	}
	do(s.MarkUnreadWoken(lane.Name))
	return true
}

func unwakeable(lane store.Lane, peer Peer, due []store.Unread) string {
	switch {
	case !peer.Live:
		return "no live process"
	case peer.Row.Roster == "" && peer.Row.Dispatch == "":
		return "no dispatch or roster to reach it"
	case settledLane(lane) && !slices.ContainsFunc(due, func(record store.Unread) bool { return record.Kind == "owner" }):
		return "its last record ended it"
	}
	return ""
}

func logDeclined(s *store.Store, lane store.Lane, why string, due []store.Unread) {
	fresh := slices.DeleteFunc(slices.Clone(due), func(record store.Unread) bool { return record.Declined })
	if len(fresh) == 0 {
		return
	}
	slog.Warn("wake declined", "lane", lane.Name, "why", why, "records", orderWords(fresh))
	seqs := make([]int64, 0, len(fresh))
	for _, record := range fresh {
		seqs = append(seqs, record.Seq)
	}
	do(s.MarkUnreadDeclined(lane.Name, seqs))
}

func unreadOrder(record store.Unread) bool {
	return record.Kind == "review" || record.Kind == "owner" || slices.Contains(askingKinds, record.Kind)
}

func orderWords(orders []store.Unread) string {
	words := make([]string, 0, len(orders))
	for _, order := range orders {
		words = append(words, fmt.Sprintf("%s's %s %s", order.Author, order.Kind, order.Name()))
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
	retired := map[string]store.Lane{}
	for _, lane := range must(s.Lanes(store.LaneRetired)) {
		retired[lane.Name] = lane
	}
	rows := map[string]store.Lane{}
	for _, lane := range must(s.Lanes(live...)) {
		rows[lane.Name] = lane
	}
	var unowned, ended, wanted []int
	left := map[int]string{}
	quiet := map[string][]int{}
	for name, prs := range must(s.Owners()) {
		var open []int
		for _, n := range prs {
			if !holds[n] {
				open = append(open, n)
			}
		}
		if lane, gone := retired[name]; gone {
			ended = append(ended, open...)
			wanted = append(wanted, open...)
			if endedAlone(lane) {
				for _, n := range prs {
					left[n] = name
				}
				wanted = append(wanted, prs...)
			}
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
	ended = handOnEnded(s, d, ended, left, states)
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

func handOnEnded(s *store.Store, d Deps, ended []int, left map[int]string, states map[int]string) []int {
	var leaving []int
	for n := range left {
		if states[n] != Closed {
			leaving = append(leaving, n)
		}
	}
	if len(leaving) == 0 {
		return ended
	}
	slices.Sort(leaving)
	own := ours(s, d, leaving)
	kept := own
	if to := defaultOwner(s); to != "" && len(own) > 0 {
		do(s.Own(to, own))
		from := make([]string, 0, len(own))
		for _, n := range own {
			from = append(from, left[n])
		}
		slices.Sort(from)
		do(d.Post(handedOn(to, slices.Compact(from), own)))
		kept = nil
	}
	return slices.DeleteFunc(ended, func(n int) bool { return slices.Contains(leaving, n) && !slices.Contains(kept, n) })
}

const clockGate = "clock:"

func clocksRanOut(s *store.Store, now time.Time) {
	for _, lane := range must(s.Lanes(live...)) {
		ran := slices.ContainsFunc(strings.Fields(lane.Awaits), func(gate string) bool {
			stamp, ok := strings.CutPrefix(gate, clockGate)
			return ok && !now.Before(must(store.Parse(stamp)))
		})
		if !ran {
			continue
		}
		why := fmt.Sprintf("you are overdue: the span your blocked record #%d named has run out and you have posted nothing since; read what that wait was for and post what happened", lane.Waiting)
		do(s.SetLaneWaiting(lane.Name, 0, now, ""))
		do(s.SetLanePage(lane.Name, map[string]any{"topic": fmt.Sprintf("clock:%d", lane.Waiting), "why": why, "text": why}))
	}
}

func waitsOnOwn(lane store.Lane, prs []int) bool {
	return len(lane.Blockers()) == 0 && slices.ContainsFunc(lane.AwaitedPRs(), func(n int) bool { return slices.Contains(prs, n) })
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

func keepDuties(s *store.Store, d Deps, found map[string]Peer, taken map[string]bool, now time.Time, out *Outcome) {
	heard := must(s.Heard())
	heads := map[string]store.Lane{}
	for _, lane := range must(s.AllLanes()) {
		if dutyOf(lane) == "" {
			continue
		}
		if head, ok := newest(s, lane.Name); ok && head.State == store.LaneRetired && dutyOf(head) != "" {
			heads[head.Name] = head
		}
	}
	for _, name := range slices.Sorted(maps.Keys(heads)) {
		head := heads[name]
		last := head.Heard
		peer := peerOf(found, name)
		if peer != nil {
			last = newer(peer.Seen, head.Heard)
		}
		if head.RetiredBy != retiredWorkerDone && alive(peer, last, now) || now.Sub(head.Since) < unownedAfter {
			continue
		}
		if at, ok := heard[head.RetiredBy]; ok && succeeds(head.RetiredBy, name) && now.Sub(at) < StaleAfter {
			continue
		}
		relaunch(s, d, head, found, fmt.Sprintf("was retired by %s with no successor", or(head.RetiredBy, "nobody")), "", taken, now, out)
	}
}

func spentRelaunches(s *store.Store, d Deps, lane store.Lane, found map[string]Peer, why string, taken map[string]bool, now time.Time) string {
	if spriteFault(s, why) {
		return ""
	}
	if !isStanding(lane) {
		if lane.Predecessor != "" && lane.Reason != "" && !spriteFault(s, lane.Reason) {
			return fmt.Sprintf("the supervisor already relaunched it once, when %s %s, and now it %s; brief %s", lane.Predecessor, lane.Reason, why, lane.Brief)
		}
		return ""
	}
	strikes := 0
	seen := map[string]bool{}
	for at, known := lane, true; known && !seen[at.Name] && at.Predecessor != "" && at.Reason != "" && now.Sub(at.Since) < deskStrikeWindow; at, known = laneOf(s, at.Predecessor) {
		seen[at.Name] = true
		if !spriteFault(s, at.Reason) {
			strikes++
		}
	}
	if strikes < deskStrikes {
		return ""
	}
	return fmt.Sprintf("the supervisor relaunched it %d times within %d min and each one died or never started, and now it %s; read why before launching its successor from %s", strikes, int(deskStrikeWindow.Minutes()), why, handLaunchedSuccessor(d, lane, found, why, taken, now))
}

func spriteFault(s *store.Store, why string) bool {
	if strings.Contains(why, launchFailed) {
		return false
	}
	if strings.Contains(why, storageNotReady) || strings.Contains(why, notAnswering) {
		return true
	}
	return strings.Contains(why, orcaHostGone) && len(must(s.Escalations(spriteCapacity))) == 0
}

func unsaid(s *store.Store, orders []owner.Row) []owner.Row {
	return slices.DeleteFunc(orders, func(order owner.Row) bool {
		cursor := orderSaidCursor + order.ID + ":" + order.Status
		named := must(s.Cursor(cursor)) != 0
		do(s.Advance(cursor, 1))
		return named
	})
}

func orphanOrders(s *store.Store, d Deps, now time.Time) {
	sweep := now.Unix() / int64(orphanSweep.Seconds())
	if last := must(s.Cursor(orphanCursor)); sweep <= last {
		return
	}
	do(s.Advance(orphanCursor, sweep))
	orders, err := d.Orders(s)
	if errors.Is(err, ErrUnread) {
		return
	}
	do(err)
	var ids, lines []string
	for _, order := range owner.Only(orders, owner.Open) {
		cursor := orphanedCursor + order.ID
		since := must(s.Cursor(cursor))
		if order.Lane == "" || must(s.Cursor(cursor+":said")) != 0 {
			continue
		}
		if since == 0 {
			do(s.Advance(cursor, now.Unix()))
			continue
		}
		if now.Sub(time.Unix(since, 0)) < orphanAfter {
			continue
		}
		do(s.Advance(cursor+":said", 1))
		ids = append(ids, order.ID)
		lines = append(lines, fmt.Sprintf("%s, last held by %s: %s; relaunch with `lr launch lane --order owner:%s`", order.ID, order.Lane, truncated(order.Text, 120), order.ID))
	}
	if len(ids) == 0 {
		return
	}
	escalateTo(s, d, "orders:"+strings.Join(ids, ","), fmt.Sprintf("%d of his open orders lost their lane %d minutes or more ago, with no delivered or parked mark: %s.", len(ids), int(orphanAfter.Minutes()), strings.Join(lines, "; ")), OwnerRelay, now)
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
	recommendedSweep(s, d, now)
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
		open, progressing := unsaid(s, owner.Only(orders, owner.Open)), unsaid(s, owner.Only(orders, owner.InProgress))
		var said []string
		if len(open) > 0 {
			said = append(said, fmt.Sprintf("%d owner asks newly open, orders with no live lane, no open PR and no landed or carried record and questions nobody answered: %s. Answer each question and mark its row, restart each order, or mark a row that orders nothing no-order with `mcp__plugin_long-running_lr__owner_mark`; a lane delivering an order is launched with `lr launch lane --order owner:<row>`.", len(open), owner.Tokens(open)))
		}
		if len(progressing) > 0 {
			said = append(said, fmt.Sprintf("%d owner orders newly in progress, landed or carried with no live lane and no delivered proof: %s. Post `cci post --kind report --attr delivered=owner:<row>` once he has what the row asked for, or restart the order.", len(progressing), owner.Tokens(progressing)))
		}
		if len(said) > 0 {
			do(d.Post(Post{Kind: "report", To: []string{OwnerRelay}, Text: strings.Join(said, " ") + " Each row is named once; `lr owner audit` lists them all."}))
		}
		if parked := owner.Only(orders, owner.Parked); hour%2 == 0 && len(parked) > 0 {
			parkedNote(s, d, parked)
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

func parkedNote(s *store.Store, d Deps, parked []owner.Row) {
	for _, lane := range must(s.Lanes(live...)) {
		if base, _ := family(lane.Name); base != progressDesk {
			continue
		}
		lines := make([]string, len(parked))
		for i, row := range parked {
			lines[i] = row.Line()
		}
		do(d.Post(Post{Kind: "note", To: []string{lane.Name}, Text: fmt.Sprintf("%d owner orders are parked behind a hold or a part left open, with no live lane and no open PR, for your update: %s", len(parked), strings.Join(lines, "; "))}))
		return
	}
}

func recommendedSweep(s *store.Store, d Deps, now time.Time) {
	if d.BuildRecommendedOrder == "" {
		return
	}
	var woken []string
	built := must(s.BranchCards())
	for _, lane := range must(s.Lanes(live...)) {
		cards := slices.DeleteFunc(card.FindAllString(lane.Awaits, -1), func(found string) bool {
			return slices.ContainsFunc(built, func(b store.BranchCard) bool { return b.Lane == lane.Name && b.Card == found })
		})
		if lane.Waiting == 0 || len(cards) == 0 || len(lane.PRs) > 0 || slices.Contains(strings.Fields(lane.Awaits), noRecommendation) {
			continue
		}
		why := fmt.Sprintf("%s stands, so no lane parks on a pick that recommends an option: build the recommended option of %s as a branch, never a PR, and carry on. If the card recommends none, re-post blocked naming it with %s. If he has answered it or it is off his board, build nothing and carry on.", d.BuildRecommendedOrder, strings.Join(cards, ", "), noRecommendation)
		do(s.PutBranchCards(lane.Name, cards))
		do(s.SetLaneWaiting(lane.Name, 0, now, ""))
		do(s.SetLanePage(lane.Name, map[string]any{"topic": d.BuildRecommendedOrder, "why": why, "text": why, "prs": append([]int{}, lane.PRs...)}))
		woken = append(woken, fmt.Sprintf("%s (%s)", lane.Name, strings.Join(cards, ", ")))
	}
	if meta := must(MetaDesk(s)); len(woken) > 0 && meta != Root {
		do(d.Post(Post{Kind: "report", To: []string{meta}, Text: fmt.Sprintf("%s: woke these lanes from a pick to build its recommended option: %s.", d.BuildRecommendedOrder, strings.Join(woken, "; "))}))
	}
}

func openAnswered(s *store.Store, d Deps, found map[string]Peer, taken map[string]bool, now time.Time, out *Outcome) {
	branches := map[string][]store.BranchCard{}
	for _, built := range must(s.BranchCards()) {
		branches[built.Lane] = append(branches[built.Lane], built)
	}
	meta := must(MetaDesk(s))
	var ordered []string
	for _, name := range slices.Sorted(maps.Keys(branches)) {
		built := branches[name]
		if slices.ContainsFunc(built, func(c store.BranchCard) bool { return c.Answered.IsZero() }) {
			continue
		}
		cards := make([]string, len(built))
		for i, c := range built {
			cards[i] = c.Card
		}
		answered := strings.Join(cards, ", ")
		holder, _ := newest(s, name)
		if built[0].Ordered.IsZero() {
			why := fmt.Sprintf("the owner answered every card your branch was built on (%s), so nothing holds its PR: build his picks on it and open the PR now with submit-pr", answered)
			if holder.State == store.LaneRetired {
				relaunch(s, d, holder, found, why, "", taken, now, out)
				holder, _ = newest(s, holder.Name)
			} else {
				do(s.SetLaneWaiting(holder.Name, 0, now, ""))
				do(s.SetLanePage(holder.Name, map[string]any{"topic": "branch:" + strings.Join(cards, ","), "why": why, "text": why}))
			}
			do(s.OrderBranch(name, holder.Name, holder.PRs, now))
			ordered = append(ordered, fmt.Sprintf("%s (%s)", holder.Name, answered))
			continue
		}
		if slices.ContainsFunc(holder.PRs, func(n int) bool { return !slices.Contains(built[0].Held, n) }) {
			do(s.DropBranch(name))
			continue
		}
		if now.Sub(built[0].Ordered) < branchPRDue || meta == Root {
			continue
		}
		do(d.Post(Post{Kind: "defect", To: []string{meta}, LaneRef: holder.Name, Text: fmt.Sprintf("branch:%s: the owner answered every card its branch was built on (%s), and %d minutes after the supervisor told it to open the PR it owns no new one. Open that PR, or find what blocks it; this never goes to the owner as a card.", holder.Name, answered, int(branchPRDue.Minutes()))}))
		do(s.DropBranch(name))
	}
	if len(ordered) > 0 && meta != Root {
		do(d.Post(Post{Kind: "report", To: []string{meta}, Text: "Told these lanes to open the PR of a branch whose every card the owner answered: " + strings.Join(ordered, "; ") + "."}))
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
		lines = append(lines, fmt.Sprintf("%s %s (oldest %s)", lane, strings.Join(counts, ", "), oldest.Name()))
	}
	return strings.Join(lines, "; ")
}
