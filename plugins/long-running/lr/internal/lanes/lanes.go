package lanes

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Self            = "supervisor"
	Root            = "root"
	OwnerRelay      = "owner-relay"
	orcaDesk        = "orca-desk"
	progressDesk    = "progress-desk"
	prUnblock       = "pr-unblock"
	RoleMeta        = "meta"
	roleDesk        = "desk"
	roleRelease     = "release"
	runner          = "desk-runner"
	mainLane        = "main"
	CursorName      = "lanes"
	launchKey       = "supervisor-launch:"
	SpriteLimit     = "Maximum concurrent sprites limit"
	spriteCapacity  = "sprite-capacity"
	launchFailed    = "LAUNCH-FAILED: "
	owedLaunch      = "launch-owed:"
	hourCursor      = "lanes.hour"
	orderSaidCursor = "lanes.order-said."
	owedSubject     = "owed-deletions"
	lateSubject     = "owed-deletions-past-due"
	kindSpawn       = "spawn"
	kindRequest     = "decide"
	kindLanded      = "landed"
	unreadMark      = " down or hand its PRs on, but it "
	turnDied        = "died"
	NeedsWork       = "needs-work"
	Broken          = "broken"
	Waiting         = "waiting"
	Closed          = "closed"
	LaneEnv         = "CLAUDE_LONG_RUNNING_LANE"
	lrLaneEnv       = "LR_LANE"
)

const (
	retiredWorkerDone = "worker-done"
	retiredSilent     = "silent"
	retiredStopped    = "stand-down"
	retiredMailTopic  = "retired-mail"
	boardLink         = "board-link"
	CardWakeTopic     = "card-wake:"
	CardAttr          = "card"
	unheardDone       = "reported worker_done, but none of its records ever reached the bus"
	workerSucceeded   = "succeeded"
	agentLive         = "live"
	agentExited       = "exited"
	agentUnverifiable = "unverifiable"
	hostUnavailable   = "host_unavailable"
	remoteHost        = "remote"
)

var sessionIdentity = []string{"CCI_AGENT_ID", "CCI_LANE", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}

func Identity(lane string) []string {
	env := make([]string, 0, 2+len(sessionIdentity))
	env = append(env, LaneEnv+"="+lane, lrLaneEnv+"="+lane)
	for _, name := range sessionIdentity {
		env = append(env, name+"=")
	}
	return env
}

func BoundLane(ctx context.Context, runner shell.Runner) (string, error) {
	if lane := os.Getenv(LaneEnv); lane != "" {
		return lane, nil
	}
	me, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "me"}})
	if err != nil {
		return "", err
	}
	for word := range strings.FieldsSeq(me.Stdout) {
		if lane, found := strings.CutPrefix(word, "lane="); found {
			return lane, nil
		}
	}
	line, _, _ := strings.Cut(me.Stdout, "\n")
	if fields := strings.Fields(line); len(fields) > 1 && fields[1] == Root {
		return Root, nil
	}
	return "", nil
}

func Adopt(lane string) error {
	identity := Identity(lane)
	errs := make([]error, 0, len(identity))
	for _, pair := range identity {
		name, value, _ := strings.Cut(pair, "=")
		errs = append(errs, os.Setenv(name, value))
	}
	return errors.Join(errs...)
}

const (
	StaleAfter       = store.StaleAfter
	launchWindow     = 25 * time.Minute
	unownedAfter     = 10 * time.Minute
	unreadAfter      = 10 * time.Minute
	deskLostAfter    = 30 * time.Minute
	wakeAfter        = 30 * time.Second
	mailAfter        = unreadAfter / 2
	strikeWindow     = 2 * StaleAfter
	deskStrikes      = 3
	deskStrikeWindow = time.Hour
	PageAnswer       = 2 * StaleAfter
	liveWithin       = 4 * StaleAfter
	branchPRDue      = 15 * time.Minute
)

