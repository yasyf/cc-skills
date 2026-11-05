package deletions

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func register(t *testing.T, rows map[string]map[string]any) []Ruling {
	t.Helper()
	rulings, err := ParseRulings(string(encoded(t, rows)))
	if err != nil {
		t.Fatal(err)
	}
	return rulings
}

func encoded(t *testing.T, rows map[string]map[string]any) []byte {
	t.Helper()
	stored := map[string]map[string]string{}
	for key, fields := range rows {
		stored[key] = map[string]string{}
		for name, value := range fields {
			text, ok := value.(string)
			if !ok {
				encoded, err := json.Marshal(value)
				if err != nil {
					t.Fatal(err)
				}
				text = string(encoded)
			}
			stored[key][name] = text
		}
	}
	raw, err := json.Marshal(map[string]any{"ledger": "0d4cfeec1316", "rows": stored})
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func removes(state string) []map[string]any {
	return []map[string]any{{"n": 1, "text": "the Go wasm host goes", "direction": "removes", "state": state}}
}

func TestAnInForceRemovesRowNeedsAnOwedTaskOrAnExceptionForEachThingItNames(t *testing.T) {
	owed := []Owed{
		{ID: "e00667e1", Title: "Owed deletion: the Go wasm host", Owner: "one-reader-1", Dirs: []string{"go/sand/next"}, Symbols: []string{"ReplicaHost"}},
		{ID: "5f1d2a3b", Title: "Owed deletion: the old follower", Owner: "one-reader-delete-1", Dirs: []string{"go/sandsql-follower"}},
	}
	cases := []struct {
		name string
		row  map[string]any
		want []string
	}{
		{"a symbol no task names", map[string]any{"status": "in-force", "implications": removes("proposed"), "symbols": `["Incarnation"]`}, []string{"Incarnation"}},
		{"a symbol a task names", map[string]any{"status": "in-force", "implications": removes("ticked"), "symbols": "ReplicaHost", "paths": `["go/sand/next/internal/sandsql/component/*.go:12-40"]`}, nil},
		{"a qualified symbol a task names bare", map[string]any{"status": "in-force", "implications": removes("proposed"), "symbols": `["component.ReplicaHost"]`}, nil},
		{"a path under a task's directory", map[string]any{"status": "in-force", "implications": removes("proposed"), "paths": `["go/sandsql-follower/main.go @ 9bcde635"]`}, nil},
		{"a glob matching a task's directory", map[string]any{"status": "in-force", "implications": removes("proposed"), "paths": "go/sandsql-*"}, nil},
		{"a path no task anchors", map[string]any{"status": "in-force", "implications": removes("proposed"), "paths": `["dashboard/src/iris/journal", "pending"]`}, []string{"dashboard/src/iris/journal"}},
		{"a struck removes", map[string]any{"status": "in-force", "implications": removes("struck"), "symbols": "Incarnation"}, nil},
		{"a requires", map[string]any{"status": "in-force", "implications": []map[string]any{{"n": 1, "direction": "requires", "state": "proposed"}}, "symbols": "Incarnation"}, nil},
		{"a row not yet in force", map[string]any{"status": "pending-owner", "implications": removes("proposed"), "symbols": "Incarnation"}, nil},
		{"a removes naming nothing", map[string]any{"status": "in-force", "implications": removes("proposed"), "paths": "pending"}, nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			gaps := Gaps(register(t, map[string]map[string]any{"order/one-reader": c.row}), owed)
			named := make([]string, 0, len(gaps))
			for _, gap := range gaps {
				if !slices.Equal(gap.Keys, []string{"order/one-reader"}) {
					t.Fatalf("gap %+v names another row", gap)
				}
				named = append(named, gap.Target)
			}
			if !slices.Equal(named, c.want) {
				t.Fatalf("gaps = %v, want %v", named, c.want)
			}
		})
	}
}

