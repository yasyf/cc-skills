package orca

import (
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func (w *deskWorld) launchLineFor(lane string) {
	w.t.Helper()
	w.register(store.Lane{Name: lane, Role: "lane", Launcher: coordinatorLane})
	brief := w.brief(lane, "Register slice: lane="+lane+" step=all\nScale: no N\nccx: place=local why=mac-build\n")
	w.lines()
	w.line(launchText(lane, brief))
	w.now = w.now.Add(time.Minute)
}

func TestALaunchLineIsNotLaunchedWhenItsLaneStoodDownAfterIt(t *testing.T) {
	w := newDeskWorld(t)
	w.launchLineFor(testLane)
	w.add(object{"kind": kindStandDown, "text": "Stand down: not an alert", "to": []string{testLane}})
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatalf("a stood-down lane was launched: %v", w.spawns)
	}
	wantContains(t, w.wantEscalation("WITHDRAWN "), "stood down on cci #")
}

func TestALaunchLineIsNotLaunchedWhenARootWithdrawFollowsItInTheSameRead(t *testing.T) {
	w := newDeskWorld(t)
	w.launchLineFor(testLane)
	w.add(object{"kind": verbWithdraw, "text": "no", "re": w.records[len(w.records)-1].Seq})
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatalf("a withdrawn launch line was launched: %v", w.spawns)
	}
}

func (w *deskWorld) metaGo(lane, brief string) client.Record {
	return w.add(object{"lane": "meta-desk", "text": launchText(lane, brief), "to": []string{deskLane}})
}

func (w *deskWorld) metaWithdraws(goLine client.Record) client.Record {
	return w.add(object{"lane": "meta-desk", "kind": kindNote, "text": "Withdrawn: do not launch from this go; the work runs once elsewhere.", "to": []string{deskLane}, "resolves": goLine.Seq})
}

func TestAGoANoteResolvesInTheReadThatBringsItNeverLaunches(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local why=mac-build\n")
	w.lines()
	goLine := w.metaGo(testLane, brief)
	note := w.metaWithdraws(goLine)
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatalf("a go #566950 resolved launched as #567925 did: %v", w.spawns)
	}
	key := "R" + strings.TrimPrefix(goLine.Name(), "#")
	wantContains(t, w.wantEscalation("WITHDRAWN "+key+" "+testLane+": cci "+note.Name()), "nothing was launched")
	if posted := w.postOpening("WITHDRAWN " + key); posted.To[0] != "meta-desk" {
		t.Fatal(posted)
	}
	w.noEscalation("DIRECTIVE-FAILED")
}

func TestAGoResolvedWhileItStartsStopsBeforeWorkerStart(t *testing.T) {
	for _, tt := range []struct {
		name     string
		resolver func(w *deskWorld, goLine client.Record) client.Record
		launcher func(by string) string
		want     string
	}{
		{"a note to orca-desk, as #566950 resolved #566716", (*deskWorld).metaWithdraws, func(by string) string {
			return testLane + " failed withdrawn by " + by + " before worker-start; rolled back terminal=term_a"
		}, "WITHDRAWN "},
		{"root's note addressed to no one", func(w *deskWorld, goLine client.Record) client.Record {
			return w.add(object{"kind": kindNote, "text": "not this one", "resolves": goLine.Seq})
		}, func(by string) string {
			return testLane + " failed withdrawn by " + by + " before worker-start; rolled back terminal=term_a"
		}, "WITHDRAWN "},
		{"a launcher that failed for its own reason", (*deskWorld).metaWithdraws, func(string) string {
			return testLane + " failed boot terminal=term_a: orca terminal wait --for tui-idle reads timeout after 180s; rolled back terminal=term_a"
		}, "LAUNCH-FAILED "},
	} {
		t.Run(tt.name, func(t *testing.T) {
			w := newDeskWorld(t)
			brief := w.brief(testLane, testBrief+"ccx: place=local why=mac-build\n")
			w.process = &fakeProcess{}
			w.lines()
			goLine := w.metaGo(testLane, brief)
			key := "R" + strings.TrimPrefix(goLine.Name(), "#")
			w.lines()
			w.step(w.desk.deliver)
			if len(w.spawns) != 1 {
				t.Fatal(w.spawns)
			}
			mark := w.desk.withdrawnMark(laneBook(testLane), key)
			if !slices.Contains(w.spawns[0].env, withdrawnEnv+"="+mark) {
				t.Fatal(w.spawns[0].env)
			}
			resolver := tt.resolver(w, goLine)
			w.lines()
			w.wantEscalation("WITHDRAW-PENDING " + key)
			w.noEscalation("DIRECTIVE-FAILED")
			by := withdrawnBy(func(string) string { return mark })
			if by != "cci "+resolver.Name() {
				t.Fatalf("the launcher reads %q from its withdrawal mark", by)
			}
			w.write(w.spawns[0].log, tt.launcher(by)+"\n")
			w.process.exited = true
			w.step(w.desk.reap)
			if action := w.action(testLane, key); action.Status != incident.ActionFailed {
				t.Fatal(action)
			}
			if tt.want == "WITHDRAWN " {
				wantContains(t, w.wantEscalation("WITHDRAWN "+key+" "+testLane+": by "+by), "it stopped before worker-start, so no worker ran a command; its launcher said: "+testLane+" failed withdrawn by")
				w.noEscalation("LAUNCH-FAILED ")
			} else {
				wantContains(t, w.wantEscalation("LAUNCH-FAILED "), by+" withdrew it while it started, so it is not relaunched")
				w.noEscalation("WITHDRAWN ")
			}
			w.noEscalation("LAUNCHED ")
			if registered := w.registered(); len(registered) != 0 || len(w.spawns) != 1 || len(w.orcaCalls("orchestration worker-stop")) != 0 || exists(mark) {
				t.Fatal(registered, w.spawns, w.calls, exists(mark))
			}
		})
	}
}

