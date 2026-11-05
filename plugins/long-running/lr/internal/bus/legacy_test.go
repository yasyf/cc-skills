package bus_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
)

const repo = "org/repo"

func legacyRecord(t *testing.T, kind, lane, text string, prs []int, attrs map[string]string) client.Record {
	t.Helper()
	refs := make([]string, 0, len(prs))
	for _, n := range prs {
		refs = append(refs, fmt.Sprintf("%s#%d", repo, n))
	}
	raw, err := json.Marshal(map[string]any{
		"seq": 1, "at": "2026-11-02T21:00:00Z", "kind": kind, "lane": lane, "drive": "d", "text": text, "source": "post",
		"refs": map[string]any{"prs": refs}, "fields": map[string]any{"attrs": attrs},
	})
	if err != nil {
		t.Fatal(err)
	}
	var record client.Record
	if err := json.Unmarshal(raw, &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func TestEachGradeTapAndExemptionBecomesOneFact(t *testing.T) {
	for _, tt := range []struct {
		name   string
		record client.Record
		want   []bus.Fact
	}{
		{"finder ok", legacyRecord(t, "review", "finder-desk", "finder: ok "+head+" luna finder over delta: no majors", []int{1}, nil), []bus.Fact{{PR: 1, Head: head, Kind: "finder", Value: "clear"}}},
		{"finder major on a successor desk", legacyRecord(t, "review", "finder-desk-2", "finder: major "+head[:9]+" 2 majors in the restack", []int{1}, nil), []bus.Fact{{PR: 1, Head: head[:9], Kind: "finder", Value: "ask"}}},
		{"fidelity ok", legacyRecord(t, "review", "fidelity-desk-22", "fidelity: ok "+head[:7]+" stays in scope", []int{1}, nil), []bus.Fact{{PR: 1, Head: head[:7], Kind: "fidelity", Value: "pass"}}},
		{"the sweep desk's fidelity ok", legacyRecord(t, "review", "fidelity-sweep", "fidelity: ok "+head[:7]+" stays in scope", []int{1}, nil), []bus.Fact{{PR: 1, Head: head[:7], Kind: "fidelity", Value: "pass"}}},
		{"a shard's fidelity ok, shaped as 439107", legacyRecord(t, "review", "fidelity-shard-5", "fidelity: ok "+head+" class regression: carries my ok at a2fdafc (438794). The delta answers ai-review's low finding", []int{1}, nil), []bus.Fact{{PR: 1, Head: head, Kind: "fidelity", Value: "pass"}}},
		{"the first shard's fidelity missing", legacyRecord(t, "review", "fidelity-shard-1", "fidelity: missing "+head[:7]+" plan:12 the plan's cap is gone", []int{1}, nil), []bus.Fact{{PR: 1, Head: head[:7], Kind: "fidelity", Value: "fail"}}},
		{"the grader's stopped report", legacyRecord(t, "report", bus.RunnerLane, "GRADE-STOPPED #1", []int{1}, map[string]string{bus.GradeStoppedWhyAttr: "fidelity: its pass ended without a grade", bus.GradeStoppedHeadAttr: head}), []bus.Fact{{PR: 1, Head: head, Kind: "grade-stopped", Value: "fidelity: its pass ended without a grade"}}},
		{"fidelity missing", legacyRecord(t, "review", "fidelity-pipeline", "fidelity: missing "+head[:7]+" plan:12 the plan's cap is gone", []int{1}, nil), []bus.Fact{{PR: 1, Head: head[:7], Kind: "fidelity", Value: "fail"}}},
		{"a coordinator go the gate posted names its class", legacyRecord(t, "go", "root", "5:26 PM go #9 at e718a74 on green [exempt: regression]", []int{9}, map[string]string{bus.ExemptClassAttr: "regression"}), []bus.Fact{{PR: 9, Head: "e718a74", Kind: "exempt", Value: "regression"}}},
		{"a head that cannot move", legacyRecord(t, bus.StuckKind, "supervisor", "#9 cannot move", []int{9}, map[string]string{"head": "e718a74", "reason": "its grade waits on the pool"}), []bus.Fact{{PR: 9, Head: "e718a74", Kind: bus.StuckFact, Value: "its grade waits on the pool"}}},
		{"a finder override", legacyRecord(t, "decide", "root", "finder-override #9 at e718a74: the finding reads a deleted file", []int{9}, nil), []bus.Fact{{PR: 9, Head: "e718a74", Kind: "override-finder", Value: "accept"}}},
		{"an override after a clock and a root label", legacyRecord(t, "decide", "root", "5:26 PM root: fidelity-override 9 e718a74", []int{9}, nil), []bus.Fact{{PR: 9, Head: "e718a74", Kind: "override-fidelity", Value: "accept"}}},
		{"fidelity judged a head the same as the tapped one", legacyRecord(t, "review", "fidelity-pipeline", "behavior: same "+head+" since a28ab62c0ffe", []int{1}, nil), []bus.Fact{{PR: 1, Head: head, Kind: "behavior", Value: "same"}, {PR: 1, Head: head, Kind: "behavior-since", Value: "a28ab62c0ffe"}}},
		{"fidelity judged a head changed since the tapped one", legacyRecord(t, "review", "fidelity-pipeline", "behavior: changed "+head+" since a28ab62c0ffe: a cold list now finds its team by lineage", []int{1}, nil), []bus.Fact{{PR: 1, Head: head, Kind: "behavior", Value: "changed"}, {PR: 1, Head: head, Kind: "behavior-since", Value: "a28ab62c0ffe"}, {PR: 1, Head: head, Kind: "what-changed", Value: "a cold list now finds its team by lineage"}}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if got := bus.LegacyFacts(tt.record, repo, nil); !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("LegacyFacts = %+v, want %+v", got, tt.want)
			}
		})
	}
}

