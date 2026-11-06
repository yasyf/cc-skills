package grading

import (
	"context"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	waitingPR   = 51688
	waitingHead = "6f2a96c9e78b52ad1f8e1c0e5d3a4b2c1d0e9f8a"
)

func waiting() machine.Facts {
	return machine.Facts{Repo: repo, Number: waitingPR, Head: waitingHead, Lane: "dev-red-5a02b5902d-fix", CI: machine.CIPending}
}

func TestAFirstAfterAFailedFidelityGradeRegradesThatHeadOnceAheadOfOlderQueuedWork(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"checkout": "/srv/main"}, "fidelity": map[string]any{"concurrent": 1}})
	const switched, at = 51676, "a28d3f5f7b6763410bb54df932ad0d24fc428023"
	switchPR := repo + "#51676"
	openedAt(t, f.store, waitingPR, waitingHead)
	post(t, client.Draft{Lane: grader.FidelityLane, Kind: kindReview, Text: "fidelity: missing " + at + " plan:115 plan names no EndpointSlice watch contract for go/ci/internal/restate/registration.go:38-40", PRs: []string{switchPR}})
	post(t, client.Draft{Lane: "rsw-cp-follow-wait", Kind: kindAsk, To: []string{grader.FidelityLane, rootLane}, Text: "Regrade #51676 at a28d3f5 against cc-notes answer 115e672: his Approve at 5:02 PM on card rmw49rsvc is his word on the watch.", PRs: []string{switchPR}})
	post(t, firstDraft(testDrive, rootLane, switched))
	snapshot := map[int]machine.Facts{
		switched:  {Repo: repo, Number: switched, Head: at, Lane: "rsw-cp-follow-wait", CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true, Tap: machine.TapTapped, Finder: machine.FinderClear, Fidelity: machine.FidelityFail},
		waitingPR: waiting(),
	}
	now := time.Now()

	dispatched, err := Act(context.Background(), f.store, f.pool, items(t, f.store), snapshot, now, 1)
	if err != nil {
		t.Fatal(err)
	}

	if want := []Dispatched{{GraderFidelity, switched, at}}; !reflect.DeepEqual(dispatched, want) {
		t.Fatalf("dispatched = %v, want only the first PR's regrade ahead of #%d", dispatched, waitingPR)
	}
	regrade := filepath.Join(f.pool.root, grader.FidelityLane, "51676-"+at[:headChars]+regradeMark)
	if spec := readJob[grader.FidelityJob](t, regrade); spec.Regrade != 1 || len(spec.Grades) != 0 {
		t.Fatalf("regrade = %d, grades = %v", spec.Regrade, spec.Grades)
	}

	post(t, client.Draft{Lane: grader.FidelityLane, Kind: kindReview, Text: "fidelity: missing " + at + " plan:115 still names no watch contract", PRs: []string{switchPR}})
	f.shell.exitAll()
	writeResult(t, regrade, `{"verdict":"missing"}`, now)
	if err := Settle(context.Background(), f.store, f.pool, now.Add(time.Minute), 1); err != nil {
		t.Fatal(err)
	}

	again, err := Act(context.Background(), f.store, f.pool, items(t, f.store), snapshot, now.Add(time.Minute), 1)
	if err != nil {
		t.Fatal(err)
	}

	if want := []Dispatched{{GraderFidelity, waitingPR, waitingHead}}; !reflect.DeepEqual(again, want) {
		t.Fatalf("again = %v, want the older PR once the first one's regrade posted", again)
	}
}

func TestAFirstGradesAHeldHeadWithNoGradeItemAheadOfOlderQueuedWork(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"concurrent": 1}})
	const held, at = 51292, "2153cb1358ef134ea05e856650481af5b88a862e"
	openedAt(t, f.store, waitingPR, waitingHead)
	if _, err := f.store.OpenItem(store.NewItem{Kind: string(machine.ActionLift), Repo: repo, Number: held, Head: at, Mover: machine.MoverRoot, Deadline: machine.RootClock}, t0); err != nil {
		t.Fatal(err)
	}
	post(t, client.Draft{Lane: "dashboard-serve-flake-1", Kind: "hold", Text: "Holding 51292 for his card: product code, per the brief.", PRs: []string{repo + "#51292"}})
	post(t, firstDraft(testDrive, rootLane, held))
	snapshot := map[int]machine.Facts{
		held:      {Repo: repo, Number: held, Head: at, Lane: "dashboard-serve-flake", CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true, Fidelity: machine.FidelityPass, Hold: machine.MoverRoot},
		waitingPR: waiting(),
	}

	dispatched, err := Act(context.Background(), f.store, f.pool, items(t, f.store), snapshot, time.Now(), 1)
	if err != nil {
		t.Fatal(err)
	}

	if want := []Dispatched{{GraderFinder, held, at}}; !reflect.DeepEqual(dispatched, want) {
		t.Fatalf("dispatched = %v, want the held first PR's finder ahead of #%d", dispatched, waitingPR)
	}
}
