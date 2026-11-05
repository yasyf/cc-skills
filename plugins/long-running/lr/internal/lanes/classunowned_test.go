package lanes

import (
	"context"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
)

func classCheck(t *testing.T, records ...client.Record) []client.Draft {
	t.Helper()
	bus := &fakeBus{tail: records}
	check := &ClassUnowned{Env: Env{Bus: bus, Config: config.Config{Drive: testDrive}}, Store: memory(t)}
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	check.read = t0.Add(-classEvery)
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	return bus.posted
}

func TestAnIncidentClassWithNoFixOrderReachesRootOnce(t *testing.T) {
	posted := classCheck(t, client.Record{Seq: 5, Lane: "alerts-desk-3", Kind: "incident", At: t0.Add(-mins(11)), Topic: "dd-325", Text: "monitor 325 fired"})

	if len(posted) != 1 || posted[0].To[0] != "root" || !strings.HasPrefix(posted[0].Text, "CLASS-UNOWNED topic:dd-325 alerts-desk-3: #5, 11 min with no fix-lane GO naming ") {
		t.Fatalf("posted = %+v", posted)
	}
}

func TestAClassThatNeedsNoAlarmDrawsNone(t *testing.T) {
	incident := client.Record{Seq: 5, Lane: "alerts-desk-3", Kind: "incident", At: t0.Add(-mins(11)), Topic: "dd-325", Text: "monitor 325 fired"}
	cases := map[string][]client.Record{
		"inside its ten minutes":               {{Seq: 5, Lane: "a", Kind: "incident", At: t0.Add(-mins(9)), Topic: "dd-325"}},
		"root's own":                           {{Seq: 5, Lane: "root", Kind: "incident", At: t0.Add(-mins(11)), Topic: "dd-325"}},
		"names no class":                       {{Seq: 5, Lane: "a", Kind: "incident", At: t0.Add(-mins(11)), Text: "monitor fired"}},
		"not a class":                          {{Seq: 5, Lane: "a", Kind: "report", At: t0.Add(-mins(11)), Topic: "dd-325", Text: "CLASSIC outage"}},
		"resolved":                             {incident, {Seq: 6, Lane: "root", Kind: "note", At: t0, Resolves: 5}},
		"a fix order names it":                 {incident, {Seq: 6, Lane: "root", Kind: "go", At: t0, Topic: "dd-325", Text: "launch the fix lane"}},
		"its own fix lane posted it":           {{Seq: 5, Lane: "dd-325-fix", Kind: "incident", At: t0.Add(-mins(11)), Topic: "dd-325", Text: "SCOPE dd-325-fix: active yes"}},
		"its fix lane spawned before it":       {{Seq: 4, Lane: Self, Kind: "state", At: t0.Add(-mins(12)), Topic: "spawn:dd-325-fix", Text: "SPAWNED dd-325-fix /state/lanes/dd-325-fix.brief.md"}, incident},
		"the supervisor launched its fix lane": {{Seq: 5, Lane: Self, Kind: "incident", At: t0.Add(-mins(11)), Topic: "dev-red", Text: "DEV-RED for 11 min"}, {Seq: 6, Lane: Self, Kind: "go", At: t0.Add(-mins(10)), Topic: "dev-red", Text: "orca-desk: incident dev-red-abc https://b :: red; fix it"}},
	}
	for name, records := range cases {
		t.Run(name, func(t *testing.T) {
			if posted := classCheck(t, records...); len(posted) != 0 {
				t.Fatalf("posted = %+v", posted)
			}
		})
	}
}

func TestAnOrderWithoutTheWordFixOrFromAnotherLaneOwnsNothing(t *testing.T) {
	incident := client.Record{Seq: 5, Lane: "a", Kind: "incident", At: t0.Add(-mins(11)), Topic: "dd-325"}
	for name, order := range map[string]client.Record{
		"no fix":                    {Seq: 6, Lane: "root", Kind: "go", At: t0, Topic: "dd-325", Text: "look at it"},
		"not root":                  {Seq: 6, Lane: "other", Kind: "go", At: t0, Topic: "dd-325", Text: "fix it"},
		"before":                    {Seq: 4, Lane: "root", Kind: "go", At: t0, Topic: "dd-325", Text: "fix it"},
		"another lane spawned":      {Seq: 4, Lane: Self, Kind: "state", At: t0, Topic: "spawn:dd-326-fix", Text: "SPAWNED dd-326-fix /state/lanes/dd-326-fix.brief.md"},
		"its evidence lane spawned": {Seq: 4, Lane: Self, Kind: "state", At: t0, Topic: "spawn:dd-325-evidence", Text: "SPAWNED dd-325-evidence /state/lanes/dd-325-evidence.brief.md"},
	} {
		t.Run(name, func(t *testing.T) {
			if posted := classCheck(t, incident, order); len(posted) != 1 {
				t.Fatalf("posted = %+v", posted)
			}
		})
	}
}

func TestAClassLineNamesItsTagsAndTagsElsewhereAreEvidence(t *testing.T) {
	record := client.Record{Text: "NEW CLASS SAND-5A is failing\nsee OTHER-9 for evidence"}
	keys := classKeys(record)
	if len(keys) != 1 || keys[0] != "tag:SAND5A" {
		t.Fatalf("keys = %v", keys)
	}
	if keys := classKeys(client.Record{Text: "[SAND-5A] down"}); len(keys) != 1 || keys[0] != "tag:SAND5A" {
		t.Fatalf("keys = %v", keys)
	}
}