func TestARootAnswerThatAlsoResolvesAGoStillAnswersItsDecide(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	brief := w.brief("lane-b", "Register slice: lane=lane-b step=all\nScale: no N\nccx: place=local why=mac-build\n")
	w.lines()
	goLine := w.metaGo("lane-b", brief)
	decide := w.add(object{"lane": deskRunner, "kind": "decide", "text": "DECIDE msg_q lane-a: which way"})
	w.lines()
	reply := w.add(object{"kind": "answer", "text": "left", "to": []string{deskLane}, "resolves": decide.Seq, "also_resolves": []int64{goLine.Seq}})
	w.lines()
	if action := w.action("lane-b", "R"+strings.TrimPrefix(goLine.Name(), "#")); action.Status != incident.ActionFailed {
		t.Fatal(action)
	}
	held, err := relayOf(w.action(testLane, fmt.Sprintf("cci#%d", reply.Seq)))
	if err != nil || held.ReplyTo != "msg_q" || held.Text != "left" {
		t.Fatal(held, err)
	}
	w.noEscalation("DIRECTIVE-FAILED")
}

func TestAGoResolvedAfterItLaunchedStopsNothing(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local why=mac-build\n")
	w.process = &fakeProcess{}
	w.lines()
	goLine := w.metaGo(testLane, brief)
	key := "R" + strings.TrimPrefix(goLine.Name(), "#")
	w.launchOut[testLane] = w.launched(testLane, testDispatch, testTerminal)
	w.lines()
	w.step(w.desk.deliver)
	w.process.exited = true
	w.dispatches[testDispatch] = &fakeDispatch{status: "running", terminal: testTerminal, observation: "live"}
	w.step(w.desk.reap)
	w.wantEscalation("LAUNCHED " + key)
	w.metaWithdraws(goLine)
	w.lines()
	wantContains(t, w.wantEscalation("WITHDRAW-FAILED "+key+" "+testLane), "which stops nothing; withdraw the lane with `orca-desk: withdraw "+testLane+"`")
	if len(w.orcaCalls("orchestration worker-stop")) != 0 {
		t.Fatal(w.calls)
	}
}

func TestAPassReadsTheInboxAsOftenForManyResolvedGosAsForOne(t *testing.T) {
	reads := func(count int) int {
		w := newDeskWorld(t)
		brief := w.brief(testLane, testBrief+"ccx: place=local why=mac-build\n")
		w.lines()
		for range count {
			w.metaWithdraws(w.metaGo(testLane, brief))
		}
		before := w.tails + w.amongs
		w.lines()
		reads := w.tails + w.amongs - before
		w.step(w.desk.deliver)
		withdrawn := slices.DeleteFunc(w.escalations(), func(line string) bool { return !strings.HasPrefix(line, "WITHDRAWN ") })
		if len(w.spawns) != 0 || len(withdrawn) != count {
			t.Fatal(w.spawns, w.escalations())
		}
		return reads
	}
	if one, many := reads(1), reads(scaletest.N()); one != many {
		t.Fatalf("a pass read the inbox %d times for 1 resolved go and %d for %d", one, many, scaletest.N())
	}
}

func TestAnIncidentLineIsNotFiredWhenItsFixLaneStoodDownOrRootWithdrewIt(t *testing.T) {
	for _, tt := range []struct {
		name, want string
		after      func(w *deskWorld, line int64)
	}{
		{"stood down", "stood down on cci #", func(w *deskWorld, _ int64) {
			w.add(object{"kind": kindStandDown, "text": "Stand down: not an alert", "to": []string{"api-500-fix"}})
		}},
		{"withdrawn", "closed it after its incident line", func(w *deskWorld, line int64) {
			w.add(object{"kind": verbWithdraw, "text": "no", "re": line})
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			w := newDeskWorld(t)
			w.lines()
			line := w.line("orca-desk: incident api-500-fix https://dd/1 :: errors")
			w.now = w.now.Add(time.Minute)
			tt.after(w, line.Seq)
			w.lines()
			w.desk.background.Wait()
			if len(w.fired) != 0 {
				t.Fatalf("an ended incident line fired: %v", w.fired)
			}
			wantContains(t, w.wantEscalation("WITHDRAWN "), tt.want)
		})
	}
}
