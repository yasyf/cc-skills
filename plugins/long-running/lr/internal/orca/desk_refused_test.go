package orca

import (
	"path/filepath"
	"regexp"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
)

func refusedEdge() []config.Refusal {
	return []config.Refusal{{Match: regexp.MustCompile(`(?i)edge-vendor`), Why: "never chased"}}
}

func TestAnAlertLineOnARefusedTopicRecordsNothingAndItsNeighbourStillRecords(t *testing.T) {
	w := newDeskWorld(t)
	w.desk.Config.RefusedBriefs = refusedEdge()
	w.lines()
	w.line("orca-desk: alert edge-1 https://dd/1 :: edge-vendor answers 403")
	w.line("orca-desk: alert the edge-vendor line in no grammar")
	w.lines()
	w.noEscalation("ALERT")
	w.line("orca-desk: alert api-500 https://dd/2 :: errors")
	w.lines()
	w.wantEscalation("ALERT api-500: errors https://dd/2")
	w.noEscalation("ALERT edge-1")
}

func TestARepeatAlertOnARefusedTopicNeverReachesItsLiveFixLane(t *testing.T) {
	w := newDeskWorld(t)
	w.desk.Config.RefusedBriefs = refusedEdge()
	w.lines()
	w.live("edge-1-fix", "ctx_fix", "term_fix")
	w.line("R4 (5:03 PM) orca-desk: alert edge-1 https://dd/1 :: edge-vendor answers 403")
	w.lines()
	if action, err := w.desk.store.Action(laneBook("edge-1-fix"), "R4:again"); err == nil {
		t.Fatalf("the refused alert was relayed to the live fix lane: %+v", action)
	}
}

func TestAnIncidentLineOnARefusedTopicFiresNothingAndItsNeighbourFires(t *testing.T) {
	w := newDeskWorld(t)
	w.desk.Config.RefusedBriefs = refusedEdge()
	w.lines()
	w.line("orca-desk: incident edge-1 https://dd/1 :: edge-vendor answers 403")
	w.lines()
	w.desk.background.Wait()
	if len(w.fired) != 0 {
		t.Fatalf("a refused incident fired: %+v", w.fired)
	}
	w.noEscalation("INCIDENT edge-1")
	w.line("orca-desk: incident api-500 https://dd/2 :: errors")
	w.lines()
	w.desk.background.Wait()
	if len(w.fired) != 1 || w.fired[0].Slug != "api-500" {
		t.Fatal(w.fired)
	}
}

func TestADriveWithNoListRecordsAndFiresAsBefore(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("orca-desk: alert edge-1 https://dd/1 :: edge-vendor answers 403")
	w.line("orca-desk: incident edge-2 https://dd/2 :: edge-vendor answers 403")
	w.lines()
	w.desk.background.Wait()
	w.wantEscalation("ALERT edge-1")
	if len(w.fired) != 1 || w.fired[0].Slug != "edge-2" {
		t.Fatal(w.fired)
	}
}

func TestABriefNamingADropClassLaunchesAndWarnsQuotingTheRuling(t *testing.T) {
	w := newDeskWorld(t)
	w.desk.Config.RefusedBriefs = []config.Refusal{{Match: regexp.MustCompile(`(?i)edge-vendor`), Why: "owner ruling ff7fca1: No. Never a lane, a read, a report, a line to him."}}
	brief := w.brief(testLane, testBrief+"ccx: place=local why=mac-build\nFind why Edge-Vendor answers 403 at open\n")
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	warned := w.wantEscalation("BRIEF-NAMES-RULING ")
	wantContains(t, warned, "No. Never a lane, a read, a report, a line to him.")
	wantContains(t, warned, "Find why Edge-Vendor answers 403 at open")
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 {
		t.Fatal(w.spawns)
	}
}

func TestAnUnfixedCaseNamingADropClassLaunchesNoLaneAndItsNeighbourLaunches(t *testing.T) {
	w := newDeskWorld(t)
	refusals, err := config.RefusalsOf(`[{"id":"ff7fca14cc8d","body":"No. Never a lane.","tags":["drop-class","match:edge 403","match:gsk edge"]}]`)
	if err != nil {
		t.Fatal(err)
	}
	w.desk.Config.RefusedBriefs = refusals
	lane := "api-fix"
	fixedLane(t, w, lane)
	message := runMessage("msg_r", typeStatus, "term_fix", 1)
	message.Subject = "ready: fixed"
	message.Body = "UNFIXED api-fix: GSK's edge 403s the GET\nUNFIXED api-fix: the poller still retries the old row\n"
	message.FromHandle = "term_fix"
	w.write(filepath.Join(w.desk.Config.Receipts, lane+".terminal"), "term_fix\n")
	w.write(filepath.Join(w.desk.Config.Receipts, lane+".json"), `{"result":{"dispatchId":"ctx_fix"}}`)
	w.checked(message)
	launches, err := w.desk.store.Actions(laneBook("api-fix-case1"), kindLaunch)
	if err != nil || len(launches) != 1 {
		t.Fatal(launches, err)
	}
	spec, err := launchOf(launches[0])
	if err != nil {
		t.Fatal(err)
	}
	wantContains(t, readBrief(spec.Brief), "the poller still retries the old row")
	if exists, _ := w.desk.store.Exists(laneBook("api-fix-case2")); exists {
		t.Fatal("a case lane launched for his dropped class")
	}
}

func TestABriefPlacedLocalWithNoNeedLaunchesWarnedThatItLandsOnASprite(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: role=desk place=local incident=none\n")
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	warned := w.wantEscalation("PLACE-UNREASONED ")
	wantContains(t, warned, "lane-a's brief says place=local but names no why=<need>, so it launches on a Sprite")
}

func TestABriefNamingWhyItNeedsTheMacLaunchesUnwarned(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: role=desk place=local why=cc-present incident=none\n")
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	w.noEscalation("PLACE-UNREASONED ")
}
