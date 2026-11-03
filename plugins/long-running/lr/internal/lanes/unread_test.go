package lanes

import (
	"errors"
	"fmt"
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

func TestOrdersUnreadForTenMinutesReachRootInOneBlockerOnce(t *testing.T) {
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
	equal(t, len(f.ofKind("blocker")), 0)
	tick(t, s, f, t0.Add(mins(10)))
	tick(t, s, f, t0.Add(mins(11)))

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{Root})
	contains(t, blockers[0].Text, "conflicts-2 holds root's ask #3, relay-join-1's decide #4, the oldest for 10 min; other-1 holds root's ask #5, the oldest for 10 min")
	equal(t, len(f.wakes), 0)
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
	equal(t, len(f.ofKind("blocker")), 1)
	tick(t, s, f, t0.Add(mins(22)))

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 2)
	contains(t, blockers[1].Text, "root's ask #3, the oldest for 10 min")
}

func TestAMetaLanesUnreadOrderNeverGoesToRoot(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	if err := s.SetLaneRole(lane2, RoleMeta); err != nil {
		t.Fatal(err)
	}
	f.feed = append(f.feed, rec(2, orderAuthor, "ask", t0, "first", lane2))
	f.peers = []Row{who(lane2, true, t0.Add(-mins(5)))}

	tick(t, s, f, t0.Add(mins(10)))

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{OwnerRelay})
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
	equal(t, len(f.ofKind("blocker")), 0)

	f.feed = append(f.feed, rec(5, Root, "ask", t0.Add(mins(10)), "second", lane2), ask(6, "done", lane2, t0.Add(mins(10)), nil, "", nil))
	tick(t, s, f, t0.Add(mins(21)))

	equal(t, len(f.ofKind("blocker")), 0)
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
	equal(t, len(f.ofKind("blocker")), 0)

	f.deps.Who = answered
	tick(t, s, f, t0.Add(mins(11)))

	equal(t, len(f.ofKind("blocker")), 1)
}

func TestABlockerThatFailsToPostLeavesTheTickRunningAndPostsOnceLater(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))
	f.peers = []Row{who(lane2, true, t0.Add(-mins(5)))}
	delivered := f.deps.Post
	f.deps.Post = func(p Post) error {
		if p.Kind == "blocker" {
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

	equal(t, len(f.ofKind("blocker")), 1)
}

func TestManyOrdersToOneLaneCostOneWakeAndOneBlocker(t *testing.T) {
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

		counts[orders] = [2]int{len(f.wakes), len(f.ofKind("blocker"))}
	}
	equal(t, counts[1000], counts[1])
	equal(t, counts[1], [2]int{1, 1})
}