var (
	ErrUnread = errors.New("source did not answer")
	Verbs     = []string{"stand-down", "park", "unpark", "owner", "release", "done", "revive", "step"}
	live      = []store.LaneState{store.LaneLaunching, store.LaneDesired}

	helperRoles = []string{"helper", "reader", "watch", "export", "evidence", "handoff", "comms", "triage"}
	carries     = []string{"head", "opened"}
	claims      = []string{"blocked", "ready"}
	builders    = []string{"build", "fix"}
	waits       = []string{"ask", "blocked", "blocker"}
	replies     = []string{"answer", "decide"}

	askingKinds    = []string{"ask", "lr.ask", kindRequest}
	machineAuthors = []string{Self, runner, boardWatch, "pr-read", "prompt-watch", "stall-watch"}
	machineKinds   = []string{"head", "state", "opened", "heartbeat", "github", "review", "claim", kindSpawn, "board.card", "queue"}

	settled      = []string{"handoff", "done", "stand-down", "stopped"}
	unprovenEnds = []string{retiredStopped, retiredWorkerDone, retiredSilent, kindLanded}
	endsAlone    = []string{"done", retiredStopped, retiredWorkerDone, kindLanded}
	laneEnds     = []string{"done", "stopped", "fix-live"}
	shipped      = []string{"release", "applied", "fix-live"}

	settledDispatch = []string{"completed", "failed"}
	agentVerdicts   = []string{agentLive, agentExited, agentUnverifiable}
	standingRoles   = []string{roleDesk, RoleMeta, roleRelease}

	numbered      = regexp.MustCompile(`^(.+)-(\d{1,2})$`)
	handedOff     = regexp.MustCompile(`\bhandoff ([\w.-]+): doc ([0-9a-f]{7,40})\b`)
	workerDone    = regexp.MustCompile(`^OUTCOME \S+ ([\w.-]+): worker_done succeeded\b`)
	workerEnded   = regexp.MustCompile(`^OUTCOME \S+ ([\w.-]+): worker_done \w+\b`)
	launchRefused = regexp.MustCompile(`(?s)^(LAUNCH-FAILED|LAUNCH-DUPLICATE|LAUNCH-EXPIRED|UNVERIFIABLE|SPRITE-UNVERIFIABLE) (?:desk-lane-[\w.-]+/)?R\d+(?:@\w+)? ([\w.-]+): (.*)`)
	withdrawnWork = regexp.MustCompile(`^WITHDRAWN \S+ ([\w.-]+): by \S+; dispatch `)
	launchStage   = regexp.MustCompile(`^STAGE R\d+(?:@\w+)? ([\w.-]+): `)
	standDownList = regexp.MustCompile(`(?i)\bstand[ -]?down\s+([^.(;:\n]+)`)
	laneWord      = regexp.MustCompile(`[\w.-]+`)
	laneAssigned  = regexp.MustCompile(`\blane=([\w.-]*\w)`)
	card          = regexp.MustCompile(`\bcard-[\w.-]*\w`)
	parked        = regexp.MustCompile(`\bPARKED ([\w.-]+):`)
	prMention     = regexp.MustCompile(`#(\d+)\b`)
	stoppedLine   = regexp.MustCompile(`^(?:\d{1,2}:\d{2} [AP]M )?STOPPED\b`)
	withdrawOrder = regexp.MustCompile(`\borca-desk: withdraw (\S+)`)
	lastRead      = regexp.MustCompile(`\blast-read=#?(\d+)\b`)
)

var DeskLane = regexp.MustCompile(`^(?:board-refresher|board-watch|card-mirror|owner-relay|desk-runner|supervisor|fidelity|finder|grader|judge|go-sweep|progress-desk|meta-desk|root$)`)

const noRecommendation = "no-recommendation"

func Canonical(drive, lane string) string {
	var initials strings.Builder
	for word := range strings.SplitSeq(drive, "-") {
		if word != "" {
			initials.WriteByte(word[0])
		}
	}
	return strings.TrimPrefix(lane, initials.String()+"-")
}

func Resolve(ctx context.Context, drive, name string) (string, error) {
	name = Canonical(drive, name)
	if client.Stem(name) != name {
		return name, nil
	}
	switch reach, err := client.Resolve(ctx, drive, name); {
	case errors.Is(err, client.ErrNoLaneRegistry):
		return name, nil
	case err != nil:
		return "", err
	case reach.Lane != "":
		return reach.Lane, nil
	}
	generations, err := client.Generations(ctx, drive, name)
	if err != nil || len(generations) == 0 {
		return name, err
	}
	return slices.MaxFunc(generations, func(a, b string) int {
		_, older := family(a)
		_, newer := family(b)
		return cmp.Compare(older, newer)
	}), nil
}