func TestARecordThatGradesTapsOrExemptsNothingWritesNothing(t *testing.T) {
	for _, tt := range []struct {
		name   string
		record client.Record
	}{
		{"a lane's own view of the finder", legacyRecord(t, "review", "lane-7", "finder: ok "+head+" my own view of the finder", []int{1}, nil)},
		{"a lane's own view of fidelity", legacyRecord(t, "review", "lane-7", "fidelity: ok "+head+" my own view of the plan", []int{1}, nil)},
		{"a lane's report naming a stop", legacyRecord(t, "report", "lane-7", "GRADE-STOPPED #1", []int{1}, map[string]string{bus.GradeStoppedWhyAttr: "my own view", bus.GradeStoppedHeadAttr: head})},
		{"a runner report that stops nothing", legacyRecord(t, "report", bus.RunnerLane, "two passes ran", []int{1}, nil)},
		{"root's report quoting an override", legacyRecord(t, "report", "root", "finder-override #1 at e718a74: 9", []int{1}, nil)},
		{"a line that is no grade", legacyRecord(t, "review", "fidelity-desk-22", "old-app: unchanged "+head[:7]+" baseline", []int{1}, nil)},
		{"a lane's own behavior line", legacyRecord(t, "review", "lane-7", "behavior: same "+head+" since a28ab62c0ffe", []int{1}, nil)},
		{"a finder line that says nothing", legacyRecord(t, "review", "finder-desk", "finder: running on "+head[:7], []int{1}, nil)},
		{"a finder line with no head", legacyRecord(t, "review", "finder-desk", "finder: ok", []int{1}, nil)},
		{"an exempt go by a lane", legacyRecord(t, "go", "lane-7", "go #9 at e718a74 [exempt: test-only]", []int{9}, map[string]string{bus.ExemptClassAttr: "test-only"})},
		{"a coordinator's exempt class typed by hand", legacyRecord(t, "go", "root", "go #9 at e718a74 on green [exempt: regression]", []int{9}, nil)},
		{"a go with no exempt class", legacyRecord(t, "go", "root", "go #9 at e718a74 on green", []int{9}, nil)},
		{"a withdrawn exempt go", legacyRecord(t, "go", "root", "withdrawn go #9 at e718a74 [exempt: test-only]", []int{9}, nil)},
		{"an exempt class on a decide with no go clause", legacyRecord(t, "decide", "root", "the class [exempt: regression] is for later", []int{9}, nil)},
		{"an override inside a sentence", legacyRecord(t, "decide", "root", "we talked about finder-override #9 at e718a74 yesterday", []int{9}, nil)},
		{"a board pick of another block", legacyRecord(t, "owner", "board-watch", "Owner picked an option", []int{1}, map[string]string{"block": "memory-choice", "event": "choice.selected"})},
		{"a merge row that is no number", legacyRecord(t, "owner", "board-watch", "approved", []int{1}, map[string]string{"block": "merge-prs", "verdict": "approved", "head": "e718a74", "row": "summary"})},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if got := bus.LegacyFacts(tt.record, repo, nil); got != nil {
				t.Fatalf("LegacyFacts = %+v, want none", got)
			}
		})
	}
}

