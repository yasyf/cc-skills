package replay

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx/ccxtest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var (
	now         = time.Date(2026, 11, 1, 20, 0, 0, 0, time.UTC)
	sectionLine = regexp.MustCompile(`^\s*(?:\[+[^\]]+\]+|"[^"]+":\s*\{|\},?)\s*$`)
)

func fixtures(t *testing.T) map[string]Fixture {
	t.Helper()
	paths, err := filepath.Glob("../../../tests/fixtures/replay/*.json")
	if err != nil || len(paths) == 0 {
		t.Fatalf("fixtures %v err %v", paths, err)
	}
	found := make(map[string]Fixture, len(paths))
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var fixture Fixture
		if err := json.Unmarshal(data, &fixture); err != nil {
			t.Fatal(err)
		}
		found[strings.TrimSuffix(filepath.Base(path), ".json")] = fixture
	}
	return found
}

func TestEachFixtureReplaysToWhatItsFixDecides(t *testing.T) {
	for name, fixture := range fixtures(t) {
		t.Run(name, func(t *testing.T) {
			if fixture.Class != name {
				t.Fatalf("class %q in file %q", fixture.Class, name)
			}
			var given Input
			if err := json.Unmarshal(fixture.Input, &given); err != nil {
				t.Fatal(err)
			}
			decided, err := Decide(given)
			if err != nil {
				t.Fatal(err)
			}
			encoded, err := json.Marshal(decided)
			if err != nil {
				t.Fatal(err)
			}
			var got, want map[string]any
			if err := json.Unmarshal(encoded, &got); err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(fixture.Expect, &want); err != nil {
				t.Fatal(err)
			}
			for key, expected := range want {
				if !reflect.DeepEqual(got[key], expected) {
					t.Fatalf("%s: got %v, want %v", key, got[key], expected)
				}
			}
		})
	}
}

func TestAFixtureCarriesOnlyWhatTheChecksRead(t *testing.T) {
	for name, fixture := range fixtures(t) {
		t.Run(name, func(t *testing.T) {
			var given Input
			if err := json.Unmarshal(fixture.Input, &given); err != nil {
				t.Fatal(err)
			}
			for _, record := range given.Records {
				if got := scrubbedText(record.Text); got != record.Text {
					t.Fatalf("record %d scrubs to %q from %q", record.Seq, got, record.Text)
				}
			}
			for _, entry := range given.PRs {
				for file, patch := range entry.Patches {
					if patch == nil {
						continue
					}
					for line := range strings.SplitSeq(strings.TrimRight(*patch, "\n"), "\n") {
						if line != "@@" && line[1:] != "x" && !sectionLine.MatchString(line[1:]) {
							t.Fatalf("%s carries %q", file, line)
						}
					}
				}
			}
		})
	}
}

func TestEveryRecurrencePatternCompiles(t *testing.T) {
	for name, fixture := range fixtures(t) {
		if _, err := regexp.Compile("(?i)" + fixture.Recurs.Text); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
	}
}

func TestScrubbingKeepsTheLinesTheMoverReadsAndDropsTheProse(t *testing.T) {
	cases := map[string]string{
		"SPAWNED takeover-500-tooling-fix /Users/x/.claude/scratch/drive/takeover-500-tooling/brief.md":                                      "SPAWNED takeover-500-tooling-fix /scratch/takeover-500-tooling/brief.md",
		"OUTCOME msg_585d takeover-500-tooling-fix: worker_done succeeded dispatch=ctx_ea33b9fa3728: Source fix merged; deploy /Users/x/run": "OUTCOME msg_585d takeover-500-tooling-fix: worker_done succeeded dispatch=ctx_ea33b9fa3728",
		"3:07 AM stand-down conflicts-2: false relaunch of a customer's lane":                                                                "stand-down conflicts-2",
		"the customer cluster's api is down for SoFi":                                                                                        "",
		"retired, stand-down, ok?": "retired; stand-down?",
	}
	for text, want := range cases {
		if got := scrubbedText(text); got != want {
			t.Fatalf("%q scrubbed to %q, want %q", text, got, want)
		}
	}
}

