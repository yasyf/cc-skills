package lanes

import (
	"bytes"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const orderAuthor = "relay-join-1"

func orcaPeers(lane string, lastCall, now time.Time) []Row {
	return []Row{who(lane, true, lastCall), {Lane: lane, Live: true, SeenAt: now, Dispatch: "ctx_af31953664f5"}}
}

func unreadOrders(t *testing.T, s *store.Store) []store.Unread {
	t.Helper()
	orders, err := s.Unread()
	if err != nil {
		t.Fatal(err)
	}
	return orders
}

func TestAnOrderNamingNoPROfItsOwnWakesAnIdleStandingLaneOnceWithinATick(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	idle := t0.Add(-mins(12))
	f.feed = append(f.feed,
		rec(2, Root, "ask", t0, "#49020 follows #49008; run the release", refresher),
		rec(3, orderAuthor, "decide", t0, "run it now", refresher),
	)

	f.peers = orcaPeers(refresher, idle, t0.Add(10*time.Second))
	beforeATick := tick(t, s, f, t0.Add(10*time.Second))
	f.peers = orcaPeers(refresher, idle, t0.Add(40*time.Second))
	withinATick := tick(t, s, f, t0.Add(40*time.Second))
	f.peers = orcaPeers(refresher, idle, t0.Add(70*time.Second))
	after := tick(t, s, f, t0.Add(70*time.Second))

	equal(t, len(beforeATick.Woken)+len(after.Woken), 0)
	equal(t, withinATick.Woken, []string{refresher})
	equal(t, f.wakes, []string{refresher})
	contains(t, pageText(f.pages[0], "why"), "root's ask #2, relay-join-1's decide #3")
}

func TestAnIdleWorkerLaneIsWokenAtFiveMinutesAndNobodyIsAskedToRelay(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	idle := t0.Add(-mins(12))
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))

	f.peers = orcaPeers(lane2, idle, t0.Add(mins(5)-time.Second))
	before := tick(t, s, f, t0.Add(mins(5)-time.Second))
	f.peers = orcaPeers(lane2, idle, t0.Add(mins(5)))
	woken := tick(t, s, f, t0.Add(mins(5)))
	f.peers = orcaPeers(lane2, t0.Add(mins(6)), t0.Add(mins(10)))
	tick(t, s, f, t0.Add(mins(10)))

	equal(t, len(before.Woken), 0)
	equal(t, woken.Woken, []string{lane2})
	equal(t, len(f.ofKind("blocker")), 0)
}

func TestANoteOrAnAnswerToALaneParkedOnItsPRWakesItOnceAtFiveMinutes(t *testing.T) {
	for _, kind := range []string{"note", "answer"} {
		t.Run(kind, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			idle := t0.Add(-mins(12))
			f.feed = append(f.feed,
				rec(2, lane2, "blocked", idle, "waiting on #50231 and the install").withRefs(prs(50231)),
				rec(3, orderAuthor, kind, t0, "the release is on the Mac", lane2),
			)

			f.peers = orcaPeers(lane2, idle, t0.Add(mins(5)-time.Second))
			before := tick(t, s, f, t0.Add(mins(5)-time.Second))
			f.peers = orcaPeers(lane2, idle, t0.Add(mins(5)))
			woken := tick(t, s, f, t0.Add(mins(5)))
			f.peers = orcaPeers(lane2, idle, t0.Add(mins(6)))
			after := tick(t, s, f, t0.Add(mins(6)))

			equal(t, len(before.Woken)+len(after.Woken), 0)
			equal(t, woken.Woken, []string{lane2})
			equal(t, f.wakes, []string{lane2})
			contains(t, pageText(f.pages[0], "why"), fmt.Sprintf("%s's %s #3", orderAuthor, kind))
		})
	}
}

func restedPastOrcasStatus(lane string, lastCall time.Time, reason string) []Row {
	return []Row{who(lane, true, lastCall), {Lane: lane, SeenAt: lastCall, Dispatch: "ctx_af31953664f5", Unverified: "has an Orca agent that reads unverifiable (" + reason + ")", HostGone: reason == hostUnavailable}}
}

