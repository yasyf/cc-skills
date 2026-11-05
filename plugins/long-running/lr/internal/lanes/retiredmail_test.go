package lanes

import (
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const heir = "conflicts-3"

func cardWake(seq int64, lane, said string, at time.Time) Record {
	return rec(seq, boardWatch, "decide", at, "WAKE card-chat: the owner answered your card: "+said, lane).withTopic(CardWakeTopic + "card-chat")
}

func stoppedOnACard(t *testing.T, s *store.Store) *fake {
	t.Helper()
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "stopped", t0, "Waiting on card-chat, which wakes this lane."))
	f.peers = nil
	tick(t, s, f, t0)
	equal(t, []any{row(t, s, lane2).State, row(t, s, lane2).RetiredBy}, []any{store.LaneRetired, retiredStopped})
	f.posts = nil
	return f
}

func TestAnAnswerToAGoneLanesCardRelaunchesItOnceWithTheAnswerAndASecondAnswerLaunchesNothing(t *testing.T) {
	s := memory(t)
	f := stoppedOnACard(t, s)

	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	out := tick(t, s, f, t0.Add(mins(5)))

	equal(t, f.launches, []string{heir})
	equal(t, len(out.Relaunched), 1)
	equal(t, row(t, s, lane2).RetiredBy, heir)
	contains(t, f.briefs[heir], "picked Combined")
	contains(t, f.briefs[heir], "`cci show 3`")
	contains(t, f.briefs[heir], "cut a fresh worktree")

	f.feed = append(f.feed, cardWake(4, lane2, "picked Separate", t0.Add(mins(6))))
	tick(t, s, f, t0.Add(mins(6)))
	f.feed = append(f.feed, cardWake(5, heir, "picked Separate", t0.Add(mins(7))))
	tick(t, s, f, t0.Add(mins(7)))

	equal(t, f.launches, []string{heir})
}

func TestTwoAnswersInOneTickLaunchOneLaneCarryingBoth(t *testing.T) {
	s := memory(t)
	f := stoppedOnACard(t, s)

	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))), cardWake(4, lane2, "picked Separate", t0.Add(mins(5))))
	tick(t, s, f, t0.Add(mins(5)))

	equal(t, f.launches, []string{heir})
	contains(t, f.briefs[heir], "picked Combined")
	contains(t, f.briefs[heir], "picked Separate")
}

func TestAnAnswerToALaneTheSupervisorAlreadyRelaunchedOnceStillRelaunchesIt(t *testing.T) {
	s := memory(t)
	f := stoppedOnACard(t, s)
	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	tick(t, s, f, t0.Add(mins(5)))
	f.feed = append(f.feed, rec(4, heir, "stopped", t0.Add(mins(6)), "Waiting on card-next."))
	tick(t, s, f, t0.Add(mins(6)))
	equal(t, spentRelaunches(s, row(t, s, heir), "stopped", t0.Add(mins(6))) != "", true)

	f.feed = append(f.feed, cardWake(5, heir, "picked Later", t0.Add(mins(7))))
	tick(t, s, f, t0.Add(mins(7)))

	equal(t, f.launches, []string{heir, "conflicts-4"})
	contains(t, f.briefs["conflicts-4"], "picked Later")
}

func TestAWakeForARetiredLaneStillAtItsPromptWakesItAndLaunchesNothing(t *testing.T) {
	s := memory(t)
	f := stoppedOnACard(t, s)
	resting := who(lane2, true, t0)
	resting.Dispatch = "ctx_resting"
	f.peers = []Row{resting}

	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	out := tick(t, s, f, t0.Add(mins(5)))

	equal(t, f.launches, []string(nil))
	equal(t, f.wakes, []string{lane2})
	equal(t, out.Woken, []string{lane2})
	contains(t, pageText(f.pages[0], "why"), "picked Combined")
	equal(t, row(t, s, lane2).Page == nil, true)
}

func TestAWakeARestingLaneDeclinesIsTriedAgainNextTick(t *testing.T) {
	s := memory(t)
	f := stoppedOnACard(t, s)
	resting := who(lane2, true, t0)
	resting.Dispatch = "ctx_resting"
	f.peers = []Row{resting}
	declining := true
	woken := 0
	f.deps.Wake = func(string, Row, map[string]any) (bool, error) {
		woken++
		return !declining, nil
	}

	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	tick(t, s, f, t0.Add(mins(5)))
	equal(t, woken, 1)
	declining = false
	tick(t, s, f, t0.Add(mins(6)))
	tick(t, s, f, t0.Add(mins(7)))

	equal(t, woken, 2)
	equal(t, f.launches, []string(nil))
}

