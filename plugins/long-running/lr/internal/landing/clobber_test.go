package landing_test

import (
	"bytes"
	"context"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	regionPR  = "release: a watch fails on a monitor's missing data only when that monitor notifies on it (#48959)"
	staffPR   = "cloudrunner: journal refusals of Forge staff teams never page (#49148)"
	connectPR = "iris: connect_app asks to connect an account on its own card (#49121)"
	connected = "## Summary\n\nA new connect_app tool raises its own card in the dashboard.\n"
	countPR   = "sand-next: intern a builtin's result once (#51407)"
	bindPR    = "sand-next: a {sql| ... |sql} block is a checked query (#51724)"
)

func callBuiltin(counted, bound bool) string {
	results, fail, done := "[]Value, error", "nil, err", "out, nil"
	if counted {
		results, fail, done = "[]Value, int, error", "nil, 0, err", "out, len(out), nil"
	}
	bind := ""
	if bound {
		bind = "\tif err := bindQueryArgs(b, args); err != nil {\n\t\treturn " + fail + "\n\t}\n"
	}
	return "package vm\n\nfunc callBuiltin(b Builtin, args []Value) (" + results + ") {\n" +
		"\tif err := b.check(args); err != nil {\n\t\treturn " + fail + "\n\t}\n" +
		"\tb.trace(\"call\")\n\tb.calls++\n" + bind + "\tb.last = args\n" +
		"\tout, err := b.run(args)\n\tif err != nil {\n\t\treturn " + fail + "\n\t}\n" +
		"\treturn " + done + "\n}\n"
}

type landings struct {
	work, checkout string
	clobbers       *landing.Clobbers
	logged         *bytes.Buffer
}

