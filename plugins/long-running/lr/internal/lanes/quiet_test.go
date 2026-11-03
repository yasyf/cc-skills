package lanes

import (
	"fmt"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestAWithdrawnLaneEndsOnTheRunnersLineAndGetsNoSuccessor(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 47001)
	f.feed = append(f.feed, rec(2, runner, "report", t0.Add(-mins(30)), "WITHDRAWN R84979 conflicts-2: by R84979; dispatch ctx_af31953664f5 stopped (stopped), and no report of it launches a case lane", Root))
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30)))}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy}, []any{store.LaneRetired, "withdrawn"})
	equal(t, owners(t, s, 47001)[47001], Root)
}

func TestAWithdrawalOfALaunchThatNeverRanLeavesTheLaneAlone(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, runner, "report", t0, "WITHDRAWN R85040 conflicts-2: by R85047; nothing was launched", Root))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0)

	equal(t, row(t, s, lane2).State, store.LaneDesired)
}

func TestALaneThatEndedOnPurposeNeverGetsASuccessor(t *testing.T) {
	for _, kind := range []string{"done", "stopped"} {
		t.Run(kind, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, rec(2, lane2, kind, t0.Add(-time.Hour), "out of scope", Root))
			f.peers = []Row{who(lane2, false, t0.Add(-time.Hour))}

			out := tick(t, s, f, t0)

			equal(t, len(out.Relaunched)+len(f.launches), 0)
			equal(t, row(t, s, lane2).State, store.LaneRetired)
		})
	}
}

func TestAParkedLaneWithADeadProcessGetsNoSuccessorUntilItsGateFires(t *testing.T) {
	gate := rec(3, runner, kindLanded, t0.Add(mins(1)), "LANDED #48099 as ef104ad", Root).withRefs(prs(48099))
	parked := rec(2, lane2, "blocked", t0.Add(-3*time.Hour), "lands after #48099").withRefs(prs(48099))
	cases := map[string]struct {
		parked, fires, other Record
	}{
		"upstream pr": {parked: parked, fires: gate, other: rec(3, runner, kindLanded, t0.Add(mins(1)), "LANDED #1 as ef104ad", Root).withRefs(prs(1))},
		"release target": {
			parked: rec(2, lane2, "blocked", t0.Add(-3*time.Hour), "lands after the api release").withRefs(Refs{Targets: []string{"api"}}),
			fires:  rec(3, "release-5", "release", t0.Add(mins(1)), "released api").withRefs(Refs{Targets: []string{"api"}}),
			other:  rec(3, "release-5", "release", t0.Add(mins(1)), "released web").withRefs(Refs{Targets: []string{"web"}}),
		},
		"another lane's end": {
			parked: rec(2, lane2, "blocked", t0.Add(-3*time.Hour), "held to the cutover").withRefs(Refs{Lanes: []string{"lr-go-lanes-1"}}),
			fires:  rec(3, "lr-go-lanes-1", "done", t0.Add(mins(1)), "cutover merged"),
			other:  rec(3, "lr-go-lanes-2", "done", t0.Add(mins(1)), "something else"),
		},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, c.parked)
			f.peers = []Row{who(lane2, false, t0.Add(-3*time.Hour))}

			waiting := tick(t, s, f, t0)
			equal(t, len(waiting.Relaunched)+len(f.launches), 0)
			equal(t, row(t, s, lane2).Waiting, int64(2))

			f.feed = append(f.feed, c.other)
			tick(t, s, f, t0.Add(mins(2)))
			equal(t, row(t, s, lane2).Waiting, int64(2))

			c.fires.Seq = 4
			f.feed = append(f.feed, c.fires)
			tick(t, s, f, t0.Add(mins(3)))
			equal(t, row(t, s, lane2).Waiting, int64(0))
			contains(t, pageText(row(t, s, lane2).Page, "why"), "what you parked on happened")
			equal(t, len(f.launches), 0)

			resumed := tick(t, s, f, t0.Add(mins(3)+StaleAfter))
			equal(t, resumed.Relaunched[0].Old, lane2)
			equal(t, f.launches, []string{"conflicts-3"})
		})
	}
}