func TestAPatchScrubsToItsShape(t *testing.T) {
	patch := "@@ -1,2 +1,3 @@ [deps]\n [dev-dependencies]\n+secret-crate = \"1\"\n-other = \"2\"\n"
	got := scrubbedPatch("go/Cargo.toml", &patch)
	if got == nil || *got != "@@\n [dev-dependencies]\n+x\n-x\n" {
		t.Fatalf("got %v", got)
	}
	if scrubbedPatch("go/main.go", &patch) != nil {
		t.Fatal("a file the check reads by name keeps its patch")
	}
}

func TestARowReadsAsItStoodAtEachTick(t *testing.T) {
	rows := []WhoRow{
		{Row: lanes.Row{Lane: "a", SeenAt: now, StoodDown: true}, AnnouncedAt: now.Add(-2 * time.Hour)},
		{Row: lanes.Row{Lane: "b", Live: true, SeenAt: now}, AnnouncedAt: now.Add(-5 * time.Minute)},
	}
	orca := []dispatch{{"c", now.Add(-time.Hour), now.Add(-30 * time.Minute)}}
	stoodDown := map[string]time.Time{"a": now.Add(-10 * time.Minute)}
	then := now.Add(-time.Hour)

	got := whoAt(rows, orca, stoodDown, then)
	want := []lanes.Row{{Lane: "c", Live: true, SeenAt: then}, {Lane: "a", Live: true, SeenAt: then}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v", got)
	}
	got = whoAt(rows, orca, stoodDown, now)
	if got[0] != (lanes.Row{Lane: "c", SeenAt: now.Add(-time.Hour)}) || !got[1].StoodDown {
		t.Fatalf("got %+v", got)
	}
}

func patchOf(text string) *string { return &text }

