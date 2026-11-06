package grading

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	lrfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

func TestAVoidedFinderOkIsListedAndGetsAWholePassKeyedByTheVoidNotItsSettledPass(t *testing.T) {
	f := newPool(t, map[string]any{"landing": map[string]any{"repo": repo, "checkout": "/srv/main"}, "finder": map[string]any{"sprite": "dar-1"}})
	settled := f.finderDir(strconv.Itoa(number) + "-" + short(head))
	if err := os.MkdirAll(settled, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(settled, grader.JobFile), []byte(`{}`), 0o600); err != nil {
		t.Fatal(err)
	}
	writeResult(t, settled, `{"verdict":"ok"}`, f.now)
	graded := post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: "finder: ok " + head + " luna finder over delta 16d9254..e718a74: no majors", PRs: []string{pull}})
	void := post(t, client.Draft{Lane: rootLane, Kind: kindDecide, Text: "finder-void #45489 at " + head + ": " + strconv.FormatInt(graded, 10), PRs: []string{pull}})
	openedAt(t, f.store, number, head)
	voided := facts(head, func(p *machine.Facts) { p.Fidelity, p.FinderVoid = machine.FidelityPass, void })

	if rows := queue(t, f, GraderFinder, nil, map[int]lrfacts.Pushed{number: {Facts: voided}}); len(rows) != 1 || rows[0].Number != number {
		t.Fatalf("finder queue = %+v, want the voided head listed", rows)
	}
	dispatched, err := Act(context.Background(), f.store, f.pool, items(t, f.store), map[int]machine.Facts{number: voided}, f.now, 1)
	if err != nil {
		t.Fatal(err)
	}

	if want := []Dispatched{{GraderFinder, number, head}}; !reflect.DeepEqual(dispatched, want) {
		t.Fatalf("dispatched = %v, want %v", dispatched, want)
	}
	if spec := readJob[grader.FinderJob](t, f.finderDir(strconv.Itoa(number)+wholeMark+strconv.FormatInt(void, 10))); !spec.Full || spec.Head != "" || spec.Prior != nil {
		t.Fatalf("job = %+v, want a whole-diff pass", spec)
	}
	if exists(filepath.Join(settled, repostedFile)) {
		t.Fatal("the voided grade's settled pass was reposted")
	}
}
