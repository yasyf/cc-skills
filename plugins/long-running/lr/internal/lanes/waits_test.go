package lanes

import (
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	waitRepo = "org/repo"
	waitHead = "e718a7434fa01ee9835f992a9a647a825350732c"
	nextHead = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
)

func waitingRow(number int, lane string, change func(*store.ReviewRow)) store.ReviewRow {
	row := store.ReviewRow{
		Repo: waitRepo, Number: number, Title: "a change", Lane: lane, Head: waitHead, State: prrow.StateOpen, ParentState: prrow.ParentLanded,
		CI: prrow.CIGreen, Approvers: "a-reviewer", Grade: prrow.GradePending, Hold: prrow.None, SupersededBy: prrow.None, Mergeable: "clean",
	}
	if change != nil {
		change(&row)
	}
	return prrow.Decide(row)
}

func putRows(t *testing.T, s *store.Store, rows ...store.ReviewRow) {
	t.Helper()
	if err := s.PutReviewRows(rows); err != nil {
		t.Fatal(err)
	}
}

func alarms(f *fake) []Post {
	var found []Post
	for _, post := range f.ofKind("blocker") {
		if strings.Contains(post.Text, "the owner has been waiting") {
			found = append(found, post)
		}
	}
	return found
}

func waitFake(t *testing.T, s *store.Store) *fake {
	t.Helper()
	f := adopted(t, s, writeBrief(t), "")
	f.deps.Repo = waitRepo
	return f
}

func TestARowWaitingOnlyOnAGradeAlarmsRootOnceAtThirtyMinutesNamingTheDeskThatOwesIt(t *testing.T) {
	s := memory(t)
	f := waitFake(t, s)
	putRows(t, s, waitingRow(49730, lane2, nil))

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(29)))
	equal(t, len(alarms(f)), 0)

	tick(t, s, f, t0.Add(mins(30)))
	tick(t, s, f, t0.Add(mins(31)))

	got := alarms(f)
	equal(t, len(got), 1)
	equal(t, got[0].To, []string{Root})
	equal(t, got[0].PRs, []int{49730})
	contains(t, got[0].Text, "org/repo#49730 at e718a7434f: for 30 min the owner has been waiting on a grade alone")
	contains(t, got[0].Text, "it is owed by finder-desk and fidelity-desk")
}

func TestAGradeAlarmNamesOnlyTheDeskStillOwing(t *testing.T) {
	s := memory(t)
	f := waitFake(t, s)
	putRows(t, s, waitingRow(49730, lane2, nil))
	if _, err := s.PutFact(waitRepo, 49730, waitHead, "finder", "clear", t0); err != nil {
		t.Fatal(err)
	}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(30)))

	contains(t, alarms(f)[0].Text, "it is owed by fidelity-desk")
}

func TestAGradeAlarmSaysWhetherAnyPassIsLeft(t *testing.T) {
	s := memory(t)
	f := waitFake(t, s)
	putRows(t, s, waitingRow(50125, lane2, nil))
	if _, err := s.PutFact(waitRepo, 50125, waitHead, machine.GradeStoppedFact, "fidelity: its pass ended without a grade on all 3 attempts", t0); err != nil {
		t.Fatal(err)
	}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(30)))

	contains(t, alarms(f)[0].Text, "no pass is left to run: fidelity: its pass ended without a grade on all 3 attempts")
}

func TestANewHeadRestartsAWaitClockAndAlarmsAgainForItsOwnHead(t *testing.T) {
	s := memory(t)
	f := waitFake(t, s)
	putRows(t, s, waitingRow(49730, lane2, nil))
	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(30)))

	putRows(t, s, waitingRow(49730, lane2, func(row *store.ReviewRow) { row.Head = nextHead }))
	tick(t, s, f, t0.Add(mins(31)))
	tick(t, s, f, t0.Add(mins(60)))
	equal(t, len(alarms(f)), 1)

	tick(t, s, f, t0.Add(mins(61)))
	equal(t, len(alarms(f)), 2)
}

