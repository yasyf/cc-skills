package lanes

import (
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
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

func TestAnAnswerToAGoneLanesCardGoesToTheLiveLaneRootGaveThePickTo(t *testing.T) {
	s := memory(t)
	f := stoppedOnACard(t, s)
	f.feed = append(f.feed,
		spawned(3, "chat-pick-1", writeBrief(t), t0.Add(mins(1)), nil),
		rec(4, Root, "report", t0.Add(mins(2)), "lane=chat-pick-1: build his pick on card-chat"),
	)
	f.peers = []Row{who("chat-pick-1", true, t0.Add(mins(2)))}
	tick(t, s, f, t0.Add(mins(2)))

	f.feed = append(f.feed, cardWake(5, lane2, "picked Combined", t0.Add(mins(5))))
	f.peers = []Row{who("chat-pick-1", true, t0.Add(mins(5)))}
	out := tick(t, s, f, t0.Add(mins(5)))

	equal(t, []any{len(f.launches), len(out.Relaunched)}, []any{0, 0})
	equal(t, row(t, s, lane2).State, store.LaneRetired)
	contains(t, pageText(row(t, s, "chat-pick-1").Page, "why"), "the owner answered card-chat, which root gave you after "+lane2+" drew it")
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
	equal(t, spentRelaunches(s, f.deps, row(t, s, heir), nil, "stopped", map[string]bool{}, t0.Add(mins(6))) != "", true)

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

func TestOnlyHisCardAnswerRelaunchesALaneThatStopped(t *testing.T) {
	for _, tt := range []struct {
		name     string
		record   Record
		launches []string
	}{
		{"his card answer", cardWake(3, lane2, "picked Combined", t0.Add(mins(5))), []string{heir}},
		{"root's answer to its stop", rec(3, Root, "answer", t0.Add(mins(5)), "Post the notebook and STOPPED.", lane2).withRe(2), nil},
		{"a lane's answer", rec(3, "meta-desk-3", "answer", t0.Add(mins(5)), "Yes, build it.", lane2), nil},
		{"a lane's ask", rec(3, "owner-relay", "ask", t0.Add(mins(5)), "Which branch holds this?", lane2), nil},
		{"a lane's decide", rec(3, "meta-desk-3", "decide", t0.Add(mins(5)), "Take option two.", lane2), nil},
		{"a report tells it nothing to do", rec(3, "install-desk", "report", t0.Add(mins(5)), "Installed.", lane2), nil},
		{"a note tells it nothing to do", rec(3, "meta-desk-3", "note", t0.Add(mins(5)), "FYI.", lane2), nil},
		{"the supervisor's own stand-down", rec(3, Self, "stand-down", t0.Add(mins(5)), "stopped, so mail to it passes on", lane2), nil},
		{"the supervisor asking for an overdue item", rec(3, Self, "ask", t0.Add(mins(5)), "org/repo#7 row-wait overdue", lane2), nil},
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

func finished(t *testing.T) (*store.Store, *fake) {
	t.Helper()
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "Delivered."))
	f.peers = nil
	tick(t, s, f, t0)
	equal(t, []any{row(t, s, lane2).State, row(t, s, lane2).RetiredBy}, []any{store.LaneRetired, "done"})
	return s, f
}

func mailOf(t *testing.T, s *store.Store, lane string) []int64 {
	t.Helper()
	var seqs []int64
	for _, order := range unreadOrders(t, s) {
		if order.Lane == lane {
			seqs = append(seqs, order.Seq)
		}
	}
	return seqs
}

func TestALaneThatFinishedIsRelaunchedForHisAnswerButNotForOtherMail(t *testing.T) {
	s, f := finished(t)
	f.feed = append(f.feed, rec(3, "owner-relay", "ask", t0.Add(mins(5)), "Which branch holds this?", lane2))
	tick(t, s, f, t0.Add(mins(5)))
	equal(t, f.launches, []string(nil))

	s, f = finished(t)
	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	tick(t, s, f, t0.Add(mins(5)))
	equal(t, f.launches, []string{heir})
}

func (r Record) withNum(num string) Record { r.Num = num; return r }

func TestRootOrTheSupervisorNamingAFinishedLaneLaunchesNothingAndTellsRootOnce(t *testing.T) {
	at := t0.Add(mins(5))
	const num = "#29934@6442ae88"
	for _, tt := range []struct {
		name    string
		record  Record
		notices int
	}{
		{"root's answer to its stem", rec(3, Root, "answer", at, "Read #50782 and ship the trim.", Stem(lane2)).withNum(num), 1},
		{"root's answer naming it in --to and as lane=", rec(3, Root, "answer", at, "lane="+Stem(lane2)+" owns item 86: ship the trim.", Stem(lane2), "slack-bugs").withNum(num), 1},
		{"root's answer naming it only as lane=", rec(3, Root, "answer", at, "lane="+lane2+" owns item 86.", "slack-bugs").withNum(num), 1},
		{"the supervisor's ask", rec(3, Self, "ask", at, "org/repo#7 row-wait overdue", lane2).withNum(num), 1},
		{"root's answer closing its done", rec(3, Root, "answer", at, "closed: delivered with zero PRs.", lane2).withResolves(2).withNum(num), 0},
		{"root's note asks nothing", rec(3, Root, "note", at, "lane="+Stem(lane2)+" shipped #50782.", lane2).withNum(num), 0},
		{"another lane's answer", rec(3, "meta-desk-3", "answer", at, "lane="+Stem(lane2)+": yes.", lane2).withNum(num), 0},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s, f := finished(t)
			f.feed = append(f.feed, tt.record, tt.record)
			tick(t, s, f, at)
			tick(t, s, f, at.Add(mins(1)))

			equal(t, f.launches, []string(nil))
			equal(t, row(t, s, lane2).State, store.LaneRetired)
			notices := f.ofKind("blocker")
			equal(t, len(notices), tt.notices)
			if tt.notices == 0 {
				return
			}
			equal(t, []any{notices[0].To, notices[0].LaneRef, notices[0].Re}, []any{[]string{Root}, lane2, int64(3)})
			equal(t, notices[0].Text, "ASSIGNMENT-UNREAD cci "+num+" names "+lane2+", retired at Oct 31 10:00 AM with no successor, so no lane reads it; name a live lane or relaunch it")
		})
	}
}