func TestAFinderOverrideWithNoCiteIsUnreadAndAFidelityOneIsNot(t *testing.T) {
	citeless := legacyRecord(t, "decide", "root", "finder-override #9 at e718a74: a memory limit", []int{9}, nil)
	if unread := bus.UnreadOverrides(citeless); len(unread) != 1 {
		t.Fatalf("UnreadOverrides = %v, want the citeless finder override", unread)
	}
	cited := legacyRecord(t, "decide", "root", "finder-override #9 at e718a74 finder #79447@637eacf4: a memory limit", []int{9}, nil)
	if unread := bus.UnreadOverrides(cited); unread != nil {
		t.Fatalf("UnreadOverrides = %v, want none for a cited override", unread)
	}
	fidelity := legacyRecord(t, "decide", "root", "fidelity-override #9 at e718a74: plan 44 says so", []int{9}, nil)
	if unread := bus.UnreadOverrides(fidelity); unread != nil {
		t.Fatalf("UnreadOverrides = %v, want none for a fidelity override", unread)
	}
}

func TestAGoLaneTheConfigNamesMayNameAnExemptClass(t *testing.T) {
	record := legacyRecord(t, "go", "go-carry-sweep-3", "go #9 at e718a74 on green [exempt: owner-pick]", []int{9}, map[string]string{bus.ExemptClassAttr: "owner-pick"})

	got := bus.LegacyFacts(record, repo, []string{"go-carry-sweep"})

	if want := []bus.Fact{{PR: 9, Head: "e718a74", Kind: "exempt", Value: "owner-pick"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("LegacyFacts = %+v, want %+v", got, want)
	}
}

func TestTheLandingPassesSweepGoCountsWhatEverTheConfigNamesAndAStrangersDoesNot(t *testing.T) {
	gated := map[string]string{bus.ExemptClassAttr: "owner-pick"}
	swept := legacyRecord(t, "go", bus.SweepLane, "go #9 at e718a74 on green [exempt: owner-pick]", []int{9}, gated)
	stranger := legacyRecord(t, "go", "pr-unblock-3", "go #9 at e718a74 on green [exempt: owner-pick]", []int{9}, gated)

	if want := []bus.Fact{{PR: 9, Head: "e718a74", Kind: "exempt", Value: "owner-pick"}}; !reflect.DeepEqual(bus.LegacyFacts(swept, repo, nil), want) {
		t.Fatalf("LegacyFacts = %+v, want %+v", bus.LegacyFacts(swept, repo, nil), want)
	}
	if got := bus.LegacyFacts(stranger, repo, nil); len(got) != 0 {
		t.Fatalf("a lane no config names counted: %+v", got)
	}
}

func TestAnOverrideCitesItsGradeWithOrWithoutTheGraderNamed(t *testing.T) {
	for _, text := range []string{
		"finder-override #9 at e718a74: 79447@637eacf4 reads a deleted file",
		"finder-override #9 at e718a74 finder #79447@637eacf4: reads a deleted file",
	} {
		got := bus.Overrides(legacyRecord(t, "decide", "root", text, []int{9}, nil))
		want := []bus.Override{{Grader: "finder", PR: 9, Head: "e718a74", Cites: "79447@637eacf4"}}
		if !slices.Equal(got, want) {
			t.Errorf("Overrides(%q) = %+v, want %+v", text, got, want)
		}
	}
}

func TestAGoLaneNamesAnExemptClassAndNoOverride(t *testing.T) {
	record := legacyRecord(t, "decide", "landing-desk-3", "finder-override #9 at e718a74: the finding reads a deleted file", []int{9}, nil)

	if got := bus.LegacyFacts(record, repo, []string{"landing-desk"}); len(got) != 0 {
		t.Fatalf("LegacyFacts = %+v, want nothing", got)
	}
}

func TestAGradeNamingAnotherRepositoryWritesNothingAndABareNumberCounts(t *testing.T) {
	record := legacyRecord(t, "review", "finder-desk", "finder: ok "+head+" no majors", nil, nil)
	if err := json.Unmarshal([]byte(`{"refs":{"prs":["other/repo#4",5]}}`), &record); err != nil {
		t.Fatal(err)
	}

	got := bus.LegacyFacts(record, repo, nil)

	if want := []bus.Fact{{PR: 5, Head: head, Kind: "finder", Value: "clear"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("LegacyFacts = %+v, want %+v", got, want)
	}
}

func TestAnOverrideInASpilledBodyCounts(t *testing.T) {
	path := filepath.Join(t.TempDir(), "body.txt")
	text := "a long decide that opens the same way"
	if err := os.WriteFile(path, []byte(text+". finder-override #9 at e718a74: read the deleted file"), 0o600); err != nil {
		t.Fatal(err)
	}
	record := legacyRecord(t, "decide", "root", text+"…", []int{9}, nil)
	record.Refs.Path = path

	got := bus.LegacyFacts(record, repo, nil)

	if want := []bus.Fact{{PR: 9, Head: "e718a74", Kind: "override-finder", Value: "accept"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("LegacyFacts = %+v, want %+v", got, want)
	}
}

func TestAnOverrideInASpilledBodyOfManyLinesCounts(t *testing.T) {
	path := filepath.Join(t.TempDir(), "body.txt")
	if err := os.WriteFile(path, []byte("a long decide\nthat opens the same way. finder-override #9 at e718a74: read the deleted file"), 0o600); err != nil {
		t.Fatal(err)
	}
	record := legacyRecord(t, "decide", "root", "a long decide that opens the same way…", []int{9}, nil)
	record.Refs.Path = path

	got := bus.LegacyFacts(record, repo, nil)

	if want := []bus.Fact{{PR: 9, Head: "e718a74", Kind: "override-finder", Value: "accept"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("LegacyFacts = %+v, want %+v", got, want)
	}
}

func TestABodyRewrittenAfterPostingIsNotTheRecordsText(t *testing.T) {
	path := filepath.Join(t.TempDir(), "body.txt")
	if err := os.WriteFile(path, []byte("rewritten. finder-override #9 at e718a74"), 0o600); err != nil {
		t.Fatal(err)
	}
	record := legacyRecord(t, "decide", "root", "the original words…", []int{9}, nil)
	record.Refs.Path = path

	if got := bus.LegacyFacts(record, repo, nil); got != nil {
		t.Fatalf("LegacyFacts = %+v, want none", got)
	}
}

func TestAWholeTextIsTheBodyItOpensOrTheTextThenItsBody(t *testing.T) {
	path := filepath.Join(t.TempDir(), "body.md")
	if err := os.WriteFile(path, []byte("the whole of it"), 0o600); err != nil {
		t.Fatal(err)
	}
	for text, want := range map[string]string{
		"the whole":  "the whole of it",
		"the whole…": "the whole of it",
		"rewritten…": "rewritten…",
		"Owner: #7":  "Owner: #7\nthe whole of it",
	} {
		record := client.Record{Text: text}
		record.Refs.Path = path
		if got := bus.FullText(record); got != want {
			t.Errorf("FullText(%q) = %q, want %q", text, got, want)
		}
	}
	gone := client.Record{Text: "kept"}
	gone.Refs.Path = filepath.Join(t.TempDir(), "gone.md")
	if got := bus.FullText(gone); got != "kept" {
		t.Errorf("got %q", got)
	}
}

func TestTheFirstReadStartsThreeDaysBackAndLaterReadsFromTheCursor(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	b := bus.Bus{Drive: "d", Lane: "finder-desk"}
	first, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "finder-desk", Kind: "review", Text: "finder: ok " + head + " no majors", PRs: []string{repo + "#1"}})
	if err != nil {
		t.Fatal(err)
	}
	second, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "go", Text: "go #1 at e718a74", PRs: []string{repo + "#1"}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "note", Text: "a note is no legacy kind"}); err != nil {
		t.Fatal(err)
	}

	now := time.Now()
	all, err := b.ReadLegacy(context.Background(), 0, now)
	if err != nil || len(all) != 2 || all[0].Seq != first.Seq || all[1].Seq != second.Seq {
		t.Fatalf("first read = %+v, %v", all, err)
	}
	later, err := b.ReadLegacy(context.Background(), first.Seq, now)
	if err != nil || len(later) != 1 || later[0].Seq != second.Seq {
		t.Fatalf("read from the cursor = %+v, %v", later, err)
	}
	past, err := b.ReadLegacy(context.Background(), 0, now.Add(96*time.Hour))
	if err != nil || len(past) != 0 {
		t.Fatalf("read four days on = %+v, %v", past, err)
	}
}

