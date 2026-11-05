package orca

import (
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
	brief := w.brief(testLane, testBrief+"ccx: place=local why=test\nFind why Edge-Vendor answers 403 at open\n")
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
	brief := w.brief(testLane, testBrief+"ccx: role=desk place=local why=serves-previews incident=none\n")
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	w.noEscalation("PLACE-UNREASONED ")
}