func TestAnOrderToTheAliasOfALaneRestedPastOrcasStatusWakesItWithinTheTick(t *testing.T) {
	for _, last := range []string{"blocked", "ask", "blocker", "report"} {
		t.Run(last, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			rested := t0.Add(-mins(150))
			f.feed = append(f.feed,
				rec(2, lane2, last, rested, "waiting on the owner", orderAuthor),
				rec(3, orderAuthor, "owner", t0, "build it now", Stem(lane2)),
			)
			f.peers = restedPastOrcasStatus(lane2, rested, "stale_status")

			before := tick(t, s, f, t0.Add(mins(5)-time.Second))
			woken := tick(t, s, f, t0.Add(mins(5)))
			after := tick(t, s, f, t0.Add(mins(6)))

			equal(t, len(before.Woken)+len(after.Woken), 0)
			equal(t, woken.Woken, []string{lane2})
			equal(t, f.wakes, []string{lane2})
			contains(t, pageText(f.pages[0], "why"), orderAuthor+"'s owner #3")
		})
	}
}

func TestAPageReachesALaneRestedPastOrcasStatus(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 50231)
	f.feed = append(f.feed, rec(2, orderAuthor, "blocker", t0, "#50231 is red", lane2).withRefs(prs(50231)))
	f.peers = restedPastOrcasStatus(lane2, t0.Add(-mins(150)), "stale_status")

	out := tick(t, s, f, t0.Add(time.Minute))

	equal(t, out.Woken, []string{lane2})
	equal(t, f.wakes, []string{lane2})
}

func TestAWakeSkipsALaneOrcaCannotReach(t *testing.T) {
	rested := t0.Add(-mins(150))
	cases := map[string][]Row{
		"its host is gone":        restedPastOrcasStatus(lane2, rested, hostUnavailable),
		"its dispatch has exited": {who(lane2, true, rested), {Lane: lane2, SeenAt: rested, Dispatch: "ctx_af31953664f5"}},
	}
	for name, peers := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, rec(2, orderAuthor, "note", t0, "the release is on the Mac", lane2))
			f.peers = peers

			out := tick(t, s, f, t0.Add(mins(5)))

			equal(t, len(out.Woken), 0)
			equal(t, len(f.wakes), 0)
		})
	}
}

func TestADeclinedWakeLogsEachRecordOnceWithItsReason(t *testing.T) {
	var logged bytes.Buffer
	before := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logged, nil)))
	t.Cleanup(func() { slog.SetDefault(before) })
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, orderAuthor, "note", t0, "the release is on the Mac", lane2))
	f.peers = restedPastOrcasStatus(lane2, t0.Add(-mins(150)), hostUnavailable)

	tick(t, s, f, t0.Add(mins(5)-time.Second))
	equal(t, strings.Count(logged.String(), "wake declined"), 0)

	tick(t, s, f, t0.Add(mins(5)))
	tick(t, s, f, t0.Add(mins(6)))
	equal(t, strings.Count(logged.String(), "wake declined"), 1)
	contains(t, logged.String(), "no dispatch or roster to reach it")
	contains(t, logged.String(), orderAuthor+"'s note #2")

	f.feed = append(f.feed, rec(3, orderAuthor, "note", t0.Add(mins(6)), "and the staging promotion", lane2))
	tick(t, s, f, t0.Add(mins(11)))
	equal(t, strings.Count(logged.String(), "wake declined"), 2)
	contains(t, logged.String(), orderAuthor+"'s note #3")
}

func TestALaneThatPostedSinceANoteIsNotWokenForIt(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed,
		rec(2, orderAuthor, "note", t0, "the release is on the Mac", lane2),
		rec(3, lane2, "report", t0.Add(time.Minute), "reading it now", orderAuthor),
	)
	f.peers = orcaPeers(lane2, t0.Add(-mins(12)), t0.Add(mins(6)))

	out := tick(t, s, f, t0.Add(mins(6)))

	equal(t, len(out.Woken), 0)
	equal(t, len(unreadOrders(t, s)), 0)
}