func TestAnExceptionRowAnchoredToTheTargetIsItsRecordedReason(t *testing.T) {
	rows := map[string]map[string]any{
		"order/incarnation":      {"status": "in-force", "implications": removes("proposed"), "symbols": `["Incarnation", "Epoch"]`},
		"exception/incarnation":  {"kind": "exception", "status": "pending-owner", "symbols": "Incarnation"},
		"exception/epoch-gone":   {"kind": "exception", "status": "superseded", "symbols": "Epoch"},
		"corpus/b-tests":         {"kind": "exception", "symbols": "Epoch"},
		"order/unrelated-anchor": {"status": "in-force", "symbols": "Epoch"},
	}

	gaps := Gaps(register(t, rows), nil)

	if want := []Gap{{Target: "Epoch", Symbol: true, Keys: []string{"order/incarnation"}}}; !reflect.DeepEqual(gaps, want) {
		t.Fatalf("gaps = %+v, want %+v", gaps, want)
	}
}

func TestRowsRemovingTheSameTargetShareOneGap(t *testing.T) {
	rows := map[string]map[string]any{
		"order/incarnation":    {"status": "in-force", "implications": removes("proposed"), "symbols": `["journal.Incarnation", "Epoch"]`},
		"design/one-reader":    {"status": "in-force", "implications": removes("proposed"), "symbols": `["Incarnation", "Incarnation"]`},
		"order/journal-client": {"status": "in-force", "implications": removes("proposed"), "paths": `["go/sand/next/internal/journal/client.go:10-40"]`},
		"design/journal":       {"status": "in-force", "implications": removes("proposed"), "paths": `["go/sand/next/internal/journal/client.go @ 9bcde635"]`},
	}

	gaps := Gaps(register(t, rows), nil)

	want := []Gap{
		{Target: "Epoch", Symbol: true, Keys: []string{"order/incarnation"}},
		{Target: "Incarnation", Symbol: true, Keys: []string{"design/one-reader", "order/incarnation"}},
		{Target: "go/sand/next/internal/journal/client.go", Keys: []string{"design/journal", "order/journal-client"}, Paths: []string{"go/sand/next/internal/journal/client.go"}},
	}
	if !reflect.DeepEqual(gaps, want) {
		t.Fatalf("gaps = %+v, want %+v", gaps, want)
	}
}

func TestAnAnchorARowGuardsOwesNoDeletion(t *testing.T) {
	rows := map[string]map[string]any{
		"rule/no-rollback": {"status": "in-force", "implications": removes("proposed"), "symbols": `["Sources.crosses", "rollbackNow"]`, "guards": `["Sources.crosses"]`},
		"rule/no-polling":  {"status": "in-force", "implications": removes("proposed"), "paths": `["go/follower/poll.go:12 @ 2a00d64", "go/follower/watch.go"]`, "guards": `["go/follower/watch.go"]`},
		"rule/other-row":   {"status": "in-force", "implications": removes("proposed"), "symbols": `["crosses"]`},
	}

	gaps := Gaps(register(t, rows), nil)

	targets := make([]string, 0, len(gaps))
	for _, gap := range gaps {
		targets = append(targets, gap.Target+" "+strings.Join(gap.Keys, ","))
	}
	if want := []string{"crosses rule/other-row", "go/follower/poll.go rule/no-polling", "rollbackNow rule/no-rollback"}; !slices.Equal(targets, want) {
		t.Fatalf("gaps = %v, want %v", targets, want)
	}
}

