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
			f := adopted(t, s, writeBrief(t), "", 47001)
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

func TestALaneParkedOnAClockIsLeftAloneByReleasesAndPagedOverdueOnceItRunsOut(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	parked := rec(2, lane2, "blocked", t0.Add(-mins(10)), "lr wait --wake on the canary log")
	parked.Fields.Attrs = map[string]string{"until": "45m"}
	f.feed = append(f.feed, parked)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(10)))}

	waiting := tick(t, s, f, t0)
	equal(t, len(waiting.Relaunched)+len(f.launches), 0)
	equal(t, row(t, s, lane2).Waiting, int64(2))

	f.feed = append(f.feed, rec(3, "release-5", "release", t0.Add(mins(1)), "released api").withRefs(Refs{Targets: []string{"api"}}))
	cut := tick(t, s, f, t0.Add(mins(2)))
	equal(t, []any{len(cut.Woken), len(f.launches), row(t, s, lane2).Waiting, row(t, s, lane2).Page == nil}, []any{0, 0, int64(2), true})

	tick(t, s, f, t0.Add(mins(34)))
	equal(t, row(t, s, lane2).Waiting, int64(2))

	tick(t, s, f, t0.Add(mins(35)))
	equal(t, row(t, s, lane2).Waiting, int64(0))
	contains(t, pageText(row(t, s, lane2).Page, "why"), "you are overdue")
	equal(t, len(f.launches), 0)
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

func TestARootReplyToTheRunnersDecideLeavesALaneParkedOnItsPRUntilThePRLands(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed,
		rec(2, lane2, "blocked", t0.Add(-mins(10)), "lands after #48099").withRefs(prs(48099)),
		rec(3, runner, "decide", t0.Add(-mins(9)), "DECIDE msg_s1 conflicts-2: Worker stopped without reporting", Root),
		rec(4, Root, "answer", t0.Add(-mins(8)), "Known", runner).withRe(3),
	)
	f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0, Dispatch: "ctx_af31953664f5"}}
	parked := tick(t, s, f, t0)

	equal(t, []any{len(parked.Woken), row(t, s, lane2).Waiting, liveness(t, s, lane2).Woke}, []any{0, int64(2), (*string)(nil)})

	f.feed = append(f.feed, rec(5, runner, kindLanded, t0.Add(mins(1)), "LANDED #48099 as ef104ad", Root).withRefs(prs(48099)))
	landed := tick(t, s, f, t0.Add(mins(2)))

	equal(t, landed.Woken, []string{lane2})
	equal(t, *liveness(t, s, lane2).Woke, store.Stamp(t0.Add(mins(1))))
}

func liveness(t *testing.T, s *store.Store, name string) Liveness {
	t.Helper()
	rows, err := LivenessRows(s)
	if err != nil {
		t.Fatal(err)
	}
	for _, one := range rows {
		if one.Lane == name {
			return one
		}
	}
	t.Fatalf("%s has no liveness row", name)
	return Liveness{}
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
	f := adopted(t, s, writeBrief(t), "", 47001)
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

func TestDoneRetiresALaneWithAnOpenPRAndABrokenOneReachesTheCoordinatorOnce(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 47001)
	f.feed = append(f.feed, rec(2, lane2, "done", t0.Add(-time.Hour), "ready; lands after the stack below", Root))
	f.peers = []Row{who(lane2, false, t0.Add(-time.Hour))}
	f.states = map[int]string{47001: NeedsWork}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy, lane.Waiting, lane.Awaits}, []any{store.LaneRetired, "done", int64(0), ""})
	equal(t, len(f.ofKind("blocker")), 0)

	f.states = map[int]string{47001: Broken}
	tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(2)))
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{Root})
	contains(t, blockers[0].Text, "#47001 needs work and no lane owns it")

	f.states = map[int]string{47001: Closed}
	tick(t, s, f, t0.Add(mins(3)))
	owned, err := s.Owners()
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(owned[lane2]), 0)
}

func TestALaneThatPostedDoneStaysRetiredWhateverItPostsAfter(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 1907)
	f.feed = append(f.feed,
		rec(2, lane2, "done", t0, "done: #1907 merged", Root).withRefs(prs(1907)),
		rec(3, lane2, "opened", t0.Add(mins(6)), "opened #1920").withRefs(prs(1920)),
		rec(4, lane2, "answer", t0.Add(mins(8)), "#1920 drops the section", "progress-desk-2"),
	)
	f.peers = []Row{who(lane2, false, t0.Add(mins(8)))}

	tick(t, s, f, t0.Add(mins(9)))
	out := tick(t, s, f, t0.Add(time.Hour))

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	equal(t, row(t, s, lane2).State, store.LaneRetired)
}

func TestABrokenPRWakesTheLaneParkedOnItOnceUntilANewHead(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 47001)
	park := func(seq int64, at time.Time) Record {
		return rec(seq, lane2, "blocked", at, "waiting on CI").withRefs(prs(47001))
	}
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