func TestARecordTheMachineWroteOrOneNotAddressedToTheLaneNeverWakesIt(t *testing.T) {
	cases := map[string]Record{
		"head":            rec(2, orderAuthor, "head", t0, "abc123", lane2),
		"state":           rec(2, orderAuthor, "state", t0, "working", lane2),
		"opened":          rec(2, orderAuthor, "opened", t0, "#50231", lane2),
		"heartbeat":       rec(2, orderAuthor, "heartbeat", t0, "alive", lane2),
		"lr.note":         rec(2, orderAuthor, "lr.note", t0, "tick", lane2),
		"a passing grade": rec(2, orderAuthor, "review", t0, "finder: clean", lane2),
		"the supervisor":  rec(2, Self, "note", t0, "tick", lane2),
		"the runner":      rec(2, runner, "report", t0, "launched", lane2),
		"the stall watch": rec(2, "stall-watch", "blocker", t0, "STALL red #50231", lane2),
		"its own record":  rec(2, lane2, "note", t0, "to myself", lane2),
		"every lane":      rec(2, orderAuthor, "note", t0, "to everyone", "all"),
	}
	for name, record := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, record)
			f.peers = orcaPeers(lane2, t0.Add(-mins(12)), t0.Add(mins(6)))

			out := tick(t, s, f, t0.Add(mins(6)))

			equal(t, len(out.Woken), 0)
			equal(t, len(unreadOrders(t, s)), 0)
		})
	}
}

func TestAReportToADeskWaitsFiveMinutesAndNeitherRelaysNorRelaunches(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	idle := t0.Add(-mins(12))
	f.feed = append(f.feed, rec(2, orderAuthor, "report", t0, "merged, not installed", refresher))

	f.peers = orcaPeers(refresher, idle, t0.Add(mins(4)))
	early := tick(t, s, f, t0.Add(mins(4)))
	f.peers = orcaPeers(refresher, idle, t0.Add(mins(5)))
	woken := tick(t, s, f, t0.Add(mins(5)))
	f.peers = orcaPeers(refresher, idle, t0.Add(mins(31)))
	late := tick(t, s, f, t0.Add(mins(31)))

	equal(t, len(early.Woken)+len(late.Woken), 0)
	equal(t, woken.Woken, []string{refresher})
	equal(t, len(late.Relaunched), 0)
	equal(t, len(relays(f)), 0)
}

func TestAFailedWakeTriesAgainOnTheNextTick(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "run it", refresher))
	f.peers = orcaPeers(refresher, t0.Add(-mins(5)), t0.Add(time.Minute))
	delivered := f.deps.Wake
	f.deps.Wake = func(string, Row, map[string]any) (bool, error) { return false, nil }

	failed := tick(t, s, f, t0.Add(time.Minute))
	f.deps.Wake = delivered
	retried := tick(t, s, f, t0.Add(2*time.Minute))

	equal(t, len(failed.Woken), 0)
	equal(t, retried.Woken, []string{refresher})
}

func TestALaneMakingToolCallsIsNeitherWokenNorReportedForAnOrder(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "run it", refresher))

	f.peers = orcaPeers(refresher, t0.Add(2*time.Second), t0.Add(time.Minute))
	first := tick(t, s, f, t0.Add(time.Minute))
	f.peers = orcaPeers(refresher, t0.Add(2*time.Second), t0.Add(mins(11)))
	second := tick(t, s, f, t0.Add(mins(11)))

	equal(t, len(first.Woken)+len(second.Woken), 0)
	equal(t, len(f.ofKind("blocker")), 0)
	equal(t, len(unreadOrders(t, s)), 0)
}