func TestWhichRecordsReachAGoneLane(t *testing.T) {
	for _, tt := range []struct {
		name     string
		record   Record
		launches []string
	}{
		{"a lane's answer", rec(3, "meta-desk-3", "answer", t0.Add(mins(5)), "Yes, build it.", lane2), []string{heir}},
		{"a lane's ask", rec(3, "owner-relay", "ask", t0.Add(mins(5)), "Which branch holds this?", lane2), []string{heir}},
		{"a lane's decide", rec(3, "meta-desk-3", "decide", t0.Add(mins(5)), "Take option two.", lane2), []string{heir}},
		{"a report tells it nothing to do", rec(3, "install-desk", "report", t0.Add(mins(5)), "Installed.", lane2), nil},
		{"a note tells it nothing to do", rec(3, "meta-desk-3", "note", t0.Add(mins(5)), "FYI.", lane2), nil},
		{"the supervisor's own stand-down", rec(3, Self, "stand-down", t0.Add(mins(5)), "stopped, so mail to it passes on", lane2), nil},
		{"the supervisor asking for an overdue item", rec(3, Self, "ask", t0.Add(mins(5)), "org/repo#7 row-wait overdue", lane2), []string{heir}},
		{"the supervisor's report", rec(3, Self, "report", t0.Add(mins(5)), "RETIRED for owning no PR.", lane2), nil},
		{"the board link saying the card drew", rec(3, boardLink, "answer", t0.Add(mins(5)), "card-chat is on his board", lane2), nil},
		{"a record to another lane", rec(3, "meta-desk-3", "answer", t0.Add(mins(5)), "Yes.", "other-1"), nil},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s := memory(t)
			f := stoppedOnACard(t, s)
			f.feed = append(f.feed, tt.record)
			tick(t, s, f, t0.Add(mins(5)))
			equal(t, f.launches, tt.launches)
		})
	}
}

func TestALaneThatFinishedIsRelaunchedForHisAnswerButNotForOtherMail(t *testing.T) {
	finished := func(t *testing.T) (*store.Store, *fake) {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.feed = append(f.feed, rec(2, lane2, "done", t0, "Delivered."))
		f.peers = nil
		tick(t, s, f, t0)
		equal(t, row(t, s, lane2).State, store.LaneRetired)
		return s, f
	}

	s, f := finished(t)
	f.feed = append(f.feed, rec(3, "owner-relay", "ask", t0.Add(mins(5)), "Which branch holds this?", lane2))
	tick(t, s, f, t0.Add(mins(5)))
	equal(t, f.launches, []string(nil))

	s, f = finished(t)
	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	tick(t, s, f, t0.Add(mins(5)))
	equal(t, f.launches, []string{heir})
}

func TestAnOverdueItemAsksALaneThatStoppedItselfAndRootForOneThatFinished(t *testing.T) {
	s := memory(t)
	stoppedOnACard(t, s)
	owner, err := ItemOwner(s, testDrive, lane2, 7)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, owner, lane2)

	s = memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "Delivered."))
	f.peers = nil
	tick(t, s, f, t0)
	if owner, err = ItemOwner(s, testDrive, lane2, 7); err != nil {
		t.Fatal(err)
	}
	equal(t, owner, Root)
}

func TestAStandingDeskIsLeftToItsOwnDutyCheck(t *testing.T) {
	s := memory(t)
	stoppedOnACard(t, s)
	if err := s.SetLaneRole(lane2, roleDesk); err != nil {
		t.Fatal(err)
	}

	equal(t, restedOnItsOwnEnd(row(t, s, lane2)), false)
}

func TestAGoneLaneWhoseBriefIsGoneIsHeldWithHisAnswerInTheBlocker(t *testing.T) {
	s := memory(t)
	f := newFake(t, []Record{spawned(1, lane2, filepath.Join(t.TempDir(), "absent.md"), t0.Add(-time.Hour), map[string]string{"model": "opus", "effort": "xhigh"})}, []Row{who(lane2, true, t0.Add(-mins(1)))})
	tick(t, s, f, t0.Add(-mins(1)))
	f.feed = append(f.feed, rec(2, lane2, "stopped", t0, "Waiting on card-chat."))
	f.peers = nil
	tick(t, s, f, t0)

	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	out := tick(t, s, f, t0.Add(mins(5)))

	equal(t, f.launches, []string(nil))
	equal(t, out.Held, []string{lane2})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	contains(t, blockers[0].Text, "picked Combined")
	equal(t, row(t, s, lane2).Page == nil, true)
}

func TestNoMailToARetiredLaneCostsNoPeerRead(t *testing.T) {
	for _, retired := range []int{1, 1000} {
		s := memory(t)
		f := stoppedOnACard(t, s)
		for i := range retired {
			name := fmt.Sprintf("old-%d-1", i)
			registered(t, s, store.Lane{Name: name})
			if err := s.SetLaneState(name, store.LaneRetired, t0, retiredStopped, ""); err != nil {
				t.Fatal(err)
			}
		}
		reads := 0
		f.deps.Who = func([]string) ([]Row, error) { reads++; return nil, nil }

		tick(t, s, f, t0.Add(mins(5)))

		equal(t, reads, 1)
	}
}