func write(t *testing.T, repo string, files map[string]string, message string) {
	t.Helper()
	for name, text := range files {
		if err := os.WriteFile(filepath.Join(repo, name), []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
		git(t, repo, "add", name)
	}
	git(t, repo, "commit", "-q", "-m", message)
}

func newLandings(t *testing.T) landings {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	root := t.TempDir()
	config := filepath.Join(root, "gitconfig")
	if err := os.WriteFile(config, []byte("[user]\n\tname = Lane\n\temail = lane@example.com\n[init]\n\tdefaultBranch = dev\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GIT_CONFIG_GLOBAL", config)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	origin, work, checkout := filepath.Join(root, "origin.git"), filepath.Join(root, "work"), filepath.Join(root, "checkout")
	git(t, root, "init", "-q", "--bare", origin)
	git(t, root, "clone", "-q", origin, work)
	write(t, work, map[string]string{
		"wire.go":  "type Run struct {\n\tTeam string\n}\n",
		"loop.go":  "log.Warn(\"journal refused\", \"team\", team)\n",
		"card.tsx": "export const ParameterCard = () => null;\n",
	}, "base")
	git(t, work, "push", "-q", "origin", "HEAD:dev")
	git(t, root, "clone", "-q", origin, checkout)
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	for number, lane := range map[int]string{48959: "release-watch-1", 49148: "staffteam-1", 49121: "port-connect-app-1"} {
		if err := s.PutPR(store.PR{Repo: "o/r", Number: number, Head: "h", Lane: lane}); err != nil {
			t.Fatal(err)
		}
		if _, err := s.WantLane(store.Lane{Name: lane, State: store.LaneDesired}, t0); err != nil {
			t.Fatal(err)
		}
	}
	logged := &bytes.Buffer{}
	return landings{work, checkout, &landing.Clobbers{
		Runner: shell.Exec{}, Store: s, Drive: "d", Repo: "o/r", Trunk: "dev", Checkout: checkout,
		Now: func() time.Time { return time.Date(2026, 11, 3, 19, 54, 0, 0, time.UTC) }, Log: slog.New(slog.NewTextHandler(logged, nil)),
	}, logged}
}

func (tr landings) landRegion(t *testing.T) {
	t.Helper()
	git(t, tr.work, "checkout", "-q", "dev")
	write(t, tr.work, map[string]string{"wire.go": "type Run struct {\n\tTeam string\n\tRegion string\n}\n"}, regionPR)
	git(t, tr.work, "push", "-q", "origin", "dev")
}

func (tr landings) landStaff(t *testing.T) {
	t.Helper()
	git(t, tr.work, "checkout", "-q", "dev")
	wire, err := os.ReadFile(filepath.Join(tr.work, "wire.go"))
	if err != nil {
		t.Fatal(err)
	}
	write(t, tr.work, map[string]string{
		"wire.go":  strings.Replace(string(wire), "}\n", "\tStaffTeam bool\n}\n", 1),
		"loop.go":  "log.Warn(\"journal refused\", \"team\", team, \"staffTeam\", staff)\n",
		"card.tsx": "export const ParameterCard = () => <OAuthConnection />;\n",
	}, staffPR)
	git(t, tr.work, "push", "-q", "origin", "dev")
}

func (tr landings) openConnect(t *testing.T) {
	t.Helper()
	git(t, tr.work, "checkout", "-q", "-b", "connect")
	write(t, tr.work, map[string]string{"card.tsx": "export const ConnectAppCard = () => null;\n"}, "connect_app raises its own card")
}

func (tr landings) mergeDev(t *testing.T, resolved bool) {
	t.Helper()
	if !resolved {
		git(t, tr.work, "checkout", "-q", "dev")
		stale := git(t, tr.work, "merge-tree", "--write-tree", "connect", "dev~1")
		merge := git(t, tr.work, "commit-tree", stale, "-p", "connect", "-p", "dev", "-m", "Merge remote-tracking branch 'origin/dev' into connect")
		git(t, tr.work, "update-ref", "refs/heads/connect", merge)
		return
	}
	git(t, tr.work, "checkout", "-q", "connect")
	if out, err := exec.Command("git", "-C", tr.work, "merge", "-q", "dev").CombinedOutput(); err == nil {
		t.Fatalf("merging dev did not conflict: %s", out)
	}
	write(t, tr.work, map[string]string{"card.tsx": "export const ConnectAppCard = () => <OAuthConnection />;\n"}, "Merge dev into connect")
}

func (tr landings) squashConnect(t *testing.T, pushHead bool, description string) {
	t.Helper()
	if pushHead {
		git(t, tr.work, "push", "-q", "origin", "connect:refs/pull/49121/head")
	}
	git(t, tr.work, "checkout", "-q", "dev")
	git(t, tr.work, "merge", "-q", "--squash", "connect")
	git(t, tr.work, "commit", "-q", "-m", connectPR+"\n\n"+description)
	git(t, tr.work, "push", "-q", "origin", "dev")
}

func (tr landings) pass(t *testing.T) {
	t.Helper()
	if err := tr.clobbers.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func clobbered(t *testing.T) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report"}})
	if err != nil {
		t.Fatal(err)
	}
	return records
}

func TestASquashWhoseMergeFromTrunkKeptAStaleTreeTellsBothLanesAndRootWhatItUndid(t *testing.T) {
	tr := newLandings(t)
	tr.openConnect(t)
	tr.landRegion(t)
	tr.landStaff(t)
	tr.pass(t)
	tr.mergeDev(t, false)
	tr.squashConnect(t, true, connected)

	tr.pass(t)

	records := clobbered(t)
	if len(records) != 1 {
		t.Fatalf("records = %+v", records)
	}
	got := records[0]
	for _, want := range []string{"CLOBBERED #49121", "#49148's 3 lines in loop.go, wire.go"} {
		if !strings.Contains(got.Text, want) {
			t.Fatalf("text %q lacks %q", got.Text, want)
		}
	}
	if strings.Contains(got.Text, "#48959") {
		t.Fatalf("credited the landing the stale tree kept: %q", got.Text)
	}
	slices.Sort(got.To)
	if !slices.Equal(got.To, []string{"port-connect-app-1", "root", "staffteam-1"}) {
		t.Fatalf("to = %v", got.To)
	}
	tr.pass(t)
	if again := clobbered(t); len(again) != 1 {
		t.Fatalf("a second pass over the same trunk posted again: %+v", again)
	}
}

func TestAClobberNeverNamesALaneThatStopped(t *testing.T) {
	tr := newLandings(t)
	if err := tr.clobbers.Store.SetLaneState("staffteam-1", store.LaneRetired, t0, "", ""); err != nil {
		t.Fatal(err)
	}
	tr.openConnect(t)
	tr.landRegion(t)
	tr.landStaff(t)
	tr.pass(t)
	tr.mergeDev(t, false)
	tr.squashConnect(t, true, connected)

	tr.pass(t)

	records := clobbered(t)
	if len(records) != 1 {
		t.Fatalf("records = %+v", records)
	}
	slices.Sort(records[0].To)
	if !slices.Equal(records[0].To, []string{"port-connect-app-1", "root"}) {
		t.Fatalf("to = %v", records[0].To)
	}
}

func TestASquashThatUndoesNothingUnsaidPostsNothing(t *testing.T) {
	for name, c := range map[string]struct {
		merge       string
		description string
	}{
		"its description names the PR it undoes":     {"stale", connected + "\nIt takes back #49148's staff field.\n"},
		"its description names the files":            {"stale", connected + "\nIt drops the staff field from wire.go and loop.go.\n"},
		"its merge changed only the conflicted file": {"resolved", connected},
		"its branch carries no merge commit":         {"", connected},
	} {
		t.Run(name, func(t *testing.T) {
			tr := newLandings(t)
			if c.merge == "" {
				tr.landStaff(t)
				tr.openConnect(t)
				write(t, tr.work, map[string]string{
					"wire.go": "type Run struct {\n\tTeam string\n}\n",
					"loop.go": "log.Warn(\"journal refused\", \"team\", team)\n",
				}, "the staff field moves to the card")
				tr.pass(t)
			} else {
				tr.openConnect(t)
				tr.landStaff(t)
				tr.pass(t)
				tr.mergeDev(t, c.merge == "resolved")
			}
			tr.squashConnect(t, true, c.description)

			tr.pass(t)

			if records := clobbered(t); len(records) != 0 {
				t.Fatalf("posted %+v", records)
			}
		})
	}
}

func TestALineThePRsOwnCommitsRemovedNeverCountsAsClobbered(t *testing.T) {
	for name, c := range map[string]struct {
		merged string
		want   string
	}{
		"the merge rewrites trunk's copy as the PR rewrote its own": {callBuiltin(true, true), ""},
		"the merge keeps the PR's stale file":                       {callBuiltin(true, false), "#51724's 1 lines in call.go"},
	} {
		t.Run(name, func(t *testing.T) {
			tr := newLandings(t)
			write(t, tr.work, map[string]string{"call.go": callBuiltin(false, false)}, "the vm calls builtins")
			git(t, tr.work, "push", "-q", "origin", "dev")
			git(t, tr.work, "checkout", "-q", "-b", "count")
			write(t, tr.work, map[string]string{"call.go": callBuiltin(true, false)}, "callBuiltin returns how many values it pushed")
			git(t, tr.work, "checkout", "-q", "dev")
			write(t, tr.work, map[string]string{"call.go": callBuiltin(false, true)}, bindPR)
			git(t, tr.work, "push", "-q", "origin", "dev")
			tr.pass(t)
			git(t, tr.work, "checkout", "-q", "count")
			git(t, tr.work, "merge", "-q", "--no-commit", "dev")
			write(t, tr.work, map[string]string{"call.go": c.merged}, "Merge dev into count")
			git(t, tr.work, "push", "-q", "origin", "count:refs/pull/51407/head")
			git(t, tr.work, "checkout", "-q", "dev")
			git(t, tr.work, "merge", "-q", "--squash", "count")
			git(t, tr.work, "commit", "-q", "-m", countPR+"\n\n## Summary\n\nA builtin's result is interned once.\n")
			git(t, tr.work, "push", "-q", "origin", "dev")

			tr.pass(t)

			records := clobbered(t)
			if c.want == "" {
				if len(records) != 0 {
					t.Fatalf("posted %+v", records)
				}
				return
			}
			if len(records) != 1 || !strings.Contains(records[0].Text, "CLOBBERED #51407") || !strings.Contains(records[0].Text, c.want) {
				t.Fatalf("records = %+v, want one CLOBBERED #51407 naming %q", records, c.want)
			}
		})
	}
}

func TestASquashWhosePRHeadCannotBeReadLogsAndThePassMovesOn(t *testing.T) {
	tr := newLandings(t)
	tr.openConnect(t)
	tr.landStaff(t)
	tr.pass(t)
	tr.mergeDev(t, false)
	tr.squashConnect(t, false, connected)

	tr.pass(t)

	if records := clobbered(t); len(records) != 0 {
		t.Fatalf("posted %+v", records)
	}
	if !strings.Contains(tr.logged.String(), "origin has no head for #49121") {
		t.Fatalf("logged %q", tr.logged.String())
	}
	git(t, tr.work, "push", "-q", "origin", "connect:refs/pull/49121/head")
	tr.pass(t)
	if records := clobbered(t); len(records) != 0 {
		t.Fatalf("a squash already read was read again: %+v", records)
	}
}