func resolveMember(ctx context.Context, s *store.Store, drive, name string, newestIfNoneLive bool) (lane string, err error) {
	defer recovered(&err)
	name = Canonical(drive, name)
	if _, registered := laneOf(s, name); registered || client.Stem(name) != name {
		return name, nil
	}
	var members, running []string
	newestMember, newestNumber := "", 0
	for _, row := range must(s.AllLanes()) {
		stem, number := family(row.Name)
		if stem != name {
			continue
		}
		members = append(members, fmt.Sprintf("%s %s", row.Name, row.State))
		if isLive(row.State) {
			running = append(running, row.Name)
		}
		if number > newestNumber {
			newestMember, newestNumber = row.Name, number
		}
	}
	switch {
	case len(members) == 0:
		return Resolve(ctx, drive, name)
	case len(running) == 1:
		return running[0], nil
	case len(running) == 0 && newestIfNoneLive:
		return newestMember, nil
	case len(running) == 0:
		return "", fmt.Errorf("%s names no live lane (%s), so name the lane itself", name, strings.Join(members, ", "))
	default:
		return "", fmt.Errorf("%s names %d live lanes (%s), so name the lane itself", name, len(running), strings.Join(members, ", "))
	}
}

func Stem(name string) string {
	stem, _ := family(name)
	return stem
}

func Holder(s *store.Store, drive, name string) (holder string, err error) {
	defer recovered(&err)
	name = Canonical(drive, name)
	if lane, found := newest(s, name); found && lane.State != store.LaneRetired {
		return lane.Name, nil
	}
	holder, newestNumber := name, 0
	for _, member := range liveFamily(s, name) {
		if _, number := family(member); number > newestNumber {
			holder, newestNumber = member, number
		}
	}
	return holder, nil
}

func Running(s *store.Store, drive, name string) (holder string, err error) {
	defer recovered(&err)
	name = Canonical(drive, name)
	if lane, found := newest(s, name); found {
		if isLive(lane.State) {
			return lane.Name, nil
		}
		return Root, nil
	}
	if members := liveFamily(s, name); len(members) == 1 {
		return members[0], nil
	}
	return Root, nil
}

func liveFamily(s *store.Store, name string) []string {
	var members []string
	for _, lane := range must(s.Lanes(live...)) {
		if Stem(lane.Name) == Stem(name) {
			members = append(members, lane.Name)
		}
	}
	return members
}

func ChangeOwner(s *store.Store, drive, pusher string, pr int) (string, error) {
	owner, err := Running(s, drive, pusher)
	if err != nil || owner != Root {
		return owner, err
	}
	named, err := s.OwnerOf([]int{pr})
	if err != nil {
		return "", err
	}
	return Running(s, drive, named[pr])
}

func ItemOwner(s *store.Store, drive, pusher string, pr int) (owner string, err error) {
	defer recovered(&err)
	if owner = must(ChangeOwner(s, drive, pusher, pr)); owner != Root {
		return owner, nil
	}
	named := must(s.OwnerOf([]int{pr}))
	for _, name := range []string{pusher, named[pr]} {
		if lane, found := newest(s, Canonical(drive, name)); found && lane.State == store.LaneRetired && restedOnItsOwnEnd(lane) {
			return lane.Name, nil
		}
	}
	return Root, nil
}

func family(name string) (string, int) {
	if found := numbered.FindStringSubmatch(name); found != nil {
		n, _ := strconv.Atoi(found[2])
		return found[1], n
	}
	return name, 1
}

func succeeds(name, old string) bool {
	base, n := family(old)
	other, later := family(name)
	return other == base && later > n
}

func SuccessorName(name string, taken map[string]bool) string {
	base, n := family(name)
	for taken[fmt.Sprintf("%s-%d", base, n+1)] {
		n++
	}
	return fmt.Sprintf("%s-%d", base, n+1)
}

func isLive(state store.LaneState) bool {
	return slices.Contains(live, state)
}

func numberList(prs []int) string {
	words := make([]string, 0, len(prs))
	for _, n := range prs {
		words = append(words, "#"+strconv.Itoa(n))
	}
	return strings.Join(words, " ")
}

type failure struct{ err error }

func must[T any](value T, err error) T {
	if err != nil {
		panic(failure{err})
	}
	return value
}

func do(err error) {
	if err != nil {
		panic(failure{err})
	}
}

