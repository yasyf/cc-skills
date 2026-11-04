package lanes

import (
	"fmt"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
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

func TestADeadLaneThatHoldsNothingRetiresOnceSilentPastTheThresholdAndIsToldItStoodDown(t *testing.T) {
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
	equal(t, reports[0].Text, "RETIRED for owning no PR, holding no standing duty, and posting nothing for 60 minutes or more: conflicts-2 (it stopped, quiet for 60 min).")
	standDowns := f.ofKind("stand-down")
	equal(t, len(standDowns), 1)
	equal(t, []any{standDowns[0].To, standDowns[0].Successor, standDowns[0].Text}, []any{[]string{lane2}, "", "conflicts-2 stood down: the supervisor retired it (silent)."})

	tick(t, s, f, t0.Add(mins(61)))

	equal(t, len(f.ofKind("stand-down")), 1)
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

func TestAColdLaneHoldingNothingIsHeldForRootPastTheThresholdNeverRetired(t *testing.T) {
	for name, peers := range map[string][]Row{
		"an unverifiable host": {{Lane: lane2, SeenAt: t0, Dispatch: "ctx_1", Unverified: "has an Orca agent that reads unverifiable (host_unavailable)"}},
		"no process row":       nil,
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, rec(2, lane2, "state", t0, "reading the logs", Root))
			f.peers = peers

			tick(t, s, f, t0.Add(mins(59)))
			equal(t, []any{row(t, s, lane2).State, len(f.posts)}, []any{store.LaneDesired, 0})

			out := tick(t, s, f, t0.Add(mins(60)))

			equal(t, out.Held, []string{lane2})
			equal(t, []any{row(t, s, lane2).State, len(out.Retired), len(f.launches)}, []any{store.LaneHeld, 0, 0})
			blockers := f.ofKind("blocker")
			equal(t, len(blockers), 1)
			equal(t, blockers[0].To, []string{Root})
		})
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
	counts := func(n int) [6]int {
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
		return [6]int{peers, records, states, owed, len(f.launches), len(f.ofKind("report"))}
	}
	equal(t, counts(1000), counts(1))
}

func TestAWorkerDoneFromALaneNoRecordOfWhichReachedTheBusIsHeldForRoot(t *testing.T) {
	for name, done := range map[string]func(f *fake){
		"its orca row": func(f *fake) {
			finished := who(lane2, false, t0.Add(-mins(20)))
			finished.Succeeded = true
			f.peers = []Row{finished}
		},
		"the runner's outcome line": func(f *fake) {
			f.feed = append(f.feed, rec(2, runner, "report", t0.Add(-mins(1)), "OUTCOME msg_b21ededd168d conflicts-2: worker_done succeeded dispatch=ctx_320e271b3112"))
			f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}
		},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			done(f)

			tick(t, s, f, t0)

			equal(t, row(t, s, lane2).State, store.LaneHeld)
			blockers := f.ofKind("blocker")
			equal(t, len(blockers), 1)
			equal(t, blockers[0].To, []string{Root})
			contains(t, blockers[0].Text, "held, not retired: it reported worker_done, but none of its records ever reached the bus")
			contains(t, blockers[0].Text, "`lr request done conflicts-2`")
		})
	}
}

func TestARetiredLanesUnownedOpenPRsOnItsBranchGoToRootOnce(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := newFake(t, []Record{spawned(1, "journal-apply-threads-1", brief, t0.Add(-time.Hour), nil), spawned(2, "chat-spin-1", brief, t0.Add(-time.Hour), nil)}, []Row{who("journal-apply-threads-1", true, t0), who("chat-spin-1", true, t0)})
	tick(t, s, f, t0.Add(-mins(1)))
	if err := s.SetLanePRs("chat-spin-1", []int{48990}); err != nil {
		t.Fatal(err)
	}
	f.branches = map[string][]int{"journal-apply-threads": {48999}, "journal-apply-threads/restack": {49000}, "journal-apply": {1}, "chat-spin": {48990}}
	f.feed = append(f.feed, rec(3, "journal-apply-threads-1", "done", t0, "threads pick shipped", Root))

	tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(2)))

	equal(t, owners(t, s, 48999, 49000, 1, 48990), map[int]string{48999: Root, 49000: Root, 1: "", 48990: "chat-spin-1"})
	reports := f.ofKind("report")
	equal(t, len(reports), 1)
	equal(t, reports[0].PRs, []int{48999, 49000})
	contains(t, reports[0].Text, "journal-apply-threads-1 left #48999 on journal-apply-threads; journal-apply-threads-1 left #49000 on journal-apply-threads/restack")
	equal(t, f.branchReads, 1)
}

func TestOpenBranchesComeFromTheRowsAndLeaveOutEndedOnes(t *testing.T) {
	s := memory(t)
	rows := []store.ReviewRow{
		{Repo: "o/r", Number: 1, Branch: "open-one", State: prrow.StateOpen},
		{Repo: "o/r", Number: 2, Branch: "open-one", State: prrow.StateOpen},
		{Repo: "o/r", Number: 3, Branch: "landed", State: prrow.StateLanded},
		{Repo: "o/r", Number: 4, Branch: "closed", State: prrow.StateClosed},
		{Repo: "o/r", Number: 5, Branch: "replaced", State: prrow.StateSuperseded},
		{Repo: "other/r", Number: 6, Branch: "other-repo", State: prrow.StateOpen},
	}
	if err := s.PutReviewRows(rows); err != nil {
		t.Fatal(err)
	}

	branches, err := openBranches(s, "o/r")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, branches, map[string][]int{"open-one": {1, 2}})
}

func TestTheBranchReadIsOnePerTickAtOneRetiredLaneAndAtAThousand(t *testing.T) {
	reads := func(n int) int {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.peers = []Row{who(lane2, true, t0)}
		f.branches = map[string][]int{}
		for i := range n {
			name := fmt.Sprintf("ended-%d", i)
			if _, err := s.WantLane(store.Lane{Name: name, Brief: "/briefs/x.md", Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneRetired}, t0.Add(-time.Hour)); err != nil {
				t.Fatal(err)
			}
			f.branches[name] = []int{50000 + i}
		}
		tick(t, s, f, t0)
		equal(t, len(f.ofKind("report")[0].PRs), n)
		return f.branchReads
	}
	equal(t, reads(1000), reads(1))
}

func TestADeskMetaOrReleaseLaneIsStandingSoItsDeathRelaunchesItWithNoStandingLine(t *testing.T) {
	for _, role := range []string{"desk", RoleMeta, "release"} {
		t.Run(role, func(t *testing.T) {
			s, brief := memory(t), writeBrief(t)
			f := newFake(t, []Record{spawned(1, "drive-watchdog-4", brief, t0.Add(-time.Hour), map[string]string{"role": role})}, []Row{who("drive-watchdog-4", true, t0.Add(-mins(1)))})
			tick(t, s, f, t0.Add(-mins(1)))
			f.peers = []Row{who("drive-watchdog-4", false, t0.Add(-3*time.Hour))}

			out := tick(t, s, f, t0.Add(2*time.Hour))

			equal(t, []any{len(out.Relaunched), len(out.Retired), f.launches}, []any{1, 0, []string{"drive-watchdog-5"}})
		})
	}
}