func TestATargetGoneFromTheNewestTrunkOwesNoDeletionWhileAnotherFetchHoldsItsRefLock(t *testing.T) {
	root := t.TempDir()
	write(t, root, map[string]string{"gitconfig": "[user]\n\tname = Lane\n\temail = lane@example.com\n[init]\n\tdefaultBranch = main\n"})
	t.Setenv("GIT_CONFIG_GLOBAL", filepath.Join(root, "gitconfig"))
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	origin, work, checkout := filepath.Join(root, "origin.git"), filepath.Join(root, "work"), filepath.Join(root, "checkout")
	git(t, root, "init", "-q", "--bare", origin)
	git(t, root, "clone", "-q", origin, work)
	write(t, work, map[string]string{
		"rust/admin.rs":           "impl AdminState {\n    fn positions() {}\n}\n",
		"go/run/storage.go":       "func closeStore() {}\n",
		"go/run/takeover.go":      "func TakeOverRun() {}\n",
		"go/run/takeover_test.go": "func onlyInTest() {}\n",
		"go/old/main.go":          "package main\n",
	})
	git(t, work, "add", ".")
	git(t, work, "commit", "-q", "-m", "tree")
	git(t, work, "push", "-q", "origin", "HEAD:main")
	git(t, root, "clone", "-q", origin, checkout)
	git(t, work, "rm", "-q", "go/old/main.go")
	git(t, work, "commit", "-q", "-m", "gone")
	git(t, work, "push", "-q", "origin", "HEAD:main")
	rows := encoded(t, map[string]map[string]any{
		"rule/seal":       {"status": "in-force", "implications": removes("proposed"), "symbols": `["AdminState::positions", "runStorage.closeStore", "Restore::plan"]`, "paths": `["rust/admin.rs:191 @ 19dd3ce", "go/run/storage.go", "rust/restore.rs:735 @ 2a00d64"]`},
		"rule/takeover":   {"status": "in-force", "implications": removes("proposed"), "symbols": `["CloudHost.TakeOverRun", "onlyInTest"]`, "paths": `["go/run/takeover.go", "go/run/takeover_test.go"]`},
		"rule/old-main":   {"status": "in-force", "implications": removes("proposed"), "paths": `["go/old/main.go"]`},
		"rule/run-dir":    {"status": "in-force", "implications": removes("proposed"), "paths": `["go/run"]`},
		"rule/unanchored": {"status": "in-force", "implications": removes("proposed"), "symbols": `["Floating"]`},
	})
	write(t, checkout, map[string]string{".git/refs/remotes/origin/main.lock": ""})
	trunk := Trunk{Runner: notes{rows: rows}, Checkout: checkout, Branch: "main"}

	gaps, err := trunk.Gaps(context.Background(), "rulings.py", "durable-agents-rebuild")
	if err != nil {
		t.Fatal(err)
	}

	want := []string{
		"AdminState::positions, removed by rule/seal",
		"Floating, removed by rule/unanchored",
		"TakeOverRun, removed by rule/takeover",
		"closeStore, removed by rule/seal",
		"go/run, removed by rule/run-dir",
	}
	if !slices.Equal(gaps, want) {
		t.Fatalf("gaps = %q, want %q", gaps, want)
	}
}

func TestAnOwedDeletionIsOverdueOnceItsDueDayHasPassed(t *testing.T) {
	due := Owed{Due: time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC)}
	for _, c := range []struct {
		row  Owed
		at   time.Time
		want bool
	}{
		{due, time.Date(2026, 11, 9, 23, 0, 0, 0, time.UTC), false},
		{due, time.Date(2026, 11, 10, 1, 0, 0, 0, time.UTC), true},
		{Owed{}, time.Date(2030, 1, 1, 0, 0, 0, 0, time.UTC), false},
	} {
		if got := c.row.Overdue(c.at); got != c.want {
			t.Errorf("due %v overdue at %v = %v, want %v", c.row.Due, c.at, got, c.want)
		}
	}
}

type notes struct {
	tasks map[string]string
	rows  []byte
}

func (n notes) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[0] == "rulings.py" {
		return shell.Result{Stdout: string(n.rows)}, nil
	}
	if c.Argv[0] != "ccn" {
		return shell.Exec{}.Run(ctx, c)
	}
	if c.Argv[4] == "show" {
		return shell.Result{Stdout: n.tasks[c.Argv[5]]}, nil
	}
	ids := make([]map[string]string, 0, len(n.tasks))
	for id := range n.tasks {
		ids = append(ids, map[string]string{"id": id, "title": "Owed deletion: " + id, "status": "open"})
	}
	listed, err := json.Marshal(ids)
	return shell.Result{Stdout: string(listed)}, err
}

func shown(t *testing.T, id, assignee string, anchors map[string]string, labels ...string) string {
	t.Helper()
	found := map[string]any{"id": id, "title": "Owed deletion: " + id, "status": "open", "labels": append([]string{Label, "program:durable-agents-rebuild"}, labels...)}
	if assignee != "" {
		found["assignee"] = assignee
	}
	listed := make([]map[string]string, 0, len(anchors))
	for value, kind := range anchors {
		listed = append(listed, map[string]string{"kind": kind, "value": value})
	}
	found["anchors"] = listed
	encoded, err := json.Marshal(found)
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded)
}