func TestARowWaitingOnSomethingElseRaisesNoGradeAlarm(t *testing.T) {
	for name, change := range map[string]func(*store.ReviewRow){
		"its parent has not landed": func(row *store.ReviewRow) { row.ParentState, row.Parent = prrow.ParentOpen, "#49700" },
		"no approval":               func(row *store.ReviewRow) { row.Approvers = prrow.None },
		"CI running":                func(row *store.ReviewRow) { row.CI = prrow.CIPending },
		"held":                      func(row *store.ReviewRow) { row.Hold = "root: wait" },
		"graded":                    func(row *store.ReviewRow) { row.Grade = prrow.GradePass },
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := waitFake(t, s)
			putRows(t, s, waitingRow(49730, lane2, change))

			tick(t, s, f, t0)
			tick(t, s, f, t0.Add(mins(120)))

			equal(t, len(alarms(f)), 0)
		})
	}
}

func TestAnOpenRowNoLiveLaneOwnsAlarmsRootOnceAtAnHour(t *testing.T) {
	s := memory(t)
	f := waitFake(t, s)
	putRows(t, s, waitingRow(49830, "chat-recovery-window-2", func(row *store.ReviewRow) { row.Grade, row.GradeFindings = prrow.GradeFail, "finder: major" }))

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(59)))
	equal(t, len(alarms(f)), 0)

	tick(t, s, f, t0.Add(mins(60)))
	tick(t, s, f, t0.Add(mins(120)))

	got := alarms(f)
	equal(t, len(got), 1)
	contains(t, got[0].Text, "on a lane to own it: chat-recovery-window-2 is gone and it waits on a grade needs a verdict: finder: major")
	contains(t, got[0].Text, Request{Verb: "owner", Lane: "<lane>", PRs: []int{49830}}.Command())
}

func TestALaneClockSkipsARowThatNeedsNoLane(t *testing.T) {
	for name, row := range map[string]store.ReviewRow{
		"its lane is live":      waitingRow(1, lane2, func(row *store.ReviewRow) { row.Grade = prrow.GradePass }),
		"no lane ever owned it": waitingRow(2, "", func(row *store.ReviewRow) { row.Grade = prrow.GradePass }),
		"only his approval left": waitingRow(3, "gone-2", func(row *store.ReviewRow) {
			row.Grade, row.ForUsers, row.PlanRow, row.Risk = prrow.GradePass, "u", "p", "r"
		}),
		"held": waitingRow(4, "gone-2", func(row *store.ReviewRow) { row.Grade, row.Hold = prrow.GradePass, "root: wait" }),
		"waiting on a stack base": waitingRow(5, "gone-2", func(row *store.ReviewRow) {
			row.Grade, row.ParentState, row.Parent = prrow.GradePass, prrow.ParentOpen, "#4"
		}),
		"landed": waitingRow(6, "gone-2", func(row *store.ReviewRow) { row.Grade, row.State = prrow.GradePass, prrow.StateLanded }),
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := waitFake(t, s)
			putRows(t, s, row)

			tick(t, s, f, t0)
			tick(t, s, f, t0.Add(mins(120)))

			equal(t, len(alarms(f)), 0)
		})
	}
}

func TestAnotherPersonsRowRaisesNoWaitAlarm(t *testing.T) {
	s := memory(t)
	f := waitFake(t, s)
	putRows(t, s, waitingRow(47613, "gone-2", nil))
	if _, err := s.PutFact(waitRepo, 47613, waitHead, prrow.FactForeign, "a-teammate", t0); err != nil {
		t.Fatal(err)
	}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(120)))

	equal(t, len(alarms(f)), 0)
}

func TestARedClassedInheritedAlarmsRootAtAnHourNamingThePRThatBrokeIt(t *testing.T) {
	s := memory(t)
	f := waitFake(t, s)
	putRows(t, s, waitingRow(49730, lane2, func(row *store.ReviewRow) { row.CI = prrow.CIRed }))
	f.feed = append(f.feed, rec(2, lane2, "red", t0, "class=inherited head=e718a74 owner=old-chat-convert-5 the serve tests #49730 runs broke with #50008").withRefs(prs(49730)))

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(59)))
	equal(t, len(alarms(f)), 0)

	tick(t, s, f, t0.Add(mins(60)))
	tick(t, s, f, t0.Add(mins(120)))

	got := alarms(f)
	equal(t, len(got), 1)
	contains(t, got[0].Text, "on a red its own change did not break: #50008 broke it, and old-chat-convert-5 owes the fix or the wait")
}

