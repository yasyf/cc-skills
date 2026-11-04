package lanes

import (
	"errors"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func deadWithMetaDesk(t *testing.T, s *store.Store) *fake {
	t.Helper()
	f := adopted(t, s, writeBrief(t), "", 46001)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20))), who("meta-desk-3", true, t0)}
	return f
}

func refusing(f *fake, times int) {
	launch := f.deps.Launch
	f.deps.Launch = func(name, model, effort, brief string) error {
		if err := launch(name, model, effort, brief); err != nil {
			return err
		}
		if len(f.launches) > times {
			return nil
		}
		return errors.New("refused, nothing was posted: brief names what no Sprite can receive")
	}
}

func relaunchReports(f *fake) []Post {
	var out []Post
	for _, p := range f.ofKind("report") {
		if strings.HasPrefix(p.Text, "RELAUNCHED ") {
			out = append(out, p)
		}
	}
	return out
}

func TestARefusedRelaunchRetriesOnceThenHoldsWithOneBlockerToTheMetaDeskAndNeverReportsARelaunch(t *testing.T) {
	s := memory(t)
	f := deadWithMetaDesk(t, s)
	refusing(f, 2)

	out := tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	equal(t, []any{f.launches, out.Relaunched, out.Held, row(t, s, lane2).State, len(relaunchReports(f))}, []any{[]string{"conflicts-3", "conflicts-3"}, []Relaunch(nil), []string{lane2}, store.LaneHeld, 0})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{"meta-desk-3"})
	contains(t, blockers[0].Text, "held, not relaunched: its relaunch as conflicts-3 was refused twice, last with refused, nothing was posted: brief names what no Sprite can receive")
}

func TestARelaunchRefusedOnceLaunchesOnItsRetry(t *testing.T) {
	s := memory(t)
	f := deadWithMetaDesk(t, s)
	refusing(f, 1)

	out := tick(t, s, f, t0)

	equal(t, []any{f.launches, out.Relaunched, row(t, s, "conflicts-3").State}, []any{[]string{"conflicts-3", "conflicts-3"}, []Relaunch{{lane2, "conflicts-3", "stopped, quiet for 20 min"}}, store.LaneLaunching})
	equal(t, []any{len(relaunchReports(f)), len(f.ofKind("blocker"))}, []any{1, 0})
}

func TestARelaunchTheRunnerRefusesRetriesOnceThenHoldsWithOneBlockerToTheMetaDesk(t *testing.T) {
	s := memory(t)
	f := deadWithMetaDesk(t, s)
	tick(t, s, f, t0)
	duplicate := func(seq int64, at time.Time) Record {
		return rec(seq, runner, "blocker", at, "LAUNCH-DUPLICATE R9 conflicts-3: fork-share-grant-2: /w/fork, 1 ahead of trunk already holds this work; nothing was launched", "meta-desk-3")
	}

	f.feed = append(f.feed, duplicate(10, t0.Add(mins(1))))
	retried := tick(t, s, f, t0.Add(mins(1)))

	equal(t, []any{f.launches, retried.Held, row(t, s, "conflicts-3").State, len(f.ofKind("blocker"))}, []any{[]string{"conflicts-3", "conflicts-3"}, []string(nil), store.LaneLaunching, 0})

	f.feed = append(f.feed, duplicate(11, t0.Add(mins(2))))
	held := tick(t, s, f, t0.Add(mins(2)))
	tick(t, s, f, t0.Add(mins(3)))

	equal(t, []any{f.launches, held.Held, row(t, s, "conflicts-3").State}, []any{[]string{"conflicts-3", "conflicts-3"}, []string{"conflicts-3"}, store.LaneHeld})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{"meta-desk-3"})
	contains(t, blockers[0].Text, "the runner refused the supervisor's relaunch as conflicts-3 twice, last with LAUNCH-DUPLICATE: fork-share-grant-2")
}

func hostGone(f *fake, gone bool) {
	quietFor(f, mins(25), "host_unavailable")
	f.peers[0].HostGone = gone
	f.hosts = map[string]Host{lane2: HostUnreachable}
}

