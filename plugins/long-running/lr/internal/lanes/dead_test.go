package lanes

import (
	"fmt"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestADesiredOrcaLaneWhoseAgentExitedWithAnOpenPRRelaunchesOnce(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.peers = []Row{{Lane: lane2, SeenAt: t0.Add(-mins(30)), Dispatch: "ctx_1"}}

	first := tick(t, s, f, t0)
	second := tick(t, s, f, t0.Add(mins(1)))

	equal(t, first.Relaunched, []Relaunch{{lane2, "conflicts-3", "stopped, quiet for 30 min"}})
	equal(t, len(second.Relaunched), 0)
	equal(t, f.launches, []string{"conflicts-3"})
	equal(t, row(t, s, "conflicts-3").PRs, []int{46001})
}

func TestADeadLaneThatHoldsNothingRetiresOnceSilentPastTheThreshold(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "reading the logs", Root))
	f.peers = []Row{{Lane: lane2, SeenAt: t0, Dispatch: "ctx_1"}}

	early := tick(t, s, f, t0.Add(mins(59)))
	equal(t, []any{len(early.Relaunched), len(f.posts), row(t, s, lane2).State}, []any{0, 0, store.LaneDesired})

	tick(t, s, f, t0.Add(mins(60)))

	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy, len(f.launches)}, []any{store.LaneRetired, retiredSilent, 0})
	reports := f.ofKind("report")
	equal(t, len(reports), 1)
	equal(t, reports[0].To, []string{Root})
	contains(t, reports[0].Text, "RETIRED conflicts-2: it stopped, quiet for 60 min, owns no PR, holds no standing duty, and has posted nothing for 60 minutes.")
}

func TestAColdSpriteParkedOnARecordedWaitIsLeftAloneAndAnUnparkedOneWithAPRIsHeld(t *testing.T) {
	cold := Row{Lane: lane2, SeenAt: t0.Add(-3 * time.Hour), Dispatch: "ctx_1", Unverified: "has an Orca agent that reads unverifiable (host_unavailable)"}
	for _, parked := range []bool{true, false} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "", 46001)
		said := rec(2, lane2, "state", t0.Add(-3*time.Hour), "restacking")
		if parked {
			said = rec(2, lane2, "blocked", t0.Add(-3*time.Hour), "lands after #48099").withRefs(prs(48099))
		}
		f.feed = append(f.feed, said)
		f.peers = []Row{cold}

		out := tick(t, s, f, t0)

		equal(t, len(out.Relaunched)+len(f.launches), 0)
		if parked {
			equal(t, []any{row(t, s, lane2).State, len(f.posts)}, []any{store.LaneDesired, 0})
			continue
		}
		equal(t, out.Held, []string{lane2})
		blockers := f.ofKind("blocker")
		equal(t, len(blockers), 1)
		contains(t, blockers[0].Text, "it has an Orca agent that reads unverifiable (host_unavailable) and has been quiet for 180 min, so nothing proves it died")
	}
}

func TestALaneTheSpriteAutostopEndedIsNeverRelaunched(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.feed = append(f.feed, rec(2, lane2, "done", t0.Add(-time.Hour), "shipped", Root))
	f.peers = []Row{{Lane: lane2, SeenAt: t0.Add(-time.Hour), Dispatch: "ctx_1"}}
	tick(t, s, f, t0)
	if first, err := s.StopSprite(lane2, t0); err != nil || !first {
		t.Fatalf("first %v err %v", first, err)
	}
	f.posts = nil

	out := tick(t, s, f, t0.Add(3*time.Hour))

	equal(t, len(out.Relaunched)+len(f.launches)+len(f.posts), 0)
	equal(t, row(t, s, lane2).State, store.LaneRetired)
}

func TestAnIdleRosteredTeammateHoldingNothingRetiresPastTheThresholdAndOneWithAPRStays(t *testing.T) {
	for _, owned := range [][]int{nil, {46001}} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "", owned...)
		f.feed = append(f.feed, rec(2, lane2, "state", t0, "restacked", Root))
		idle := onRoster(who(lane2, true, t0))
		idle.Idle = true
		f.peers = []Row{idle}

		tick(t, s, f, t0.Add(mins(59)))
		equal(t, row(t, s, lane2).State, store.LaneDesired)
		tick(t, s, f, t0.Add(mins(60)))

		want := store.LaneRetired
		if len(owned) > 0 {
			want = store.LaneDesired
		}
		equal(t, []any{row(t, s, lane2).State, len(f.launches)}, []any{want, 0})
	}
}

func TestATickOverLanesWithNoProcessReadsTheSameAtOneLaneAndAtAThousand(t *testing.T) {
	counts := func(n int) [5]int {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		var peers, records, states, owed int
		base := f.deps
		f.deps.Who = func(names []string) ([]Row, error) { peers++; return base.Who(names) }
		f.deps.Records = func(since int64) ([]Record, error) { records++; return base.Records(since) }
		f.deps.PRStates = func(numbers []int) (map[int]string, error) { states++; return base.PRStates(numbers) }
		f.deps.Owed = func() ([]deletions.Owed, error) { owed++; return base.Owed() }
		f.peers = []Row{who(lane2, true, t0)}
		for i := range n {
			name := fmt.Sprintf("gone-%d", i)
			if _, err := s.WantLane(store.Lane{Name: name, Brief: "/briefs/x.md", Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired}, t0.Add(-3*time.Hour)); err != nil {
				t.Fatal(err)
			}
			f.peers = append(f.peers, Row{Lane: name, SeenAt: t0.Add(-3 * time.Hour), Dispatch: fmt.Sprintf("ctx_%d", i)})
		}
		tick(t, s, f, t0)
		retired, err := s.Lanes(store.LaneRetired)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, len(retired), n)
		return [5]int{peers, records, states, owed, len(f.launches)}
	}
	equal(t, counts(1000), counts(1))
}