func recovered(err *error) {
	switch caught := recover().(type) {
	case nil:
	case failure:
		*err = caught.err
	default:
		panic(caught)
	}
}

func newest(s *store.Store, name string) (store.Lane, bool) {
	lane, found := laneOf(s, name)
	if !found {
		return store.Lane{}, false
	}
	seen := map[string]bool{name: true}
	for lane.State == store.LaneRetired && !seen[lane.RetiredBy] {
		after, ok := laneOf(s, lane.RetiredBy)
		if !ok {
			break
		}
		lane = after
		seen[lane.Name] = true
	}
	return lane, true
}

func laneOf(s *store.Store, name string) (store.Lane, bool) {
	lane, found, err := s.Lane(name)
	do(err)
	return lane, found
}

func Route(s *store.Store, name string) (route string, err error) {
	defer recovered(&err)
	return routeOf(s, name), nil
}

func routeOf(s *store.Store, name string) string {
	lane, found := newest(s, name)
	if !found {
		return name
	}
	if lane.State == store.LaneRetired {
		return Root
	}
	return lane.Name
}

func NoticeTo(s *store.Store, name string) (to string, err error) {
	defer recovered(&err)
	return noticeTo(s, name), nil
}

func noticeTo(s *store.Store, name string) string {
	if !isMeta(s, name) {
		return Root
	}
	lane, _ := laneOf(s, name)
	if lane.Role == RoleMeta {
		return OwnerRelay
	}
	if head, found := newest(s, lane.Launcher); found && isLive(head.State) {
		return head.Name
	}
	if lane.Role == roleDesk {
		return Root
	}
	for _, row := range must(s.AllLanes()) {
		if row.Role == RoleMeta && isLive(row.State) {
			return row.Name
		}
	}
	return Root
}

func isMeta(s *store.Store, name string) bool {
	for seen := map[string]bool{}; !seen[name]; {
		seen[name] = true
		lane, found := laneOf(s, name)
		if !found {
			return false
		}
		if lane.Role == RoleMeta {
			return true
		}
		name = lane.Launcher
	}
	return false
}

func AdoptMeta(s *store.Store, desk string, helpers []string) (adopted int, unknown []string, err error) {
	defer recovered(&err)
	if _, found := laneOf(s, desk); !found {
		return 0, nil, fmt.Errorf("no lane named %s", desk)
	}
	do(s.SetLaneRole(desk, RoleMeta))
	for _, name := range helpers {
		lane, found := laneOf(s, name)
		switch {
		case !found:
			unknown = append(unknown, name)
		case name != desk && lane.Launcher == "":
			do(s.SetLaneLauncher(name, desk))
			adopted++
		}
	}
	return adopted, unknown, nil
}

func MetaDesk(s *store.Store) (desk string, err error) {
	defer recovered(&err)
	for _, lane := range must(s.Lanes(live...)) {
		if lane.Role == RoleMeta {
			return lane.Name, nil
		}
	}
	return Root, nil
}

func Owners(s *store.Store, name string) (owners []string, err error) {
	defer recovered(&err)
	meta := must(MetaDesk(s))
	if lane, found := laneOf(s, name); found && lane.Launcher != "" && lane.Launcher != meta {
		return []string{lane.Launcher, meta}, nil
	}
	return []string{meta}, nil
}

func retire(s *store.Store, name, by string, at time.Time) {
	do(s.SetLaneState(name, store.LaneRetired, at, by, ""))
	do(s.SetLaneWaiting(name, 0, time.Time{}, ""))
	do(s.SetLanePage(name, nil))
	must(s.ReleaseWorker(name))
}

func ours(s *store.Store, d Deps, prs []int) []int {
	if len(prs) == 0 {
		return nil
	}
	own := must(d.Ours(s, prs))
	do(s.Disown(slices.DeleteFunc(slices.Clone(prs), func(n int) bool { return slices.Contains(own, n) })))
	return own
}

func defaultOwner(s *store.Store) string {
	member := heirOf(s, prUnblock)
	if lane, found := laneOf(s, member); found && isLive(lane.State) {
		return member
	}
	return ""
}

func endedAlone(lane store.Lane) bool {
	return slices.Contains(endsAlone, lane.RetiredBy) && !restedOnItsOwnEnd(lane) && (dutyOf(lane) == "" || endedItsDuty(lane))
}

