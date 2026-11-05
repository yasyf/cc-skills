package grading

import (
	"context"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

func parkedOnACard(t *testing.T) (*poolFixture, map[int]machine.Facts) {
	t.Helper()
	f := newPool(t, map[string]any{"landing": map[string]any{"checkout": "/srv/main"}, "fidelity": map[string]any{}})
	post(t, client.Draft{Lane: grader.FidelityLane, Kind: kindReview, Text: "fidelity: missing " + head[:7] + " class owner-pick: the chapter title waits on his answer to card rmw37abc", PRs: []string{pull}})
	snapshot := map[int]machine.Facts{number: facts(head, func(f *machine.Facts) { f.Fidelity = machine.FidelityFail })}
	if started, err := f.pool.Reopen(context.Background(), snapshot); err != nil || len(started) != 0 {
		t.Fatalf("before any answer: started = %v, err = %v", started, err)
	}
	return f, snapshot
}

func wakes(t *testing.T) []client.Record {
	t.Helper()
	found, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{kindReport}})
	if err != nil {
		t.Fatal(err)
	}
	return slices.DeleteFunc(found, func(r client.Record) bool { return !strings.HasPrefix(r.Text, "ANSWER-REGRADES") })
}

func TestHisAnswerToTheCardAnOwnerPickGradeWaitsOnRegradesItOnceAndWakesItsDesk(t *testing.T) {
	f, snapshot := parkedOnACard(t)
	post(t, client.Draft{Lane: "owner-relay", Kind: kindOwner, Text: `Owner: tapped "Keep chapter(title)" on the question "Chapter title" (card rmw37abc), head e718a74 #45489`})

	started, err := f.pool.Reopen(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}
	again, err := f.pool.Reopen(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}

	if len(started) != 1 || len(again) != 0 {
		t.Errorf("started = %v, then %v", started, again)
	}
	if spec := readJob[grader.FidelityJob](t, filepath.Join(f.pool.root, grader.FidelityLane, jobDir+regradeMark)); spec.Regrade != 1 {
		t.Errorf("regrade = %d", spec.Regrade)
	}
	posted := wakes(t)
	if len(posted) != 1 || !slices.Equal(posted[0].To, []string{oddDesk}) || !strings.Contains(posted[0].Text, "card rmw37abc") {
		t.Errorf("wakes = %+v", posted)
	}
}

func TestHisAnswerToAnotherCardLeavesTheGradeParked(t *testing.T) {
	f, snapshot := parkedOnACard(t)
	post(t, client.Draft{Lane: "owner-relay", Kind: kindOwner, Text: `Owner: tapped "Approve, land it" on the question "Approve #45490" (card rmw99xyz), head aa11bb2 #45490`})

	started, err := f.pool.Reopen(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}

	if len(started) != 0 || len(wakes(t)) != 0 {
		t.Errorf("started = %v, wakes = %+v", started, wakes(t))
	}
}
