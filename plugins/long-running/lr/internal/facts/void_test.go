package facts_test

import (
	"context"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

func decide(t *testing.T, lane, text string) int64 {
	t.Helper()
	record, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: "decide", Text: text, PRs: []string{repo + "#1"}})
	if err != nil {
		t.Fatal(err)
	}
	return record.Seq
}

func TestAVoidedFinderOkLeavesItsHeadOwingTheFinderUntilANewGradeReplacesIt(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	review(t, "finder-desk", "finder: ok "+head+" luna finder over delta 16d9254..e718a74: no majors", 1)
	review(t, "fidelity-pipeline", "fidelity: ok "+head+" stays in scope", 1)
	void := decide(t, "root", "finder-void #1 at "+head+": 519896. Its ok carried a misscoped pick.")

	if unfolded := pushedHeads(t, h)[1].Facts; unfolded.Finder != "" || unfolded.FinderVoid != void {
		t.Fatalf("pushed head = %+v, want the void counted before the pass folds it", unfolded)
	}
	voided := h.snapshot(t)[1]
	if voided.Finder != "" || voided.FinderVoid != void || voided.Fidelity != machine.FidelityPass {
		t.Fatalf("voided = %+v, want the finder owed again and fidelity kept", voided)
	}

	review(t, "finder-desk", "finder: ok "+head+" luna finder over whole: no majors", 1)

	if regraded := h.snapshot(t)[1]; regraded.Finder != machine.FinderClear || regraded.FinderVoid != 0 {
		t.Fatalf("regraded = %+v, want the new ok standing in the void's place", regraded)
	}
}

func TestAVoidFromALaneOrAtAnOlderHeadLeavesTheGradeStanding(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: 1, head: head, login: "drive-bot"}}, status(1))
	review(t, "finder-desk", "finder: ok "+head2+" luna finder over delta: no majors", 1)
	review(t, "finder-desk", "finder: ok "+head+" luna finder over delta: no majors", 1)
	decide(t, "pr-unblock-9", "finder-void #1 at "+head+": 519896")
	decide(t, "root", "finder-void #1 at "+head2+": 519895")

	if row := h.snapshot(t)[1]; row.Finder != machine.FinderClear || row.FinderVoid != 0 {
		t.Fatalf("row = %+v, want the ok at the current head standing", row)
	}
}