func endedItsDuty(lane store.Lane) bool {
	return lane.RetiredBy == retiredStopped || lane.RetiredBy == "done"
}

func handedOn(to string, from []string, prs []int) Post {
	return Post{Kind: "report", To: []string{Root}, PRs: prs, Text: fmt.Sprintf("HANDED ON %s to %s, the default owner, as `%s` would: %s ended naming no successor. Name another owner with `%s`.", numberList(prs), to, Request{Verb: "owner", Lane: to, PRs: prs}.Command(), strings.Join(from, ", "), Request{Verb: "owner", Lane: "<lane>", PRs: prs}.Command())}
}

func hold(s *store.Store, name, why string, at time.Time) {
	do(s.SetLaneState(name, store.LaneHeld, at, "", why))
	must(s.ReleaseWorker(name))
}

func neverStarted(lane store.Lane) bool {
	return lane.State == store.LaneHeld && strings.HasPrefix(lane.Reason, launchFailed) && lane.Heard.IsZero()
}

type owing struct {
	name     string
	rows     []deletions.Owed
	orders   []owner.Row
	promises []Promise
	err      error
	notes    error
}

func owingOf(s *store.Store, d Deps, name string) (owing, bool) {
	one, owes := deletionsOwed(s, d, name)
	if owes {
		return one, true
	}
	if one.orders, one.err = carriedOrders(s, d, name); one.err != nil || len(one.orders) > 0 {
		return one, true
	}
	if one.promises, one.err = promisedBy(d, name); one.err != nil || len(one.promises) > 0 {
		return one, true
	}
	one.notes = notesOwed(s, d, name)
	return one, one.notes != nil
}

func deletionsOwed(s *store.Store, d Deps, name string) (owing, bool) {
	listed, err := d.Owed()
	if err != nil {
		return owing{name: name, err: err}, true
	}
	owned := slices.DeleteFunc(listed, func(row deletions.Owed) bool {
		head, found := newest(s, row.Owner)
		return !found || head.Name != name
	})
	rows, err := d.Present(owned)
	return owing{name: name, rows: rows, err: err}, err != nil || len(rows) > 0
}

func carriedOrders(s *store.Store, d Deps, name string) ([]owner.Row, error) {
	lane, _ := laneOf(s, name)
	carried := must(BriefOf(lane.Brief)).Orders
	if len(carried) == 0 {
		return nil, nil
	}
	rows, err := d.Undelivered()
	return slices.DeleteFunc(rows, func(row owner.Row) bool { return !slices.Contains(carried, row.ID) }), err
}

func overdue(s *store.Store, d Deps, now time.Time) {
	late, err := d.Owed()
	if err == nil {
		late, err = d.Present(slices.DeleteFunc(late, func(row deletions.Owed) bool { return !row.Overdue(now) }))
	}
	if err != nil {
		if must(s.Escalate(lateSubject, err.Error(), Root, now)) {
			do(d.Post(Post{Kind: "blocker", To: []string{Root}, Text: fmt.Sprintf("The past-due owed-deletion check did not run: %v", err)}))
		}
		return
	}
	for _, row := range late {
		to := noticeTo(s, row.Owner)
		if head, found := newest(s, row.Owner); found && isLive(head.State) {
			to = head.Name
		}
		if must(s.Escalate(lateSubject, row.ID, to, now)) {
			do(d.Post(Post{Kind: "blocker", To: []string{to}, LaneRef: row.Owner, Text: fmt.Sprintf("%s is past due with its code still on trunk. Delete the code, or change its due: label with `ccn task edit` and name the decision that moves the date.", row)}))
		}
	}
}

func (out *folded) owesNothing(s *store.Store, name string, fresh bool, at time.Time) bool {
	if fresh {
		return true
	}
	one, owes := owingOf(s, out.deps, name)
	if owes {
		hold(s, name, one.owes(), at)
		out.owing = append(out.owing, one)
	}
	return !owes
}

func refusedOwing(s *store.Store, d Deps, name string, now time.Time) bool {
	one, owes := owingOf(s, d, name)
	if owes {
		hold(s, name, one.owes(), now)
		one.post(s, d)
	}
	return owes
}