func TestTheClassCheckNamesWhatMakesAChangeCode(t *testing.T) {
	devManifest := "@@ -1,1 +1,2 @@\n [dev-dependencies]\n+a = \"1\"\n"
	production := "@@ -1,1 +1,2 @@\n [dependencies]\n+a = \"1\"\n"
	cases := []struct {
		name    string
		patches map[string]*string
		want    string
	}{
		{"tests and CI", map[string]*string{"go/a_test.go": nil, ".github/workflows/x.yml": nil, "docs/a.go": nil}, ""},
		{"source", map[string]*string{"go/a_test.go": nil, "go/a.go": nil}, "go/a.go"},
		{"an unread file stands", map[string]*string{"README.md": nil}, ""},
		{"a dev manifest and its lockfile", map[string]*string{"go/Cargo.toml": patchOf(devManifest), "Cargo.lock": patchOf("@@\n+x\n")}, ""},
		{"a production manifest", map[string]*string{"go/Cargo.toml": patchOf(production)}, "go/Cargo.toml"},
		{"a lockfile that removes", map[string]*string{"go/Cargo.toml": patchOf(devManifest), "Cargo.lock": patchOf("@@\n-x\n")}, "Cargo.lock"},
		{"a lint config", map[string]*string{".golangci.yml": nil}, ""},
		{"tooling", map[string]*string{".agents/skills/x/scripts/a.py": nil}, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := testOnlyOffender(tc.patches); got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

type fakeRunner struct {
	calls   [][]string
	records string
	who     string
	decided map[string]string
}

func (f *fakeRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.calls = append(f.calls, c.Argv)
	switch {
	case slices.Equal(c.Argv[:2], []string{"cci", "tail"}):
		return shell.Result{Stdout: f.records}, nil
	case slices.Equal(c.Argv[:2], []string{"cci", "who"}):
		return shell.Result{Stdout: f.who}, nil
	case c.Argv[0] == "cci":
		return shell.Result{}, nil
	}
	for version, out := range f.decided {
		if strings.Contains(c.Argv[0], "/"+version+"/") {
			if !slices.Equal(c.Env, []string{pinEnv + "=" + version}) {
				return shell.Result{}, fmt.Errorf("env %v", c.Env)
			}
			return shell.Result{Stdout: out}, nil
		}
	}
	return shell.Result{}, fmt.Errorf("no reply for %v", c.Argv)
}

func (f *fakeRunner) posts() [][]string {
	var found [][]string
	for _, call := range f.calls {
		if slices.Equal(call[:2], []string{"cci", "post"}) {
			found = append(found, call)
		}
	}
	return found
}

func (f *fakeRunner) ran(name string) int {
	count := 0
	for _, call := range f.calls {
		if strings.HasSuffix(call[0], name) {
			count++
		}
	}
	return count
}

func argAfter(call []string, flag string) string {
	return call[slices.Index(call, flag)+1]
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

func gateDeps(t *testing.T, runner *fakeRunner, out *strings.Builder) Deps {
	t.Helper()
	return Deps{Runner: runner, CCX: &ccxtest.Fake{GHOut: map[string]string{"api --paginate repos/o/r/pulls/47425/files --jq " + filesJQ: `["go/go.mod",null]` + "\n"}}, Installs: t.TempDir(), Now: func() time.Time { return now }, Stdout: out}
}

func recordLine(seq int64, at time.Time, kind, text string, prs ...int) string {
	line, err := json.Marshal(map[string]any{"seq": seq, "lane": "desk-runner", "kind": kind, "at": iso(at), "text": text, "refs": map[string]any{"prs": prs}})
	if err != nil {
		panic(err)
	}
	return string(line) + "\n"
}

func TestTheGateRefusesAVersionThatRelaunchesOrRefusesWhatTheOldOneDidNot(t *testing.T) {
	runner := &fakeRunner{
		records: recordLine(8, now.Add(-30*time.Hour), "note", "older") + recordLine(9, now.Add(-time.Hour), "note", `EXEMPT-ADMITTED #47425 at a1b2c3d4e5: fidelity grade cci #9 says "test-only or CI-only, no non-test code"`, 47425),
		decided: map[string]string{
			"0.7.820": `{"relaunched":["a"],"held":[],"refused":[]}`,
			"0.7.821": `{"relaunched":["a","b"],"held":[],"refused":[47425]}`,
		},
	}
	var out strings.Builder
	d := gateDeps(t, runner, &out)

	code, err := d.Gate(context.Background(), "d1", "o/r", "0.7.820", "0.7.821")
	if err != nil || code != Refused {
		t.Fatalf("got %d %v", code, err)
	}

	posts := runner.posts()
	if len(posts) != 1 || argAfter(posts[0], "--kind") != "blocker" || argAfter(posts[0], "--id") != "replay-refused:0.7.821" {
		t.Fatalf("posts %v", posts)
	}
	text := argAfter(posts[0], "--text")
	for _, want := range []string{"replayed 1 records and 1 test-only class checks of the last 24 hours", "relaunches b; refuses #47425's test-only claim, which 0.7.820 did not"} {
		if !strings.Contains(text, want) {
			t.Fatalf("no %q in %q", want, text)
		}
	}
	if strings.Contains(text, "relaunches a;") || !strings.Contains(out.String(), "relaunches b") {
		t.Fatalf("text %q out %q", text, out.String())
	}
}

func TestTheGatePassesAVersionThatDecidesNoMoreAndNamesTheClassesItAdds(t *testing.T) {
	runner := &fakeRunner{
		records: recordLine(9, now.Add(-time.Hour), "note", "x"),
		decided: map[string]string{
			"0.7.820": `{"relaunched":["a"],"refused":[47425]}`,
			"0.7.821": `{"relaunched":[],"held":["a"],"refused":[]}`,
		},
	}
	var out strings.Builder
	d := gateDeps(t, runner, &out)
	write(t, filepath.Join(d.Installs, "0.7.821", "tests", "fixtures", "replay", "class-check-unclassed-file.json"), `{"defect":293707,"fix":"yasyf/cc-skills#1"}`)

	code, err := d.Gate(context.Background(), "d1", "o/r", "0.7.820", "0.7.821")
	if err != nil || code != 0 {
		t.Fatalf("got %d %v", code, err)
	}

	posts := runner.posts()
	if len(posts) != 1 || argAfter(posts[0], "--kind") != "state" || argAfter(posts[0], "--id") != "replay-passed:0.7.821" {
		t.Fatalf("posts %v", posts)
	}
	text := argAfter(posts[0], "--text")
	if !strings.HasPrefix(text, passed) || !strings.Contains(text, "class-check-unclassed-file (defect #293707, yasyf/cc-skills#1)") {
		t.Fatalf("text %q", text)
	}
	if !slices.Contains(posts[0], "classes=class-check-unclassed-file") || !slices.Contains(posts[0], "version=0.7.821") {
		t.Fatalf("flags %v", posts[0])
	}
}

func TestTheGateReadsNothingWhenBothVersionsRunOneBinary(t *testing.T) {
	runner := &fakeRunner{}
	var out strings.Builder
	d := gateDeps(t, runner, &out)
	shim := `  replay.py) exec "$ROOT/bin/lr" replay "$@" ;;`
	for _, version := range []string{"0.7.820", "0.7.821"} {
		write(t, filepath.Join(d.Installs, version, "bin", descriptor), `{"digest":"same"}`)
		write(t, filepath.Join(d.Installs, version, "scripts", "bin-shim.sh"), shim)
	}

	code, err := d.Gate(context.Background(), "d1", "o/r", "0.7.820", "0.7.821")
	if err != nil || code != 0 {
		t.Fatalf("got %d %v", code, err)
	}

	if runner.ran("replay.py") != 0 || len(runner.calls) != 1 {
		t.Fatalf("calls %v", runner.calls)
	}
	want := passed + " 0.7.821 runs the binary 0.7.820 runs, so it replays nothing; it installs no new fixture class"
	if got := argAfter(runner.posts()[0], "--text"); got != want {
		t.Fatalf("got %q", got)
	}
}

func TestAPythonPackIsReplayedEvenWithTheBinaryOfItsSuccessor(t *testing.T) {
	runner := &fakeRunner{decided: map[string]string{"0.7.820": `{}`, "0.7.821": `{}`}}
	var out strings.Builder
	d := gateDeps(t, runner, &out)
	for _, version := range []string{"0.7.820", "0.7.821"} {
		write(t, filepath.Join(d.Installs, version, "bin", descriptor), `{"digest":"same"}`)
	}
	write(t, filepath.Join(d.Installs, "0.7.821", "scripts", "bin-shim.sh"), `  replay.py) exec "$ROOT/bin/lr" replay "$@" ;;`)

	if _, err := d.Gate(context.Background(), "d1", "o/r", "0.7.820", "0.7.821"); err != nil {
		t.Fatal(err)
	}

	if runner.ran("replay.py") != 2 {
		t.Fatalf("calls %v", runner.calls)
	}
}

func TestRecurrencesAreCountedOnceTheDayAfterAnInstallHasRun(t *testing.T) {
	installed := now.Add(-25 * time.Hour)
	line := func(seq int64, lane, kind string, at time.Time, text string, attrs map[string]string) string {
		encoded, err := json.Marshal(map[string]any{"seq": seq, "lane": lane, "kind": kind, "at": iso(at), "text": text, "refs": map[string]any{}, "fields": map[string]any{"attrs": attrs}})
		if err != nil {
			t.Fatal(err)
		}
		return string(encoded) + "\n"
	}
	runner := &fakeRunner{records: line(1, "replay", "state", installed, "4:00 PM "+passed+" 0.7.821 ...", map[string]string{"version": "0.7.821", "classes": "supervisor-finished-lane"}) +
		line(2, "root", "stand-down", installed.Add(3*time.Hour), "stand-down x-2: false relaunch", nil) +
		line(3, "root", "note", installed.Add(4*time.Hour), "false relaunch, noted", nil) +
		line(4, "root", "defect", installed.Add(24*time.Hour+30*time.Minute), "false relaunch again", nil) +
		line(5, "replay", "state", now.Add(-2*time.Hour), passed+" 0.7.822", map[string]string{"version": "0.7.822", "classes": "supervisor-live-fix-alias"})}
	var out strings.Builder
	d := gateDeps(t, runner, &out)
	write(t, filepath.Join(d.Installs, "0.7.821", "tests", "fixtures", "replay", "supervisor-finished-lane.json"),
		`{"defect":283488,"fix":"yasyf/cc-skills#1575","recurs":{"kinds":["stand-down","defect"],"text":"false relaunch|relaunched the finished"}}`)

	if err := d.Recur(context.Background(), "d1"); err != nil {
		t.Fatal(err)
	}

	posts := runner.posts()
	if len(posts) != 1 || argAfter(posts[0], "--kind") != "report" || argAfter(posts[0], "--id") != "recurrence:0.7.821:supervisor-finished-lane" {
		t.Fatalf("posts %v", posts)
	}
	if got := argAfter(posts[0], "--text"); got != "RECURRENCE supervisor-finished-lane: 1 in the 24 hours after 0.7.821 installed (defect #283488, fix yasyf/cc-skills#1575): #2" {
		t.Fatalf("text %q", got)
	}
	if !slices.Contains(posts[0], "class=supervisor-finished-lane") || !slices.Contains(posts[0], "recurrences=1") {
		t.Fatalf("flags %v", posts[0])
	}
}

func TestPagingFollowsTheLastSeqUntilAShortPage(t *testing.T) {
	var full strings.Builder
	for seq := range pageRows {
		full.WriteString(recordLine(int64(seq+1), now, "note", "x"))
	}
	pages := []string{full.String(), recordLine(int64(pageRows+1), now, "note", "x")}
	var sinces []string
	runner := runnerFunc(func(c shell.Command) (shell.Result, error) {
		sinces = append(sinces, argAfter(c.Argv, "--since"))
		page := pages[0]
		pages = pages[1:]
		return shell.Result{Stdout: page}, nil
	})

	found, err := Deps{Runner: runner}.paged(context.Background(), "d", "72h")
	if err != nil {
		t.Fatal(err)
	}

	if len(found) != pageRows+1 || !slices.Equal(sinces, []string{"72h", fmt.Sprint(pageRows)}) {
		t.Fatalf("found %d sinces %v", len(found), sinces)
	}
}

type runnerFunc func(shell.Command) (shell.Result, error)

func (f runnerFunc) Run(_ context.Context, c shell.Command) (shell.Result, error) { return f(c) }

func TestNamesLaneMatchesWholeNamesOnly(t *testing.T) {
	cases := []struct {
		text string
		want bool
	}{
		{"SPAWNED fix-1 /a", true},
		{"re fix-1: done", true},
		{"prefix-1 ran", false},
		{"fix-1.5 ran", false},
		{"fix-12 ran", false},
		{"then fix-12 and fix-1", true},
	}
	for _, tc := range cases {
		if got := namesLane(tc.text, "fix-1"); got != tc.want {
			t.Fatalf("%q: got %v", tc.text, got)
		}
	}
}

func TestCaptureWritesAScrubbedFixtureAboutTheNamedLanes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "x.json")
	runner := &fakeRunner{
		records: `{"seq":3,"lane":"fix-1","kind":"note","at":"2026-11-01T20:00:00Z","text":"the customer api is down, stand-down fix-1 now","refs":{"prs":["o/r#7"]},"fields":{"attrs":{"brief":"/Users/x/run/brief.md","secret":"s"}}}` + "\n" +
			`{"seq":4,"lane":"other","kind":"note","at":"2026-11-01T20:00:01Z","text":"unrelated","refs":{},"fields":{}}` + "\n",
		who: `{"lane":"fix-1","live":true,"seen_at":"2026-11-01T20:00:00Z","session":"private"}` + "\n" + `{"lane":"other","live":true,"seen_at":"2026-11-01T20:00:00Z"}` + "\n",
	}
	var out strings.Builder
	d := gateDeps(t, runner, &out)

	err := d.Capture(context.Background(), Capture{Drive: "d", Class: "x", Defect: 1, Fix: "o/r#2", Recurs: "false", Check: "lanes", Lanes: []string{"fix-1"}, From: 1, Upto: noUpperBound, Expect: `{"relaunched":[]}`, Out: path}, &out)
	if err != nil {
		t.Fatal(err)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	for _, leaked := range []string{"customer", "secret", "/Users/x", "private", "unrelated"} {
		if strings.Contains(text, leaked) {
			t.Fatalf("%q leaked into %s", leaked, text)
		}
	}
	var fixture Fixture
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	var given Input
	if err := json.Unmarshal(fixture.Input, &given); err != nil {
		t.Fatal(err)
	}
	if len(given.Records) != 1 || given.Records[0].Text != "stand-down fix-1 now" || given.Records[0].Attr("brief") != "/scratch/run/brief.md" || !slices.Equal(given.Records[0].PRs(), []int{7}) {
		t.Fatalf("records %+v", given.Records)
	}
	if !strings.Contains(out.String(), "with 1 records") {
		t.Fatalf("out %q", out.String())
	}
}
