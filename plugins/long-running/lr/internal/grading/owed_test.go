package grading

import (
	"context"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func TestAGradeWhoseTasksFetchTimesOutGradesFromTheLocalListAndSaysSo(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Reply(`[{"id":"aaaaaaa1","title":"delete the old loop","assignee":"chores"}]`, "ccn", "-R", "/briefs", "task")
	runner.Fail(1, "sync --prefix refs/cc-notes/tasks/: context deadline exceeded", "ccn", "-R", "/briefs", "sync")
	pool := &JobPool{PoolDeps: PoolDeps{Runner: runner}, briefs: "/briefs"}

	lines, missing := pool.owedDeletions(context.Background(), "Deletes the loop owed by task bbbbbbb2.")

	if len(missing) != 0 || len(lines) != 2 || !strings.Contains(lines[0], "fetching the newest tasks failed") || !strings.Contains(lines[1], "task aaaaaaa, owner chores") {
		t.Fatalf("lines %q, missing %q; want the local list with a note that the fetch failed, and nothing missing", lines, missing)
	}
}
