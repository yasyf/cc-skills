package lanes

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	unblock    = "pr-unblock-9"
	theirs     = 47613
	driveRepo  = "o/r"
	driveLogin = "drive-bot"
)

func withUnblock(t *testing.T, s *store.Store, f *fake, state store.LaneState) {
	t.Helper()
	gone := storeLane("pr-unblock-8")
	gone.State, gone.RetiredBy = store.LaneRetired, unblock
	member := storeLane(unblock)
	member.State = state
	for _, lane := range []store.Lane{gone, member} {
		if _, err := s.WantLane(lane, t0.Add(-time.Hour)); err != nil {
			t.Fatal(err)
		}
	}
	if isLive(state) {
		f.peers = append(f.peers, who(unblock, true, t0))
	}
}

func TestARelaunchCarriesTheDrivesOwnPRsAndDropsAnotherPersonsFromTheRegistry(t *testing.T) {
	brief := writeBrief(t)
	cases := map[string]func(f *fake){
		"the supervisor relaunches a dead lane": func(f *fake) { f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))} },
		"a successor spawns under the next number": func(f *fake) {
			f.feed = append(f.feed, spawned(2, "conflicts-3", brief, t0, nil))
			f.peers = []Row{who(lane2, true, t0), who("conflicts-3", true, t0)}
		},
	}
	for name, relaunch := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: driveRepo, Number: 46001, State: prrow.StateOpen}}); err != nil {
				t.Fatal(err)
			}
			if _, err := s.PutFact(driveRepo, 46003, "", store.AuthorFact, driveLogin, t0); err != nil {
				t.Fatal(err)
			}
			f := adopted(t, s, brief, "", 46001, 46003, theirs)
			f.deps.Ours = func(s *store.Store, prs []int) ([]int, error) { return s.DrivePRs(driveRepo, driveLogin, prs) }
			relaunch(f)

			tick(t, s, f, t0)

			equal(t, row(t, s, lane2).State, store.LaneRetired)
			equal(t, owners(t, s, 46001, 46003, theirs), map[int]string{46001: "conflicts-3", 46003: "conflicts-3", theirs: ""})
		})
	}
}

func TestAReleaseRequestDropsOnlyThePRsTheLaneHoldsFromItsRow(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 46001, theirs)
	if err := s.Own("restack-children-2", []int{46002}); err != nil {
		t.Fatal(err)
	}
	f.feed = append(f.feed, ask(2, "release", lane2, t0, []int{theirs, 46002}, "meta-desk-1", nil))

	tick(t, s, f, t0)

	equal(t, owners(t, s, 46001, theirs, 46002), map[int]string{46001: lane2, theirs: "", 46002: "restack-children-2"})
	equal(t, row(t, s, lane2).State, store.LaneDesired)
}

func TestTheReleaseRequestNamesEachPRItDropsAndPostsOneTypedRecord(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv(LaneEnv, "meta-desk-1")
	a, s := driveState(t)
	if _, err := s.WantLane(storeLane(unblock), t0); err != nil {
		t.Fatal(err)
	}

	if _, err := execute(t, RequestCommand(a), "release", unblock); err == nil || !strings.Contains(err.Error(), "names each PR it drops") {
		t.Fatalf("err %v", err)
	}
	if _, err := execute(t, RequestCommand(a), "release", prUnblock, "--pr", strconv.Itoa(theirs)); err != nil {
		t.Fatal(err)
	}

	records, err := client.Tail(context.Background(), client.Query{Drive: testDrive})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(records), 1)
	r := recordOf(records[0])
	equal(t, []any{r.Lane, isRequest(r), r.Attr("verb"), r.Attr("lane"), r.PRs()}, []any{"meta-desk-1", true, "release", unblock, []int{theirs}})
}

func TestARetiredLaneHoldingAnOpenDrivePRGivesItToPRUnblocksLiveMemberWithANoteToRoot(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	withUnblock(t, s, f, store.LaneDesired)
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "shipped; lands after review", Root))
	f.states = map[int]string{46001: Broken}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).State, store.LaneRetired)
	equal(t, owners(t, s, 46001), map[int]string{46001: unblock})
	notes := f.ofKind("report")
	equal(t, len(notes), 1)
	equal(t, []any{notes[0].To, notes[0].PRs}, []any{[]string{Root}, []int{46001}})
	contains(t, notes[0].Text, "HANDED ON #46001 to pr-unblock-9, the default owner, as `lr request owner pr-unblock-9 --pr 46001` would: conflicts-2 ended naming no successor.")
	equal(t, len(f.ofKind("blocker")), 0)
}