func git(t *testing.T, dir string, args ...string) {
	t.Helper()
	if out, err := exec.Command("git", append([]string{"-C", dir}, args...)...).CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

func write(t *testing.T, dir string, files map[string]string) {
	t.Helper()
	for name, text := range files {
		path := filepath.Join(dir, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func TestPresentKeepsTheOpenTasksStillOnTheNewestTrunkWhileAnotherFetchHoldsItsRefLock(t *testing.T) {
	root := t.TempDir()
	write(t, root, map[string]string{"gitconfig": "[user]\n\tname = Lane\n\temail = lane@example.com\n[init]\n\tdefaultBranch = main\n"})
	t.Setenv("GIT_CONFIG_GLOBAL", filepath.Join(root, "gitconfig"))
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	origin, work, checkout := filepath.Join(root, "origin.git"), filepath.Join(root, "work"), filepath.Join(root, "checkout")
	git(t, root, "init", "-q", "--bare", origin)
	git(t, root, "clone", "-q", origin, work)
	write(t, work, map[string]string{
		"go/next/cmd/serve.go":      "func serveFollow() {}\n",
		"go/next/cmd/serve_test.go": "func oldHelper() {}\n",
		"go/next/cmd/notes.txt":     "oldNote\n",
		"go/next/cmd/gone.go":       "func goneSoon() {}\n",
		"go/follower/main.go":       "package main\n",
		"rust/config.rs":            "pub follower_bin: PathBuf,\n",
	})
	git(t, work, "add", ".")
	git(t, work, "commit", "-q", "-m", "tree")
	git(t, work, "push", "-q", "origin", "HEAD:main")
	git(t, root, "clone", "-q", origin, checkout)
	git(t, work, "rm", "-q", "go/next/cmd/gone.go")
	git(t, work, "commit", "-q", "-m", "gone")
	git(t, work, "push", "-q", "origin", "HEAD:main")
	owner := "one-reader-1"
	tasks := map[string]string{
		"dir":           shown(t, "dir", owner+" <lane@example.com>", map[string]string{"go/follower": "dir"}),
		"symbol-in-dir": shown(t, "symbol-in-dir", owner, map[string]string{"go/next": "dir"}, "symbol:serveFollow", "due:2026-11-09"),
		"qualified":     shown(t, "qualified", owner, map[string]string{"go/next/cmd/serve.go": "path"}, "symbol:cmd.serveFollow"),
		"non-go-file":   shown(t, "non-go-file", owner, map[string]string{"rust/config.rs": "path"}, "symbol:follower_bin"),
		"unanchored":    shown(t, "unanchored", owner, nil),
		"test-only":     shown(t, "test-only", owner, map[string]string{"go/next": "dir"}, "symbol:oldHelper"),
		"non-go-only":   shown(t, "non-go-only", owner, map[string]string{"go/next": "dir"}, "symbol:oldNote"),
		"word-prefix":   shown(t, "word-prefix", owner, map[string]string{"go/next": "dir"}, "symbol:serveFollo"),
		"deleted":       shown(t, "deleted", owner, map[string]string{"go/next/cmd/gone.go": "path"}),
		"deleted-sym":   shown(t, "deleted-sym", owner, map[string]string{"go/next/cmd/gone.go": "path"}, "symbol:goneSoon"),
		"never":         shown(t, "never", owner, map[string]string{"go/next/cmd/never.go": "path"}),
		"another-lane":  shown(t, "another-lane", "other-lane-2", map[string]string{"go/follower": "dir"}),
	}
	write(t, checkout, map[string]string{".git/refs/remotes/origin/main.lock": ""})
	trunk := Trunk{Runner: notes{tasks: tasks}, Checkout: checkout, Branch: "main"}

	listed, err := trunk.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	owed, err := trunk.Present(context.Background(), slices.DeleteFunc(listed, func(row Owed) bool { return row.Owner != owner }))
	if err != nil {
		t.Fatal(err)
	}

	ids := make([]string, 0, len(owed))
	for _, row := range owed {
		ids = append(ids, row.ID)
		if row.Owner != owner {
			t.Errorf("task %s owner = %q, want %q", row.ID, row.Owner, owner)
		}
		if row.ID == "symbol-in-dir" && !row.Due.Equal(time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC)) {
			t.Errorf("due = %v, want its due: label", row.Due)
		}
	}
	slices.Sort(ids)
	if want := []string{"dir", "non-go-file", "qualified", "symbol-in-dir", "unanchored"}; !reflect.DeepEqual(ids, want) {
		t.Fatalf("owed = %v, want %v", ids, want)
	}
}