func TestAFiredGateWakesALiveParkedLane(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "blocked", t0.Add(-time.Hour), "lands after #48099").withRefs(prs(48099)))
	f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0, Dispatch: "ctx_af31953664f5"}}
	tick(t, s, f, t0)
	equal(t, len(f.wakes), 0)

	f.feed = append(f.feed, rec(3, runner, kindLanded, t0.Add(mins(1)), "LANDED #48099 as ef104ad", Root).withRefs(prs(48099)))
	out := tick(t, s, f, t0.Add(mins(2)))

	equal(t, out.Woken, []string{lane2})
}

func TestABlockerOnItsPRResumesAParkedLane(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 48099)
	f.feed = append(f.feed, rec(2, lane2, "blocked", t0.Add(-time.Hour), "waiting on the go at the new head").withRefs(prs(48099)))
	f.peers = []Row{onRoster(who(lane2, true, t0))}
	tick(t, s, f, t0)

	f.feed = append(f.feed, stall(3, t0.Add(mins(1)), "STALL carry #48099: the go is at an older head", 48099, "carry:48099"))
	out := tick(t, s, f, t0.Add(mins(2)))

	equal(t, out.Woken, []string{lane2})
	equal(t, row(t, s, lane2).Waiting, int64(0))
}

func TestAnAnsweredAskToAnyLaneResumesItsAsker(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "ask", t0.Add(-time.Hour), "which head?", "meta-desk-1"))
	f.peers = []Row{who(lane2, false, t0.Add(-time.Hour))}

	waiting := tick(t, s, f, t0)
	equal(t, len(waiting.Relaunched), 0)

	f.feed = append(f.feed, rec(3, "meta-desk-1", "answer", t0.Add(mins(1)), "the newest", lane2).withRe(2))
	tick(t, s, f, t0.Add(mins(2)))
	equal(t, row(t, s, lane2).Waiting, int64(0))

	out := tick(t, s, f, t0.Add(mins(1)+StaleAfter))
	equal(t, out.Relaunched[0].Old, lane2)
}

func TestDoneWhileAPRIsOpenParksTheLaneUntilItsLastPRLands(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 47001, 47002)
	f.feed = append(f.feed, rec(2, lane2, "done", t0.Add(-time.Hour), "ready; lands after the stack below", Root))
	f.peers = []Row{who(lane2, false, t0.Add(-time.Hour))}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.Awaits}, []any{store.LaneDesired, doneParked})
	equal(t, owners(t, s, 47001, 47002), map[int]string{47001: lane2, 47002: lane2})

	f.feed = append(f.feed, rec(3, runner, kindLanded, t0.Add(mins(1)), "LANDED #47001", Root).withRefs(prs(47001)))
	tick(t, s, f, t0.Add(mins(2)))
	equal(t, row(t, s, lane2).State, store.LaneDesired)

	f.feed = append(f.feed, rec(4, runner, kindLanded, t0.Add(mins(3)), "LANDED #47002", Root).withRefs(prs(47002)))
	final := tick(t, s, f, t0.Add(mins(4)))
	lane = row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy, len(final.Relaunched)}, []any{store.LaneRetired, "done", 0})
}

