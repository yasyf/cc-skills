package lanes

import (
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Self        = "supervisor"
	Root        = "root"
	OwnerRelay  = "owner-relay"
	runner      = "desk-runner"
	mainLane    = "main"
	CursorName  = "lanes"
	hourCursor  = "lanes.hour"
	strikeLimit = 2
	kindSpawn   = "spawn"
	kindRequest = "decide"
	kindLanded  = "landed"
	unreadMark  = " down or hand its PRs on, but it "
	NeedsWork   = "needs-work"
	Waiting     = "waiting"
	Closed      = "closed"
)

const (
	StaleAfter   = store.StaleAfter
	unownedAfter = 10 * time.Minute
	strikeWindow = 2 * StaleAfter
	PageAnswer   = 2 * StaleAfter
	liveWithin   = 4 * StaleAfter
)

var (
	ErrUnread = errors.New("source did not answer")
	Verbs     = []string{"stand-down", "park", "unpark", "owner", "done"}
	live      = []store.LaneState{store.LaneLaunching, store.LaneDesired}

	helperRoles = []string{"helper", "reader", "watch", "export", "evidence", "handoff", "comms", "triage"}
	carries     = []string{"head", "opened"}
	waits       = []string{"ask", "blocked", "blocker"}
	settled     = []string{"handoff", "done", "stand-down", "stopped"}

	numbered       = regexp.MustCompile(`^(.+)-(\d{1,2})$`)
	replaces       = regexp.MustCompile(`\b([\w.-]+) replaces ([\w.-]+)\b`)
	handedOff      = regexp.MustCompile(`\bhandoff ([\w.-]+): doc ([0-9a-f]{7,40})\b`)
	verbWord       = regexp.MustCompile(`\b(?:closed|complete|done|park|retired|stop waking|unpark)\b`)
	followedByWord = regexp.MustCompile(`^\s+\w`)
	workerDone     = regexp.MustCompile(`^OUTCOME \S+ ([\w.-]+): worker_done succeeded\b`)
	standDownList  = regexp.MustCompile(`(?i)\bstand[ -]?down\s+([^.(;:\n]+)`)
	laneWord       = regexp.MustCompile(`[\w.-]+`)
	card           = regexp.MustCompile(`\bcard-[\w.-]*\w`)
	parked         = regexp.MustCompile(`\bPARKED ([\w.-]+):`)
	prMention      = regexp.MustCompile(`#(\d+)\b`)
	ownerAttr      = regexp.MustCompile(`\bowner=([\w.-]+)`)
	retiredWord    = regexp.MustCompile(`\b([\w.-]+) (?:retired|is done)\b`)
	ownerClause    = regexp.MustCompile(`#(\d+)\b[^.;#]*?\b(?:moves to ([\w.-]+)|with ([\w.-]+) as owner)`)
	lastRead       = regexp.MustCompile(`\blast-read=#?(\d+)\b`)
	picked         = regexp.MustCompile(`\([\w.-]+\)`)
)

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

func successorName(name string, taken map[string]bool) string {
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

func retire(s *store.Store, name, by string, at time.Time) {
	do(s.SetLaneState(name, store.LaneRetired, at, by, ""))
	must(s.ReleaseWorker(name))
}

func hold(s *store.Store, name, why string, at time.Time) {
	do(s.SetLaneState(name, store.LaneHeld, at, "", why))
	must(s.ReleaseWorker(name))
}

func unreadBy(s *store.Store, r Record, name, cited string) string {
	newestSeq := must(s.NewestSeq(name))
	if cited != "" {
		seq, err := strconv.ParseInt(cited, 10, 64)
		if err == nil && (newestSeq == 0 || seq >= newestSeq) {
			return ""
		}
	}
	read := "cites no last-read=<seq>"
	if cited != "" {
		read = fmt.Sprintf("cites last-read=%s, older than %s's newest record #%d", cited, name, newestSeq)
	}
	return fmt.Sprintf("root's %s #%d would stand %s%s%s", r.Kind, r.Seq, name, unreadMark, read)
}

func successorNote(s *store.Store, name string) string {
	probes := must(s.Probes())
	heir, found := newest(s, name)
	if found && heir.Name != name && heir.State != store.LaneRetired && probes[heir.Name].Dead.IsZero() {
		return "its live successor is " + heir.Name
	}
	return "it has no live successor"
}

type Row struct {
	Lane      string    `json:"lane"`
	Session   string    `json:"session,omitempty"`
	Agent     string    `json:"agent,omitempty"`
	Live      bool      `json:"live"`
	SeenAt    time.Time `json:"seen_at"`
	StoodDown bool      `json:"stood_down,omitempty"`
	Roster    string    `json:"roster,omitempty"`
	Dispatch  string    `json:"dispatch,omitempty"`
}

type Peer struct {
	Lane string
	Live bool
	Seen time.Time
	Row  Row
}

func Peers(rows []Row) map[string]Peer {
	found := make(map[string]Peer, len(rows))
	for _, row := range rows {
		if row.Lane == "" {
			continue
		}
		have, known := found[row.Lane]
		if !known || (row.Live && !have.Live) || (row.Live == have.Live && row.SeenAt.After(have.Seen)) {
			found[row.Lane] = Peer{row.Lane, row.Live, row.SeenAt.UTC(), row}
		}
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
	StoodDown VerdictKind = "stood-down"
	AskRotate VerdictKind = "ask-rotate"
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
	aliveNow := alive(peer, heard, now)
	if lane.State == store.LaneLaunching {
		if peer != nil && aliveNow {
			return Verdict{Kind: CameUp}
		}
		if now.Sub(lane.Since) >= StaleAfter {
			return Verdict{NoShow, fmt.Sprintf("not live %d minutes after its launch", int(StaleAfter.Minutes()))}
		}
		return Verdict{Kind: Ok}
	}
	if !aliveNow {
		if lane.StoppedOn != 0 {
			return Verdict{StoodDown, fmt.Sprintf("stopped on root's #%d", lane.StoppedOn)}
		}
		if peer == nil {
			last := "never"
			if !heard.IsZero() {
				last = "at " + pacific(heard)
			}
			return Verdict{Unproven, "has no process row in cci who or Orca and was last heard " + last + ", so nothing proves it died"}
		}
		if peer.Row.StoodDown {
			return Verdict{Kind: StoodDown}
		}
		if lane.Waiting != 0 || (!lane.Answered.IsZero() && now.Sub(lane.Answered) < StaleAfter) {
			return Verdict{Kind: Ok}
		}
		return Verdict{Dead, "stopped at " + pacific(heard)}
	}
	if tokens > 0 && rotateAt > 0 && tokens >= rotateAt {
		return Verdict{AskRotate, commas(tokens) + " tokens"}
	}
	return Verdict{Kind: Ok}
}
