package watch

import (
	"cmp"
	"context"
	"fmt"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/findings"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	UnfinishedNamespace = "unfinished"

	unfinishedKind = "unfinished"
	unmoved        = 24 * time.Hour
	retroFile      = "retro.json"
	findingsFile   = "findings.json"
	retroDone      = "live"
	pickKind       = "pick"
	progressDesk   = "progress-desk"
	clockAndDay    = "3:04 PM Jan 2"
)

var (
	findingOpen = []string{"open", "routed"}
	namedPR     = regexp.MustCompile(`#(\d+)\b`)
)

type Ledgers struct {
	Asks     asks.Book
	Retro    []ledger.Row
	Findings []ledger.Row
	Waits    []board.Wait
	Live     []string
	Landed   map[int]bool
}

func ReadLedgers(ctx context.Context, s *store.Store, entry drive.Entry, runner shell.Runner, now time.Time) (Ledgers, error) {
	source := ledger.CLI{Runner: runner, Repo: entry.Checkout}
	book, err := asks.Mirror{Source: source, Store: s, Ledger: entry.Ledger, Now: func() time.Time { return now }}.Book(ctx)
	if err != nil {
		return Ledgers{}, err
	}
	retro, err := ledger.FromState(ctx, source, filepath.Join(entry.StateDir, retroFile))
	if err != nil {
		return Ledgers{}, err
	}
	findings, err := ledger.FromState(ctx, source, filepath.Join(entry.StateDir, findingsFile))
	if err != nil {
		return Ledgers{}, err
	}
	rows, err := s.ReviewRows(entry.Repo)
	if err != nil {
		return Ledgers{}, err
	}
	live, err := board.LiveOf(board.Outcomes{}, nil, rows)
	if err != nil {
		return Ledgers{}, err
	}
	lanes, err := s.Lanes(store.LaneLaunching, store.LaneDesired)
	if err != nil {
		return Ledgers{}, err
	}
	prs, err := s.PRs(entry.Repo)
	if err != nil {
		return Ledgers{}, err
	}
	l := Ledgers{Asks: book, Retro: retro, Findings: findings, Waits: live.Waits(), Landed: map[int]bool{}}
	for _, lane := range lanes {
		l.Live = append(l.Live, lane.Name)
	}
	for _, pr := range prs {
		if pr.State == machine.StateLanded {
			l.Landed[pr.Number] = true
		}
	}
	return l, nil
}

func (l Ledgers) liveOwner(owner string) string {
	if owner == "" || owner == root || !slices.ContainsFunc(l.Live, func(name string) bool { return client.Stem(name) == client.Stem(owner) }) {
		return ""
	}
	return client.Stem(owner)
}

func (l Ledgers) pickRoute(fields map[string]string) string {
	if owner := l.liveOwner(fields["owner"]); owner != "" {
		return owner
	}
	if slices.ContainsFunc(namedPR.FindAllStringSubmatch(fields["text"], -1), func(found []string) bool {
		number, err := strconv.Atoi(found[1])
		return err == nil && l.Landed[number]
	}) {
		return progressDesk
	}
	return root
}

func (l Ledgers) waitsOnHim(text string) bool {
	for _, found := range namedPR.FindAllStringSubmatch(text, -1) {
		if slices.ContainsFunc(l.Waits, func(w board.Wait) bool { return w.Kind == "tap" && w.PR == found[1] }) {
			return true
		}
	}
	return false
}

func Unfinished(l Ledgers, now time.Time) ([]Wait, error) {
	var waits []Wait
	for _, row := range l.Retro {
		if row.Fields["state"] == retroDone || row.Fields["dup_of"] != "" {
			continue
		}
		due, err := dueOf(row.Fields)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", row.Key, err)
		}
		what := fmt.Sprintf("retro row, state %s, owner %s", orNone(row.Fields["state"]), orNone(row.Fields["owner"]))
		step := fmt.Sprintf("name a live owner and a new due with `retro.py set %s`, or close it with `retro.py live %s --record <FIX-LIVE seq>`", row.Key, row.Key)
		waits = append(waits, unfinishedWait(row.Key, what, "unchanged", row.Fields["failure"], step, cmp.Or(l.liveOwner(row.Fields["owner"]), root), row.Updated, due, now))
	}
	for _, ask := range l.Asks.Asks {
		if slices.Contains([]asks.State{asks.Live, asks.Dropped, asks.Answered}, l.Asks.State(ask, now)) {
			continue
		}
		moved := ask.AskedAt
		if ask.Newest != nil {
			moved = ask.Newest.At
		}
		what := "owner ask, worker " + orNone(ask.Lane)
		step := fmt.Sprintf("deliver it, log progress with `lr asks progress --ask %s`, or settle it with `lr asks answer` or `drop`", ask.Key)
		waits = append(waits, unfinishedWait(ask.Key, what, "no progress", ask.Text, step, root, moved, ask.Due, now))
	}
	for _, row := range l.Findings {
		if !slices.Contains(findingOpen, row.Fields["state"]) || row.Fields["kind"] == pickKind && l.waitsOnHim(row.Fields["text"]) {
			continue
		}
		seq := row.Fields["seq"]
		what, to := "finding from "+row.Fields["sweep"], root
		step := fmt.Sprintf("build it and name #%s in the HEAD record, or close it with `lr findings close --why <reason> %s`", seq, seq)
		if row.Fields["kind"] == pickKind {
			what, to = "owner pick", l.pickRoute(row.Fields)
			step = pickAsk(to, seq)
		}
		waits = append(waits, unfinishedWait(row.Key, what+", owner "+orNone(row.Fields["owner"]), row.Fields["state"], row.Fields["text"], step, to, row.Updated, time.Time{}, now))
	}
	return waits, nil
}

func pickAsk(to, seq string) string {
	speaker := to
	if to == progressDesk {
		speaker = root
	}
	return fmt.Sprintf("act on nothing under it until its purpose is checked: if what it asks for is still undone and its text or change is still right, %s posts `%s`; if it is done or moot, close it with `lr findings close --why <reason> %s`", speaker, findings.UnmetPost(speaker, seq), seq)
}

func unfinishedWait(key, what, quiet, text, step, to string, moved, due, now time.Time) Wait {
	since, within, state, mover := moved, unmoved, quiet+" since "+moved.In(pacific).Format("Jan 2"), machine.MoverRoot
	if to != root {
		mover = machine.MoverLane
	}
	if !due.IsZero() && due.Before(now) {
		since, within, state = due, 0, "past due since "+due.In(pacific).Format("Jan 2")
	}
	return Wait{
		Kind: unfinishedKind, Repo: UnfinishedNamespace, Head: key, Mover: mover, Since: since, Within: within, To: to,
		Cause: fmt.Sprintf("unfinished: %s, %s: %s; still standing %s; %s", what, state, oneLine(text), now.In(pacific).Format("Jan 2"), step),
	}
}

func dueOf(fields map[string]string) (time.Time, error) {
	if fields["due"] == "" {
		return time.Time{}, nil
	}
	return store.Parse(fields["due"])
}

func orNone(word string) string {
	if word == "" {
		return "none"
	}
	return word
}
