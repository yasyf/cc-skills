package bus_test

import (
	"reflect"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

func TestWhatARecordAsksOfTheFinderDesk(t *testing.T) {
	to := []string{"finder-desk"}
	for name, tt := range map[string]struct {
		record client.Record
		want   []string
	}{
		"an ask for a whole pass":                     {client.Record{Kind: "ask", Lane: "lane-7", To: to, Text: "finder: whole on org/repo#45489 and org/repo#45490"}, []string{"45489", "45490"}},
		"another repo's PR":                           {client.Record{Kind: "ask", Lane: "lane-7", To: to, Text: "finder: whole on other/repo#45489"}, []string{}},
		"a bare number":                               {client.Record{Kind: "ask", Lane: "lane-7", To: to, Text: "Finder: Whole #45489, not #12"}, []string{"45489"}},
		"an ask for anything else":                    {client.Record{Kind: "ask", Lane: "lane-7", To: to, Text: "how is #45489 doing"}, nil},
		"an ask to another lane":                      {client.Record{Kind: "ask", Lane: "lane-7", To: []string{"root"}, Text: "finder: whole #45489"}, nil},
		"a root regrade order":                        {client.Record{Kind: "decide", Lane: "root", To: to, Text: "finder-desk: regrade #45489; leave #45490"}, []string{"45489"}},
		"a lane's regrade order":                      {client.Record{Kind: "decide", Lane: "lane-7", To: to, Text: "finder-desk: regrade #45489"}, nil},
		"a coordinator decide with no ask":            {client.Record{Kind: "decide", Lane: "root", To: to, Text: "#45489 is settled"}, []string{}},
		"a root order in prose for whole-diff passes": {client.Record{Kind: "decide", Lane: "root", To: to, Text: "Run now, ahead of the queue: whole-diff finder passes on #45489 at 1490b9c (dev..head) and #45490 at 5e55ce2, plus #45491 for shep-exec-scope (994@0cd7efa0)."}, []string{"45489", "45490", "45491"}},
		"a numbered root's whole-pass defect":         {client.Record{Kind: "defect", Lane: "root-2", To: to, Text: "the whole pass on #45489 has not run"}, []string{"45489"}},
	} {
		got := bus.WholeAsks(tt.record, repo)
		if len(got) != len(tt.want) || (len(got) > 0 && !reflect.DeepEqual(got, tt.want)) {
			t.Errorf("%s: WholeAsks = %v, want %v", name, got, tt.want)
		}
	}
}

func TestAWholeAskOwesThePRAWholePassThatOnlyAWholeReadPays(t *testing.T) {
	ask := legacyRecord(t, "ask", "lane-7", "finder: whole #45489", []int{45489}, nil)
	ask.To = []string{"finder-desk"}
	for name, tt := range map[string]struct {
		record client.Record
		want   string
	}{
		"an ask":       {ask, machine.WholeOwed},
		"a whole read": {legacyRecord(t, "review", "finder-desk", "finder: ok 1490b9c 0 open over whole 1a2b3c4^..1490b9c", []int{45489}, nil), "read"},
		"a delta read": {legacyRecord(t, "review", "finder-desk", "finder: ok 1490b9c 0 open over delta 5e21452..1490b9c", []int{45489}, nil), ""},
		"a carry":      {legacyRecord(t, "review", "finder-desk", "finder: ok 1490b9c carried from #518277", []int{45489}, nil), ""},
	} {
		got := ""
		for _, fact := range bus.LegacyFacts(tt.record, repo, nil) {
			if fact.Kind == machine.WholeFact {
				if fact.PR != 45489 || fact.Head != "" {
					t.Errorf("%s: fact = %+v, want one per PR at no head", name, fact)
				}
				got = fact.Value
			}
		}
		if got != tt.want {
			t.Errorf("%s: %s = %q, want %q", name, machine.WholeFact, got, tt.want)
		}
	}
}