func TestALaneWithNoHookThatPostsAfterAnOrderHasReadIt(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "run it", refresher), rec(3, refresher, "state", t0.Add(5*time.Second), "on it"))
	f.peers = []Row{{Lane: refresher, Live: true, SeenAt: t0.Add(mins(11)), Dispatch: "ctx_af31953664f5"}}

	out := tick(t, s, f, t0.Add(mins(11)))

	equal(t, len(out.Woken), 0)
	equal(t, len(f.ofKind("blocker")), 0)
}

func TestALiveOrcaRowAloneNeverCountsAsReadingAnOrder(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "run it", refresher))
	f.peers = []Row{{Lane: refresher, Live: true, SeenAt: t0.Add(time.Minute), Dispatch: "ctx_af31953664f5"}}

	out := tick(t, s, f, t0.Add(time.Minute))

	equal(t, out.Woken, []string{refresher})
}

func TestALaneWhoseNewestRecordSettlesItIsNeverWokenForAnOrder(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, refresher, "handoff", t0.Add(-mins(5)), "flushed", Root).withRefs(Refs{CCN: "9a1b2c3"}), rec(3, Root, "ask", t0, "run it", refresher))
	f.peers = orcaPeers(refresher, t0.Add(-mins(5)), t0.Add(time.Minute))

	out := tick(t, s, f, t0.Add(time.Minute))

	equal(t, len(out.Woken), 0)
}

func relays(f *fake) []Post {
	return slices.DeleteFunc(f.ofKind("go"), func(p Post) bool { return !strings.HasPrefix(p.Text, "orca-desk: relay to ") })
}

func TestOrdersUnreadForTenMinutesAreRelayedThroughTheDeskOncePerLane(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	other := "other-1"
	f.feed = append(f.feed,
		spawned(2, other, brief, t0.Add(-mins(30)), nil),
		rec(3, Root, "ask", t0, "first", lane2),
		rec(4, orderAuthor, "decide", t0, "second", lane2),
		rec(5, Root, "ask", t0, "third", other),
	)
	idle := t0.Add(-mins(5))
	f.peers = []Row{who(lane2, true, idle), who(other, true, idle)}

	tick(t, s, f, t0.Add(mins(10)-time.Second))
	equal(t, len(relays(f)), 0)
	tick(t, s, f, t0.Add(mins(10)))
	tick(t, s, f, t0.Add(mins(11)))

	sent := relays(f)
	equal(t, len(sent), 2)
	equal(t, sent[0].To, []string{orcaDesk})
	contains(t, sent[0].Text, "relay to conflicts-2: Orders addressed to you are unread: root's ask #3, relay-join-1's decide #4, the oldest for 10 min")
	contains(t, sent[1].Text, "relay to other-1: Orders addressed to you are unread: root's ask #5, the oldest for 10 min")
	equal(t, len(f.ofKind("blocker")), 0)
	equal(t, len(f.wakes), 0)
}

func TestAnOrderAddressedToAStemIsUnreadForTheLaneHoldingIt(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", Stem(lane2)))
	f.peers = []Row{who(lane2, true, t0.Add(-mins(5)))}

	tick(t, s, f, t0.Add(mins(10)))

	sent := relays(f)
	equal(t, len(sent), 1)
	contains(t, sent[0].Text, "relay to "+lane2+": Orders addressed to you are unread: root's ask #2, the oldest for 10 min")
}

func TestAnOrderAfterALaneReadsStartsItsOwnTenMinutes(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))
	f.peers = []Row{who(lane2, true, t0.Add(-mins(5)))}
	tick(t, s, f, t0.Add(mins(10)))

	f.peers = []Row{who(lane2, true, t0.Add(mins(11)))}
	tick(t, s, f, t0.Add(mins(11)))
	f.feed = append(f.feed, rec(3, Root, "ask", t0.Add(mins(12)), "second", lane2))
	tick(t, s, f, t0.Add(mins(21)))
	equal(t, len(relays(f)), 1)
	tick(t, s, f, t0.Add(mins(22)))

	sent := relays(f)
	equal(t, len(sent), 2)
	contains(t, sent[1].Text, "root's ask #3, the oldest for 10 min")
}