func TestABrokenPRWakesTheLaneParkedOnItOnceUntilANewHead(t *testing.T) {
	parks := map[string]func(seq int64, at time.Time) Record{
		"done": func(seq int64, at time.Time) Record { return rec(seq, lane2, "done", at, "ready", Root) },
		"blocked on its PR": func(seq int64, at time.Time) Record {
			return rec(seq, lane2, "blocked", at, "waiting on CI").withRefs(prs(47001))
		},
	}
	for name, park := range parks {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "", 47001)
			f.feed = append(f.feed, park(2, t0))
			f.peers = []Row{onRoster(who(lane2, true, t0))}
			f.states = map[int]string{47001: NeedsWork}
			equal(t, len(tick(t, s, f, t0.Add(mins(1))).Woken), 0)

			f.states = map[int]string{47001: Broken}
			equal(t, tick(t, s, f, t0.Add(mins(2))).Woken, []string{lane2})
			equal(t, row(t, s, lane2).Waiting, int64(0))

			f.feed = append(f.feed, park(3, t0.Add(mins(4))))
			tick(t, s, f, t0.Add(mins(5)))
			equal(t, row(t, s, lane2).Waiting, int64(3))
			equal(t, row(t, s, lane2).Page == nil, true)

			f.feed = append(f.feed, rec(4, lane2, "head", t0.Add(mins(6)), "HEAD #47001").withRefs(prs(47001)), park(5, t0.Add(mins(7))))
			tick(t, s, f, t0.Add(mins(8)))
			equal(t, row(t, s, lane2).Waiting, int64(0))
		})
	}
}

func TestALaneThatPostsAfterItsDoneStaysParkedAndGetsNoSuccessor(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 47001)
	f.feed = append(f.feed,
		rec(2, lane2, "done", t0.Add(-3*time.Hour), "ready", Root),
		rec(3, lane2, "head", t0.Add(-2*time.Hour), "HEAD #47002 merged").withRefs(prs(47002)),
		rec(4, lane2, "answer", t0.Add(-2*time.Hour), "one more answer"),
	)
	f.peers = []Row{who(lane2, false, t0.Add(-2*time.Hour))}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	equal(t, row(t, s, lane2).Awaits, doneParked)

	f.states = map[int]string{47001: Closed, 47002: Closed}
	final := tick(t, s, f, t0.Add(mins(1)))
	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy, len(final.Relaunched)}, []any{store.LaneRetired, "done", 0})
}

func TestAParkedDoneLaneRetiresWhenItsPRCloses(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 47001)
	f.feed = append(f.feed, rec(2, lane2, "done", t0.Add(-2*time.Hour), "ready", Root))
	f.states = map[int]string{47001: Closed}

	tick(t, s, f, t0)

	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy}, []any{store.LaneRetired, "done"})
}

func TestATurnKilledByAnAPIErrorResumesTheSameLane(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	died := rec(2, lane2, "blocker", t0.Add(-mins(1)), "conflicts-2's turn died on an API error: rate_limit", Root)
	died.Fields.Attrs = map[string]string{"turn": turnDied}
	f.feed = append(f.feed, died)
	f.peers = []Row{onRoster(who(lane2, true, t0))}

	out := tick(t, s, f, t0)

	equal(t, out.Woken, []string{lane2})
	contains(t, pageText(f.pages[0], "why"), "your last turn died on an API error")
	equal(t, len(out.Relaunched), 0)
}

func TestATickReadsTheSameAtOneLaneAndAtAThousand(t *testing.T) {
	counts := func(n int) [3]int {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		var who, records, states int
		base := f.deps
		f.deps.Who = func(names []string) ([]Row, error) { who++; return base.Who(names) }
		f.deps.Records = func(since int64) ([]Record, error) { records++; return base.Records(since) }
		f.deps.PRStates = func(numbers []int) (map[int]string, error) { states++; return base.PRStates(numbers) }
		for i := range n {
			name := fmt.Sprintf("parked-%d", i)
			if _, err := s.WantLane(store.Lane{Name: name, Brief: "/briefs/x.md", Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired, PRs: []int{50000 + i}}, t0.Add(-time.Hour)); err != nil {
				t.Fatal(err)
			}
			if err := s.SetLaneWaiting(name, int64(i+1), time.Time{}, fmt.Sprintf("pr:%d", 60000+i)); err != nil {
				t.Fatal(err)
			}
		}
		tick(t, s, f, t0)
		return [3]int{who, records, states}
	}
	equal(t, counts(1000), counts(1))
}