func TestAnInheritedRedNamingNoPRSaysSoAndARealRedRaisesNothing(t *testing.T) {
	for class, want := range map[string]string{
		"inherited": "no record names the PR that did; old-chat-convert-5 owes that name",
		"real":      "",
	} {
		t.Run(class, func(t *testing.T) {
			s := memory(t)
			f := waitFake(t, s)
			putRows(t, s, waitingRow(49730, lane2, func(row *store.ReviewRow) { row.CI = prrow.CIRed }))
			f.feed = append(f.feed, rec(2, lane2, "red", t0, "class="+class+" head=e718a74 owner=old-chat-convert-5 a serve test").withRefs(prs(49730)))

			tick(t, s, f, t0)
			tick(t, s, f, t0.Add(mins(60)))

			got := alarms(f)
			if want == "" {
				equal(t, len(got), 0)
				return
			}
			contains(t, got[0].Text, want)
		})
	}
}

func TestANewerClassReplacesAnInheritedRed(t *testing.T) {
	s := memory(t)
	f := waitFake(t, s)
	putRows(t, s, waitingRow(49730, lane2, func(row *store.ReviewRow) { row.CI = prrow.CIRed }))
	f.feed = append(f.feed, rec(2, lane2, "red", t0, "class=inherited head=e718a74 from #50008").withRefs(prs(49730)))
	tick(t, s, f, t0)

	f.feed = append(f.feed, rec(3, lane2, "red", t0.Add(mins(5)), "class=real head=e718a74 my own bug").withRefs(prs(49730)))
	tick(t, s, f, t0.Add(mins(60)))

	equal(t, len(alarms(f)), 0)
}

func TestAStandingDeskThatTakesNoOrderIsLostAtThirtyMinutesEvenWhileItReadsLive(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "grade #49730 first", refresher))
	idle := t0.Add(-mins(5))

	f.peers = orcaPeers(refresher, idle, t0.Add(mins(29)))
	before := tick(t, s, f, t0.Add(mins(29)))
	f.peers = orcaPeers(refresher, idle, t0.Add(mins(30)))
	at := tick(t, s, f, t0.Add(mins(30)))

	equal(t, len(before.Relaunched), 0)
	equal(t, len(at.Relaunched), 1)
	equal(t, at.Relaunched[0].Old, refresher)
	contains(t, at.Relaunched[0].Why, "is a standing desk that took none of its 1 orders, root's ask #2, the oldest for 30 min")
	lost := f.ofKind("report")
	if !strings.Contains(lost[len(lost)-1].Text, "LANE-LOST "+refresher) {
		t.Fatalf("reports = %+v", lost)
	}
}

func TestAStandingDeskMakingToolCallsOrPostingIsNotLost(t *testing.T) {
	for name, feed := range map[string]func(*fake){
		"a tool call after the order": func(f *fake) { f.peers = orcaPeers(refresher, t0.Add(mins(29)), t0.Add(mins(40))) },
		"a record after the order": func(f *fake) {
			f.feed = append(f.feed, rec(3, refresher, "state", t0.Add(mins(29)), "grading"))
			f.peers = orcaPeers(refresher, t0.Add(-mins(5)), t0.Add(mins(40)))
		},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f, _ := standingLane(t, s)
			f.feed = append(f.feed, rec(2, Root, "ask", t0, "grade #49730 first", refresher))
			feed(f)

			out := tick(t, s, f, t0.Add(mins(40)))

			equal(t, len(out.Relaunched), 0)
		})
	}
}

func TestAnOrdinaryLaneOrTheMetaLaneQuietWithAnOrderIsNeverRelaunchedForIt(t *testing.T) {
	for name, role := range map[string]string{"an ordinary lane": "", "the meta lane": RoleMeta} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			if err := s.SetLaneRole(lane2, role); err != nil {
				t.Fatal(err)
			}
			f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))
			f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0.Add(mins(45)), Dispatch: "ctx_1"}}

			out := tick(t, s, f, t0.Add(mins(45)))

			equal(t, len(out.Relaunched), 0)
		})
	}
}

func TestASpriteWorkersUnreadOrderIsRelayedOnceAndSendsNobodyToReadItsSprite(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s)
	f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))
	f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0.Add(mins(11)), Dispatch: "ctx_1"}}

	tick(t, s, f, t0.Add(mins(11)))
	tick(t, s, f, t0.Add(mins(12)))

	equal(t, len(relays(f)), 1)
	for _, post := range f.posts {
		if strings.Contains(post.Text, "read its Sprite") {
			t.Fatalf("a post still sends someone to the Sprite: %s", post.Text)
		}
	}
}