func TestAMetaLanesUnreadOrderIsRelayedAndRaisesNothingToRoot(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	if err := s.SetLaneRole(lane2, RoleMeta); err != nil {
		t.Fatal(err)
	}
	f.feed = append(f.feed, rec(2, orderAuthor, "ask", t0, "first", lane2))
	f.peers = []Row{who(lane2, true, t0.Add(-mins(5)))}

	tick(t, s, f, t0.Add(mins(10)))

	equal(t, len(relays(f)), 1)
	equal(t, len(f.ofKind("blocker")), 0)
}

func TestAResolvedOrderARetiredLaneAndAnOrderToItselfOrRootRaiseNothing(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	answered := rec(4, orderAuthor, "answer", t0.Add(time.Minute), "handled", lane2)
	answered.Resolves = 3
	f.feed = append(f.feed,
		rec(2, lane2, "ask", t0, "to itself and root", lane2, Root),
		rec(3, Root, "ask", t0, "first", lane2),
		answered,
	)
	f.peers = []Row{who(lane2, true, t0.Add(-mins(5)))}
	tick(t, s, f, t0.Add(mins(10)))
	equal(t, len(relays(f)), 0)

	f.feed = append(f.feed, rec(5, Root, "ask", t0.Add(mins(10)), "second", lane2), ask(6, "done", lane2, t0.Add(mins(10)), nil, "", nil))
	tick(t, s, f, t0.Add(mins(21)))

	equal(t, len(relays(f)), 0)
	equal(t, len(unreadOrders(t, s)), 0)
}

func TestAReadThatFailsRaisesNothingAndTheNextTickStillRaises(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))
	f.peers = []Row{who(lane2, true, t0.Add(-mins(5)))}
	tick(t, s, f, t0.Add(time.Minute))

	answered := f.deps.Who
	f.deps.Who = func([]string) ([]Row, error) { return nil, fmt.Errorf("%w: cci who", ErrUnread) }
	if _, err := Tick(s, f.deps, t0.Add(mins(10)), 1); !errors.Is(err, ErrUnread) {
		t.Fatalf("err %v", err)
	}
	equal(t, len(relays(f)), 0)

	f.deps.Who = answered
	tick(t, s, f, t0.Add(mins(11)))

	equal(t, len(relays(f)), 1)
}

func TestARelayThatFailsToPostLeavesTheTickRunningAndPostsOnceLater(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))
	f.peers = []Row{who(lane2, true, t0.Add(-mins(5)))}
	delivered := f.deps.Post
	f.deps.Post = func(p Post) error {
		if p.Kind == "go" {
			return fmt.Errorf("%w: cci post", ErrUnread)
		}
		return delivered(p)
	}

	if _, err := Tick(s, f.deps, t0.Add(mins(10)), 1); err != nil {
		t.Fatal(err)
	}
	f.deps.Post = delivered
	tick(t, s, f, t0.Add(mins(11)))
	tick(t, s, f, t0.Add(mins(12)))

	equal(t, len(relays(f)), 1)
}

func TestManyOrdersToOneLaneCostOneWakeAndOneRelay(t *testing.T) {
	counts := map[int][2]int{}
	for _, orders := range []int{1, 1000} {
		s := memory(t)
		f, _ := standingLane(t, s)
		for seq := range orders {
			f.feed = append(f.feed, rec(int64(seq+2), orderAuthor, "ask", t0, "run it", refresher))
		}
		f.peers = orcaPeers(refresher, t0.Add(-mins(5)), t0.Add(mins(10)))

		tick(t, s, f, t0.Add(mins(10)))
		tick(t, s, f, t0.Add(mins(11)))

		counts[orders] = [2]int{len(f.wakes), len(relays(f))}
	}
	equal(t, counts[1000], counts[1])
	equal(t, counts[1], [2]int{1, 1})
}