func TestASpriteLaneWhoseHostOrcaReportsUnavailableForTenMinutesIsRelaunchedOnceFromWhatTheBusHolds(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s, 46001)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	hostGone(f, true)
	f.peers = append(f.peers, who("meta-desk-3", true, t0))

	tick(t, s, f, t0)
	early := tick(t, s, f, t0.Add(hostGoneAfter-time.Minute))
	equal(t, []any{early.Relaunched, len(f.launches)}, []any{[]Relaunch(nil), 0})

	out := tick(t, s, f, t0.Add(hostGoneAfter))

	why := "has a Sprite host Orca has reported unavailable for 10 min"
	equal(t, out.Relaunched, []Relaunch{{lane2, "conflicts-3", why}})
	reports := lostReports(f)
	equal(t, len(reports), 1)
	equal(t, slices.Contains(reports[0].To, "meta-desk-3"), true)
	brief := f.briefs["conflicts-3"]
	contains(t, brief, "read conflicts-2's records with `cci tail --drive "+testDrive+" --lane conflicts-2 --oldest`")
	contains(t, brief, "conflicts-2's worktree and transcript stayed on its Sprite")
	for _, gone := range []string{testCheckout, "lane-status", "lsof", "handoff-subagent-brief"} {
		if strings.Contains(brief, gone) {
			t.Fatalf("a Sprite successor's brief names %q:\n%s", gone, brief)
		}
	}
}

func TestASpriteLaneThatRecoversIsNeverRelaunchedAndOneThatRecoversAfterItsRelaunchDoesNotDoubleUp(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s, 46001)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	meta := who("meta-desk-3", true, t0)
	at := func(minutes int, gone bool) Outcome {
		hostGone(f, gone)
		f.peers = append(f.peers, meta)
		return tick(t, s, f, t0.Add(mins(minutes)))
	}

	at(0, true)
	at(8, false)
	at(9, true)
	at(18, true)
	equal(t, len(f.launches), 0)

	at(19, true)
	equal(t, f.launches, []string{"conflicts-3"})

	f.peers = []Row{who(lane2, true, t0.Add(mins(20))), meta}
	tick(t, s, f, t0.Add(mins(20)))
	tick(t, s, f, t0.Add(mins(21)))
	equal(t, []any{f.launches, row(t, s, lane2).State}, []any{[]string{"conflicts-3"}, store.LaneRetired})
}

func TestAQuietSpriteLaneOrcaReportsLiveIsNeverRelaunchedForPostingNoRecord(t *testing.T) {
	s := memory(t)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	spawn := spawned(1, lane2, writeBrief(t), t0.Add(-time.Hour), map[string]string{"model": "opus", "effort": "xhigh", "placement": placedOnSprite})
	spawn.Lane = "meta-desk-3"
	live := []Row{{Lane: lane2, Live: true, SeenAt: t0, Dispatch: "ctx_1"}, who("meta-desk-3", true, t0)}
	f := newFake(t, []Record{spawn}, live)
	tick(t, s, f, t0.Add(-time.Hour))

	tick(t, s, f, t0)
	equal(t, len(f.launches), 0)

	f.peers = []Row{{Lane: lane2, SeenAt: t0.Add(-time.Hour), Dispatch: "ctx_1", Unverified: "has an Orca agent that reads unverifiable (stale_status)"}, who("meta-desk-3", true, t0)}
	out := tick(t, s, f, t0.Add(mins(1)))

	equal(t, out.Relaunched, []Relaunch{{lane2, "conflicts-3", "has posted no record in the 61 min since its launch, and Orca does not report it live"}})
}

func TestAnOverdueItemsAskToAParkedLaneIsRelayedThroughTheDesk(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Self, "lr.ask", t0, "org/repo#1 restack overdue", lane2))
	f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0.Add(mins(11)), Dispatch: "ctx_1"}}

	tick(t, s, f, t0.Add(mins(11)))

	relays := f.ofKind("go")
	if len(relays) != 1 || !strings.HasPrefix(relays[0].Text, "orca-desk: relay to "+lane2+": Orders addressed to you are unread: supervisor's lr.ask #2") {
		t.Fatalf("relays = %+v", relays)
	}
}

func TestUnreadOrdersAreRelayedThroughTheDeskExceptForASpriteLaneTheSupervisorSeesNoToolCallsFrom(t *testing.T) {
	for name, c := range map[string]struct {
		setup  func(*testing.T, *store.Store) *fake
		relays int
	}{
		"a Sprite lane": {func(t *testing.T, s *store.Store) *fake { return onSprite(t, s) }, 0},
		"a Mac lane":    {func(t *testing.T, s *store.Store) *fake { return adopted(t, s, writeBrief(t), "") }, 1},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := c.setup(t, s)
			f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))
			f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0.Add(mins(11)), Dispatch: "ctx_1"}}

			tick(t, s, f, t0.Add(mins(11)))

			relays := 0
			for _, p := range f.ofKind("go") {
				if strings.HasPrefix(p.Text, "orca-desk: relay to "+lane2+": Orders addressed to you are unread: ") {
					relays++
				}
			}
			equal(t, relays, c.relays)
		})
	}
}
