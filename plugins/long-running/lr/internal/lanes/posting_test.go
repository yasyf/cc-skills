package lanes

import (
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestALaneThatStoppedAndThenLandedAPRReadsLiveEverywhere(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "stopped", t0, "Stopped on ask 438574: the pick is meta-desk's", Root))
	f.peers = []Row{who(lane2, true, t0)}
	tick(t, s, f, t0.Add(mins(1)))
	equal(t, row(t, s, lane2).State, store.LaneRetired)

	f.feed = append(f.feed, rec(3, lane2, "landed", t0.Add(mins(28)), "merged cc-notes#226", "meta-desk"))
	f.peers = []Row{who(lane2, true, t0.Add(mins(28)))}
	tick(t, s, f, t0.Add(mins(29)))

	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy}, []any{store.LaneDesired, ""})
	equal(t, must(Running(s, "", lane2)), lane2)
	equal(t, must(Route(s, lane2)), lane2)
}

func TestAPRPushedUnderAnAliasWithNoRowReachesItsOneLiveNumberedLane(t *testing.T) {
	s := memory(t)
	run := func(name string) {
		if _, err := s.WantLane(store.Lane{Name: name, State: store.LaneDesired}, t0); err != nil {
			t.Fatal(err)
		}
	}
	run("test-sleeps-go-1")
	equal(t, must(ItemOwner(s, testDrive, "test-sleeps-go", 51958)), "test-sleeps-go-1")

	run("test-sleeps-go-2")
	equal(t, must(ItemOwner(s, testDrive, "test-sleeps-go", 51958)), Root)
}

func TestALaneTheSupervisorRetiredForSilenceIsLiveOnItsNextRecord(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "reading the logs", Root))
	f.peers = []Row{{Lane: lane2, SeenAt: t0, Dispatch: "ctx_1"}}
	tick(t, s, f, t0.Add(mins(60)))
	equal(t, row(t, s, lane2).RetiredBy, retiredSilent)

	f.feed = append(f.feed, rec(3, lane2, "report", t0.Add(mins(70)), "still on it", Root))
	f.peers = []Row{who(lane2, true, t0.Add(mins(70)))}
	tick(t, s, f, t0.Add(mins(71)))

	equal(t, row(t, s, lane2).State, store.LaneDesired)
}

func TestACoordinatorStandDownStandsAgainstALaneThatKeepsPosting(t *testing.T) {
	standDown := ask(3, "stand-down", lane2, t0.Add(mins(2)), nil, "", map[string]string{"last-read": "2"})
	for name, first := range map[string]Record{
		"of a live lane":      rec(2, lane2, "state", t0, "working", Root),
		"after the lane stop": rec(2, lane2, "stopped", t0, "parked", Root),
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, first, standDown)
			f.peers = []Row{who(lane2, true, t0.Add(mins(2)))}
			tick(t, s, f, t0.Add(mins(3)))

			f.feed = append(f.feed, rec(4, lane2, "report", t0.Add(mins(10)), "one more thing", Root))
			tick(t, s, f, t0.Add(mins(11)))

			lane := row(t, s, lane2)
			equal(t, []any{lane.State, lane.RetiredBy}, []any{store.LaneRetired, Root})
		})
	}
}

func TestARecordThatEndsTheLaneAgainNeverRevivesIt(t *testing.T) {
	for kind, text := range map[string]string{
		"stopped":    "still stopped",
		"stand-down": "standing down",
		"handoff":    "handed off",
		"state":      "STOPPED conflicts-2: finished",
	} {
		t.Run(kind, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, rec(2, lane2, "stopped", t0, "finished", Root))
			f.peers = []Row{who(lane2, true, t0)}
			tick(t, s, f, t0.Add(mins(1)))

			f.feed = append(f.feed, rec(3, lane2, kind, t0.Add(mins(5)), text, Root))
			tick(t, s, f, t0.Add(mins(6)))

			equal(t, row(t, s, lane2).State, store.LaneRetired)
		})
	}
}

func TestAWorkerThatEndsAfterItsStopEndsTheParkedLaneHoweverItEnded(t *testing.T) {
	for _, outcome := range []string{"succeeded", "failed"} {
		t.Run(outcome, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, rec(2, lane2, "stopped", t0, "parked on a wait", Root))
			f.peers = []Row{who(lane2, true, t0)}
			tick(t, s, f, t0.Add(mins(1)))
			equal(t, row(t, s, lane2).RetiredBy, retiredStopped)

			f.feed = append(f.feed, rec(3, runner, "report", t0.Add(mins(5)), "OUTCOME msg_1 "+lane2+": worker_done "+outcome+" dispatch=ctx_1: routed"))
			tick(t, s, f, t0.Add(mins(6)))

			equal(t, row(t, s, lane2).RetiredBy, retiredWorkerDone)
		})
	}
}

func TestAStoppedLaneIsToldItsWaitStillWakesIt(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "stopped", t0, "Parked, not finished", "meta-desk"))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	downs := f.ofKind("stand-down")
	equal(t, len(downs), 1)
	if !strings.Contains(downs[0].Text, "keep waiting") || strings.Contains(downs[0].Text, "retired") {
		t.Fatal(downs[0].Text)
	}
}