func TestHoldLifts(t *testing.T) {
	hold := func(seq, resolves int64) client.Record {
		return client.Record{Seq: seq, Kind: "hold", Resolves: resolves}
	}
	for name, tc := range map[string]struct {
		records []client.Record
		targets []client.Record
		failed  error
		want    map[int64]bool
		reads   int
	}{
		"a hold resolving a hold lifts":     {records: []client.Record{hold(5, 4)}, targets: []client.Record{hold(4, 0)}, want: map[int64]bool{5: true}, reads: 1},
		"a hold resolving an ask holds":     {records: []client.Record{hold(5, 3)}, targets: []client.Record{{Seq: 3, Kind: "ask"}}, want: map[int64]bool{5: false}, reads: 1},
		"a hold resolving nothing reads no": {records: []client.Record{hold(5, 0)}, want: map[int64]bool{}},
		"another kind reads nothing":        {records: []client.Record{{Seq: 5, Kind: "note", Resolves: 4}}, want: map[int64]bool{}},
		"a failed read fails open":          {records: []client.Record{hold(5, 3), hold(6, 0)}, failed: errors.New("cci is down"), want: map[int64]bool{5: true}, reads: 1},
	} {
		t.Run(name, func(t *testing.T) {
			reads := 0
			got, err := bus.HoldLifts(tc.records, func([]int64) ([]client.Record, error) {
				reads++
				return tc.targets, tc.failed
			})
			if !errors.Is(err, tc.failed) || reads != tc.reads || !maps.Equal(got, tc.want) {
				t.Fatalf("lifts %v, err %v, reads %d", got, err, reads)
			}
		})
	}
}