func TestAnotherPersonsPRARetiredLaneHeldGoesToNoLaneAndRaisesNothing(t *testing.T) {
	for _, member := range []store.LaneState{store.LaneDesired, store.LaneRetired} {
		t.Run(string(member), func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "", theirs)
			withUnblock(t, s, f, member)
			f.foreign = map[int]bool{theirs: true}
			f.feed = append(f.feed, rec(2, lane2, "done", t0, "shipped", Root))
			f.states = map[int]string{theirs: Broken}

			tick(t, s, f, t0)
			tick(t, s, f, t0.Add(mins(1)))

			equal(t, owners(t, s, theirs), map[int]string{theirs: ""})
			equal(t, []any{len(f.ofKind("report")), len(f.ofKind("blocker"))}, []any{0, 0})
		})
	}
}

func TestWithNoLivePRUnblockMemberRootGetsTheBlockerForARetiredLanesBrokenPR(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	withUnblock(t, s, f, store.LaneRetired)
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "shipped", Root))
	f.states = map[int]string{46001: Broken}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	equal(t, owners(t, s, 46001), map[int]string{46001: lane2})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{Root})
	contains(t, blockers[0].Text, "#46001 needs work and no lane owns it")
	equal(t, len(f.ofKind("report")), 0)
}

func TestAStandDownNamingNoSuccessorGivesTheDrivesPRsToPRUnblocksLiveMember(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001, theirs)
	f.peers = []Row{who(lane2, true, t0)}
	withUnblock(t, s, f, store.LaneDesired)
	f.foreign = map[int]bool{theirs: true}
	f.feed = append(f.feed, rec(2, Root, "stand-down", t0, "stand down, last-read=1", lane2))

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, owners(t, s, 46001, theirs), map[int]string{46001: unblock, theirs: ""})
	reports := f.ofKind("report")
	equal(t, len(reports), 2)
	equal(t, []any{reports[0].To, reports[0].Text}, []any{[]string{OwnerRelay}, "RETIRED conflicts-2 by stand-down #2, its PRs to pr-unblock-9."})
	equal(t, []any{reports[1].To, reports[1].PRs}, []any{[]string{Root}, []int{46001}})
	contains(t, reports[1].Text, "HANDED ON #46001 to pr-unblock-9")
}

func TestARetiredLanesOrphanedPRsGoToPRUnblocksLiveMember(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	withUnblock(t, s, f, store.LaneDesired)
	f.branches = map[string][]int{"conflicts": {48999}}
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "shipped", Root))

	tick(t, s, f, t0)

	equal(t, owners(t, s, 48999), map[int]string{48999: unblock})
	reports := f.ofKind("report")
	equal(t, len(reports), 1)
	contains(t, reports[0].Text, "ORPHANED PRs now owned by pr-unblock-9, the default owner, since no lane owned them when their lanes retired: conflicts-2 left #48999 on conflicts.")
}

func TestHandingRetiredLanesPRsOnReadsTheSameAtOneLaneAndAtMany(t *testing.T) {
	counts := func(n int) [3]int {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		withUnblock(t, s, f, store.LaneDesired)
		var states int
		base := f.deps.PRStates
		f.deps.PRStates = func(numbers []int) (map[int]string, error) { states++; return base(numbers) }
		for i := range n {
			ended := storeLane(fmt.Sprintf("ended-%d", i))
			ended.State, ended.RetiredBy, ended.PRs = store.LaneRetired, "done", []int{50000 + i}
			if _, err := s.WantLane(ended, t0.Add(-time.Hour)); err != nil {
				t.Fatal(err)
			}
		}

		tick(t, s, f, t0)

		owned, err := s.Owners()
		if err != nil {
			t.Fatal(err)
		}
		equal(t, len(owned[unblock]), n)
		return [3]int{f.oursReads, states, len(f.ofKind("report"))}
	}
	equal(t, counts(scaletest.N()), counts(1))
}