func (o owing) owes() string {
	if o.notes != nil {
		return "its cc-notes did not reach origin: " + o.notes.Error()
	}
	if o.err != nil {
		return "what it owes did not read: " + o.err.Error()
	}
	var owed []string
	if len(o.rows) > 0 {
		rows := make([]string, len(o.rows))
		for i, row := range o.rows {
			rows[i] = row.String()
		}
		owed = append(owed, fmt.Sprintf("it owes %d deletions whose code is still on trunk: %s", len(o.rows), strings.Join(rows, "; ")))
	}
	if len(o.orders) > 0 {
		owed = append(owed, fmt.Sprintf("it was assigned %d owner orders with no delivered proof: %s", len(o.orders), owner.Tokens(o.orders)))
	}
	if len(o.promises) > 0 {
		owed = append(owed, "its cards promise him something by a clock, with no delivered proof: "+promiseCards(o.promises))
	}
	return strings.Join(owed, ", and ")
}

func (o owing) post(s *store.Store, d Deps) {
	if o.notes != nil {
		do(d.Post(Post{Kind: "blocker", To: []string{noticeTo(s, o.name), o.name}, LaneRef: o.name, Text: fmt.Sprintf("%s would retire, but %s. The supervisor holds %s, never relaunched or retired, and tries again when it posts. Standing it down by order accepts leaving those notes on its Sprite.", o.name, o.owes(), o.name)}))
		return
	}
	var remedies []string
	if o.err != nil || len(o.rows) > 0 {
		remedies = append(remedies, "delete the code, or hand each task to a live lane with `ccn task edit <id> --assignee <lane>`")
	}
	if o.err != nil || len(o.orders) > 0 {
		remedies = append(remedies, "deliver each order and post `cci post --kind report --attr delivered=owner:<row>` once he has it")
	}
	if o.err != nil || len(o.promises) > 0 {
		remedies = append(remedies, "deliver what each card promised and post `cci post --kind report --attr delivered=card:<card>` once he has it")
	}
	do(d.Post(Post{Kind: "defect", To: []string{noticeTo(s, o.name), o.name}, LaneRef: o.name, Text: fmt.Sprintf("%s would retire, but %s. The supervisor holds %s, never relaunched or retired, until it posts again. %s: %s; coordinator: relaunch it or reassign its tasks.", o.name, o.owes(), o.name, o.name, strings.Join(remedies, "; "))}))
}

func unreadBy(s *store.Store, r Record, name, cited string, retry Request) (unread, bool) {
	read, newest := unseen(s, name, cited)
	if read == "" || r.Lane == name {
		return unread{}, false
	}
	return unread{name: name, why: fmt.Sprintf("the coordinator's %s %s would stand %s%s%s", r.Kind, r.Name(), name, unreadMark, read), asker: r.Lane, retry: retry, newest: newest}, true
}

func strippedBy(s *store.Store, owner string, numbers []int) []string {
	var stripped []string
	for _, lane := range must(s.Lanes(live...)) {
		kept := slices.ContainsFunc(lane.PRs, func(n int) bool { return !slices.Contains(numbers, n) })
		if lane.Name != owner && len(lane.PRs) > 0 && !kept {
			stripped = append(stripped, lane.Name)
		}
	}
	return stripped
}

func unseen(s *store.Store, name, cited string) (read string, newest int64) {
	newest = must(s.NewestSeq(name))
	if cited == "" {
		return "cites no last-read=<seq>", newest
	}
	seq, err := strconv.ParseInt(cited, 10, 64)
	if err == nil && (newest == 0 || seq >= newest) {
		return "", newest
	}
	return fmt.Sprintf("cites last-read=%s, older than %s's newest record #%d", cited, name, newest), newest
}

type Row struct {
	Lane        string    `json:"lane"`
	Session     string    `json:"session,omitempty"`
	Agent       string    `json:"agent,omitempty"`
	Live        bool      `json:"live"`
	SeenAt      time.Time `json:"seen_at"`
	StoodDown   bool      `json:"stood_down,omitempty"`
	Roster      string    `json:"roster,omitempty"`
	Idle        bool      `json:"idle,omitempty"`
	Dispatch    string    `json:"dispatch,omitempty"`
	Succeeded   bool      `json:"succeeded,omitempty"`
	Unverified  string    `json:"unverified,omitempty"`
	HostGone    bool      `json:"host_gone,omitempty"`
	Terminal    string    `json:"terminal,omitempty"`
	Environment string    `json:"environment,omitempty"`
}