func TestAssignmentUnreadNeverNamesAFixedLaneOrARecordAnsweredInTheSameTick(t *testing.T) {
	at := t0.Add(mins(5))
	overdue := rec(3, Self, "ask", at, "org/repo#7 row-wait overdue", lane2).withNum("#3")
	for _, tt := range []struct {
		name    string
		retired string
		feed    []Record
		notices int
	}{
		{"an ask nobody answered", "", []Record{overdue}, 1},
		{"an ask root resolved", "", []Record{overdue, rec(4, Root, "answer", at, "Nothing for a lane to do.", Self).withResolves(3)}, 0},
		{"an ask owner-relay answered", "", []Record{overdue, rec(4, OwnerRelay, "answer", at, "Routed; nothing to carry.", Self).withRe(3)}, 0},
		{"an ask to root's retired row", Root, []Record{rec(3, Self, "ask", at, "the owner record is yours to carry", Root).withNum("#3")}, 0},
		{"an ask to owner-relay's retired row", OwnerRelay, []Record{rec(3, Self, "ask", at, "the card answer needs routing", OwnerRelay).withNum("#3")}, 0},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s, f := finished(t)
			if tt.retired != "" {
				if _, err := s.WantLane(store.Lane{Name: tt.retired, State: store.LaneDesired}, t0); err != nil {
					t.Fatal(err)
				}
				if err := s.SetLaneState(tt.retired, store.LaneRetired, t0, "done", ""); err != nil {
					t.Fatal(err)
				}
			}
			f.feed = append(f.feed, tt.feed...)
			tick(t, s, f, at)

			equal(t, len(f.ofKind("blocker")), tt.notices)
		})
	}
}

func TestRootNamingARetiredLaneWhoseSuccessorIsLiveRoutesTheRecordToIt(t *testing.T) {
	s, f := finished(t)
	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	tick(t, s, f, t0.Add(mins(5)))
	f.feed = append(f.feed, rec(4, heir, "report", t0.Add(mins(6)), "Up; building the pick."))
	f.peers = []Row{who(heir, true, t0.Add(mins(6)))}
	tick(t, s, f, t0.Add(mins(6)))

	f.feed = append(f.feed, rec(5, Root, "answer", t0.Add(mins(7)), "Also take item 87.", lane2).withNum("#5"))
	tick(t, s, f, t0.Add(mins(7)))

	equal(t, f.launches, []string{heir})
	equal(t, row(t, s, lane2).Page == nil, true)
	equal(t, mailOf(t, s, heir), []int64{5})
	equal(t, len(f.ofKind("blocker")), 0)
}

func TestARegistryRequestCCIForwardsToTheSupervisorsRetiredOwnerRaisesNoNotice(t *testing.T) {
	s, f := finished(t)
	request := ask(3, "owner", "pr-unblock-18", t0.Add(mins(5)), []int{50314}, "", map[string]string{"last-read": "2"}).withNum("#3")
	request.To = append(request.To, Stem(lane2))
	f.feed = append(f.feed, request)
	tick(t, s, f, t0.Add(mins(5)))

	equal(t, isRequest(request), true)
	equal(t, f.launches, []string(nil))
	equal(t, len(f.ofKind("blocker")), 0)
}

func TestAnOverdueItemOfALaneThatStoppedOrFinishedAsksRoot(t *testing.T) {
	s := memory(t)
	stoppedOnACard(t, s)
	owner, err := ItemOwner(s, testDrive, lane2, 7)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, owner, Root)

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

	equal(t, stoppedToWait(row(t, s, lane2)), false)
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
	for _, retired := range []int{1, scaletest.N()} {
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
