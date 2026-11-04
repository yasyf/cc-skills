package board

import (
	"context"
	"encoding/json"
	"reflect"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	liveHead = "632c4a5aa1b2c3d4e5f60718293a4b5c6d7e8f90"
	tapped   = "2026-11-03T02:00:00Z"
)

func outcomesOf(t *testing.T, raw string) Outcomes {
	t.Helper()
	var outcomes Outcomes
	if err := json.Unmarshal([]byte(raw), &outcomes); err != nil {
		t.Fatal(err)
	}
	return outcomes
}

func liveOf(t *testing.T, raw string, snapshots ...Snapshot) Live {
	t.Helper()
	live, err := LiveOf(outcomesOf(t, raw), snapshots)
	if err != nil {
		t.Fatal(err)
	}
	return live
}

const prGroupsDoc = `{"doc":{"blocks":[{"id":"sec","type":"section","children":[{"id":"merge-prs","type":"pr-groups","groups":[{"id":"g","title":"Fixes","prs":[{"number":47321,"head":"632c4a5","title":"PR"}]}]}]}]},`

func TestABoardRowNamesItsPRHeadAndTheOwnerVerdictAtThatHead(t *testing.T) {
	live := liveOf(t, prGroupsDoc+`"interactions":{"triage":{"merge-prs":{"47321":{"verdict":"approved","head":"632c4a5","at":"`+tapped+`"}}}}}`)
	if got, want := live.Lines(), []string{"board #47321 at 632c4a5: approved"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("lines = %v", got)
	}
	if waits := live.Waits(); len(waits) != 0 {
		t.Fatalf("an answered row still waits: %+v", waits)
	}
}

func TestAVerdictAtAnOlderHeadLeavesTheRowWaitingOnTheOwner(t *testing.T) {
	live := liveOf(t, prGroupsDoc+`"interactions":{"triage":{"merge-prs":{"47321":{"verdict":"approved","head":"aaaaaaa","at":"`+tapped+`"}}}}}`)
	waits := live.Waits()
	if len(waits) != 1 || waits[0].Line() != "tap merge-prs #47321 at 632c4a5, up since just now" {
		t.Fatalf("waits = %+v", waits)
	}
}

func TestATriageRowTakesItsHeadFromItsHintAndARowWithNoHeadAwaitsNothing(t *testing.T) {
	live := liveOf(t, `{"doc":{"blocks":[{"id":"t","type":"triage","items":[{"id":"merge-row-48401","hint":"at commit `+liveHead+` today"},{"id":"merge-row-48402","hint":"no sha"},{"id":"other"}]}]},"interactions":{}}`)
	if want := []MergeRow{{"t", "48401", liveHead}, {"t", "48402", ""}}; !reflect.DeepEqual(live.Rows, want) {
		t.Fatalf("rows = %+v", live.Rows)
	}
	if waits := live.Waits(); len(waits) != 1 || waits[0].PR != "48401" {
		t.Fatalf("waits = %+v", waits)
	}
}

func TestAPickWaitsUntilTheOwnerPicksOrWritesAnAnswer(t *testing.T) {
	doc := `{"doc":{"blocks":[{"id":"card-a","type":"card","title":"Which one?","children":[{"id":"open","type":"choice"},{"id":"picked","type":"choice"},{"id":"wrote","type":"choice"}]}]},`
	live := liveOf(t, doc+`"interactions":{"choices":{"picked":{"optionIds":["x"]},"wrote":{"other":"neither"}}}}`)
	if len(live.Picks) != 1 || live.Picks[0].Line() != "pick card-a open: Which one?, up since just now" {
		t.Fatalf("picks = %+v", live.Picks)
	}
}

func TestAWaitIsDatedByTheFirstOfTheUnbrokenRunOfSnapshotsThatShowIt(t *testing.T) {
	at := func(hour int) time.Time { return time.Date(2026, 11, 3, hour, 0, 0, 0, time.UTC) }
	row, card := `{"rows":[[47321,"632c4a5"]]}`, `{"cards":[["card-a",[]]]}`
	shown, err := firstShown([]Snapshot{{1, at(1), row}, {2, at(2), card}, {3, at(3), row}, {4, at(4), row}})
	if err != nil {
		t.Fatal(err)
	}
	if !shown["row:47321:632c4a5"].Equal(at(3)) {
		t.Fatalf("shown = %v", shown)
	}
	if _, kept := shown["card:card-a"]; kept {
		t.Fatalf("a card off the newest snapshot stayed: %v", shown)
	}
}

func TestTheNewestVerdictAcrossBlocksWinsForAPR(t *testing.T) {
	newest := newestByPR(map[string]map[string]Verdict{
		"a": {"47321": {Verdict: "rejected", At: "2026-11-03T01:00:00Z"}},
		"b": {"merge-row-47321": {Verdict: "approved", At: "2026-11-03T02:00:00Z"}, "free-text": {Verdict: "approved"}},
	})
	if len(newest) != 1 || newest["47321"].Verdict != "approved" {
		t.Fatalf("newest = %+v", newest)
	}
}

type scripted struct{ result shell.Result }

func (s scripted) Run(context.Context, shell.Command) (shell.Result, error) {
	return s.result, nil
}

func TestABoardThatDoesNotReadIsNamed(t *testing.T) {
	_, err := ReadOutcomes(context.Background(), scripted{shell.Result{Code: 1, Stderr: "no board"}}, "s")
	if err == nil || err.Error() != "cc-present outcomes exited 1: no board" {
		t.Fatalf("err = %v", err)
	}
	outcomes, err := ReadOutcomes(context.Background(), scripted{shell.Result{Stdout: "note\n" + `{"doc":{"a":1},"interactions":{}}`}}, "s")
	if err != nil || outcomes.Doc["a"] != 1.0 {
		t.Fatalf("outcomes = %+v, %v", outcomes, err)
	}
}

func TestTheMainBoardIsTheArtifactBoundToTheRootSession(t *testing.T) {
	listing := "cc-present daemon 0.54.14 · port 61118\n\n" +
		"SUBJECT                           SLUG                                     SESSION      STATUS  EVENTS  URL\n" +
		"f02f306df1d09223c6ea40de1a5a8097  dashboard-mockups-for-your-tap--7b8a71f4  lane-session  open    12      http://127.0.0.1:61118/p/dashboard-mockups-for-your-tap--7b8a71f4\n" +
		"0c1d6f0e9b2a4c3d8e7f6a5b4c3d2e1f  drive-board--1a2b3c4d                     root-session  open    90      http://127.0.0.1:61118/p/drive-board--1a2b3c4d\n"
	names, err := MainBoard(context.Background(), scripted{shell.Result{Stdout: listing}}, "root-session")
	if err != nil || !slices.Equal(names, []string{"0c1d6f0e9b2a4c3d8e7f6a5b4c3d2e1f", "drive-board--1a2b3c4d"}) {
		t.Fatalf("names = %q, %v", names, err)
	}
	if _, err := MainBoard(context.Background(), scripted{shell.Result{Code: 1, Stderr: "no daemon"}}, "root-session"); err == nil {
		t.Fatal("an unread listing named a main board")
	}
}