type Peer struct {
	Lane     string
	Live     bool
	Seen     time.Time
	Row      Row
	LastCall time.Time
}

func Peers(rows []Row) map[string]Peer {
	found := make(map[string]Peer, len(rows))
	for _, row := range rows {
		if row.Lane == "" {
			continue
		}
		have, known := found[row.Lane]
		lastCall := have.LastCall
		if row.Dispatch == "" {
			lastCall = newer(lastCall, row.SeenAt.UTC())
		}
		if !known || (row.Live && !have.Live) || (row.Live == have.Live && row.SeenAt.After(have.Seen)) {
			have = Peer{Lane: row.Lane, Live: row.Live, Seen: row.SeenAt.UTC(), Row: row}
		}
		have.LastCall = lastCall
		if row.Unverified != "" && !row.HostGone {
			have.Row.Dispatch, have.Row.Terminal, have.Row.Environment = row.Dispatch, row.Terminal, row.Environment
		}
		found[row.Lane] = have
	}
	return found
}

func alive(peer *Peer, heard, now time.Time) bool {
	return (peer != nil && peer.Live) || (!heard.IsZero() && now.Sub(heard) < StaleAfter)
}

type VerdictKind string

const (
	Ok        VerdictKind = "ok"
	CameUp    VerdictKind = "came-up"
	NoShow    VerdictKind = "no-show"
	Dead      VerdictKind = "dead"
	Unproven  VerdictKind = "unproven"
	Lost      VerdictKind = "lost"
	StoodDown VerdictKind = "stood-down"
	AskRotate VerdictKind = "ask-rotate"

	Unanswered VerdictKind = "unanswered"

	nothingProves = ", so nothing proves it died"
)

type Verdict struct {
	Kind VerdictKind
	Why  string
}

func newer(a, b time.Time) time.Time {
	if a.After(b) {
		return a
	}
	return b
}

func commas(n int) string {
	digits := strconv.Itoa(n)
	for at := len(digits) - 3; at > 0; at -= 3 {
		digits = digits[:at] + "," + digits[at:]
	}
	return digits
}

func Classify(lane store.Lane, peer *Peer, now time.Time, tokens, rotateAt int) Verdict {
	heard := lane.Heard
	if peer != nil {
		heard = newer(peer.Seen, lane.Heard)
	}
	duty := dutyOf(lane)
	aliveNow := alive(peer, heard, now) && (duty == "" || peer != nil && peer.Live || now.Sub(heard) < unownedAfter)
	if lane.State == store.LaneLaunching {
		if aliveNow && (peer != nil || lane.Heard.After(lane.Since)) {
			return Verdict{Kind: CameUp}
		}
		if now.Sub(lane.Since) >= launchWindow && now.Sub(lane.Staged) >= StaleAfter {
			return Verdict{NoShow, fmt.Sprintf("not live %d minutes after its launch", int(launchWindow.Minutes()))}
		}
		return Verdict{Kind: Ok}
	}
	if !aliveNow {
		if lane.StoppedOn != 0 && peer != nil && peer.Row.Unverified == "" {
			return Verdict{StoodDown, fmt.Sprintf("stopped on the coordinator's #%d", lane.StoppedOn)}
		}
		if lane.Waiting != 0 && duty == "" {
			return Verdict{Kind: Ok}
		}
		heardFor := "was never heard"
		if !heard.IsZero() {
			heardFor = fmt.Sprintf("has been quiet for %d min", int(now.Sub(heard).Minutes()))
		}
		if peer == nil {
			return Verdict{Unproven, "has no process row in cci who or Orca and " + heardFor + nothingProves}
		}
		if peer.Row.StoodDown {
			return Verdict{Kind: StoodDown}
		}
		if !lane.Answered.IsZero() && now.Sub(lane.Answered) < StaleAfter {
			return Verdict{Kind: Ok}
		}
		if peer.Row.Unverified != "" {
			return Verdict{Unproven, peer.Row.Unverified + " and " + heardFor + nothingProves}
		}
		return Verdict{Dead, fmt.Sprintf("stopped, quiet for %d min", int(now.Sub(heard).Minutes()))}
	}
	if tokens > 0 && rotateAt > 0 && tokens >= rotateAt {
		return Verdict{AskRotate, commas(tokens) + " tokens"}
	}
	return Verdict{Kind: Ok}
}