func TestAGoLiftsAHoldOnlyWhenPostedAfterItWhateverOrderTheRelayNumberedThem(t *testing.T) {
	at := func(minute int) time.Time { return time.Date(2026, 11, 4, 1, minute, 0, 0, time.UTC) }
	record := func(seq int64, origin string, originSeq int64, minute int, text string) client.Record {
		return client.Record{Seq: seq, Origin: origin, OriginSeq: originSeq, At: at(minute), Text: text}
	}
	hold := record(100, "mac", 500, 10, "hold #1")
	for name, tc := range map[string]struct {
		g    client.Record
		want bool
	}{
		"an older go backfilled after the hold":        {record(900, "mac", 400, 5, "go #1 at e718a74"), false},
		"a later go numbered before a late hold":       {record(50, "mac", 600, 15, "go #1 at e718a74"), true},
		"a later go from another node":                 {record(90, "sprite", 7, 15, "go #1 at e718a74"), true},
		"an older go from another node, numbered last": {record(900, "sprite", 7, 5, "go #1 at e718a74"), false},
		"a later go naming another PR":                 {record(900, "mac", 600, 15, "go #2 at e718a74"), false},
	} {
		t.Run(name, func(t *testing.T) {
			if got := bus.GoLifts([]client.Record{tc.g}, hold, 1); got != tc.want {
				t.Fatalf("lifted = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestAGoPostedOnThisNodeLiftsAHoldPostedHereByItsSeq(t *testing.T) {
	at := time.Date(2026, 11, 4, 1, 10, 0, 0, time.UTC)
	hold := client.Record{Seq: 100, At: at, Text: "hold #1"}
	for seq, want := range map[int64]bool{99: false, 101: true} {
		if got := bus.GoLifts([]client.Record{{Seq: seq, At: at, Text: "go #1 at e718a74"}}, hold, 1); got != want {
			t.Fatalf("go at seq %d: lifted = %v, want %v", seq, got, want)
		}
	}
}

func TestHoldLiftsReadsOnceForAnyNumberOfHolds(t *testing.T) {
	for _, n := range []int64{1, 1000} {
		records := make([]client.Record, n)
		for i := range n {
			records[i] = client.Record{Seq: 2000 + i, Kind: "hold", Resolves: i + 1}
		}
		reads := 0
		if _, err := bus.HoldLifts(records, func([]int64) ([]client.Record, error) {
			reads++
			return nil, nil
		}); err != nil {
			t.Fatal(err)
		}
		if reads != 1 {
			t.Fatalf("%d holds took %d reads", n, reads)
		}
	}
}

func TestAnAppealIsADecideFromTheAppealsLaneThatNamesAnOverride(t *testing.T) {
	const override = "finder-override #9 at e718a74: 434757. Row rule/e20e623 grounds it"
	for name, tc := range map[string]struct {
		kind, lane, text string
		want             bool
	}{
		"the desk":             {"decide", "grader-appeals", override, true},
		"its first number":     {"decide", "grader-appeals-1", override, true},
		"a successor":          {"decide", "grader-appeals-7", override, true},
		"another lane":         {"decide", "grader-appeals-desk", override, false},
		"root":                 {"decide", "root", override, false},
		"a note":               {"note", "grader-appeals-1", override, false},
		"a decide of no grade": {"decide", "grader-appeals-1", "the row stands", false},
	} {
		t.Run(name, func(t *testing.T) {
			if got := bus.Appeal(legacyRecord(t, tc.kind, tc.lane, tc.text, []int{9}, nil)); got != tc.want {
				t.Fatalf("Appeal = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestAnAppealWritesNoFactOnAnyHost(t *testing.T) {
	record := legacyRecord(t, "decide", "grader-appeals-1", "finder-override #9 at e718a74: 434757. Row rule/e20e623 grounds it", []int{9}, nil)
	if got := bus.LegacyFacts(record, repo, nil); got != nil {
		t.Fatalf("LegacyFacts = %+v, want none", got)
	}
}

func TestAnOverrideLineReadsBackAsTheSameOverride(t *testing.T) {
	for _, override := range []bus.Override{
		{Grader: "finder", PR: 9, Head: "e718a74", Cites: "434757"},
		{Grader: "fidelity", PR: 9, Head: "e718a74", Cites: "113250@ccf1997a"},
		{Grader: "fidelity", PR: 9, Head: "e718a74"},
	} {
		line := override.Line("Counted from grader-appeals-1's cci #7, whose owner rows exist.")
		got := bus.Overrides(legacyRecord(t, "decide", bus.SweepLane, line, []int{9}, nil))
		if !slices.Equal(got, []bus.Override{override}) {
			t.Errorf("%q reads %+v, want %+v", line, got, override)
		}
	}
}

func TestTheLandingPassesCarriedOverrideCounts(t *testing.T) {
	record := legacyRecord(t, "decide", bus.SweepLane, "finder-override #9 at e718a74: 434757. Carried from aaaaaaa, whose own diff is the same.", []int{9}, nil)
	want := []bus.Fact{{PR: 9, Head: "e718a74", Kind: "override-finder", Value: "434757"}}
	if got := bus.LegacyFacts(record, repo, nil); !reflect.DeepEqual(got, want) {
		t.Fatalf("LegacyFacts = %+v, want %+v", got, want)
	}
}

func TestEveryListedFidelityLaneAndItsSuccessorWritesAGradeFact(t *testing.T) {
	want := []bus.Fact{{PR: 1, Head: head, Kind: "fidelity", Value: "pass"}}
	for _, lane := range append(bus.FidelityLanes(), "fidelity-desk-29", "fidelity-shard-5") {
		record := legacyRecord(t, "review", lane, "fidelity: ok "+head+" reads", []int{1}, nil)
		if got := bus.LegacyFacts(record, repo, nil); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: LegacyFacts = %+v, want %+v", lane, got, want)
		}
	}
}
