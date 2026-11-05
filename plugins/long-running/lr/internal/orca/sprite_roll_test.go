package orca

import (
	"bytes"
	"context"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	rollRun   = "run_roll"
	rollTree  = "/home/sprite/monorepo::"
	rollCCX   = "0.91.68"
	rollPack  = "0.7.1199"
	rollTools = `[{"kind":"binary","name":"ccx","url":"https://example.test/ccx.tgz","sha256":"` + testCCISHA + `","version":"` + rollCCX + `"},{"kind":"plugin","name":"long-running@skills","url":"https://example.test/pack.tgz","sha256":"` + testLRSHA + `","version":"` + rollPack + `"}]`
	rollAll   = "installed cci 0.49.36\ninstalled lr 9.9.9\ninstalled ccx " + rollCCX + "\ninstalled long-running@skills " + rollPack + "\n"
)

type rollWorld struct {
	t       *testing.T
	store   *store.Store
	env     map[string]string
	workers []Worker
	replies map[string]shell.Result
	boxes   []string
	out     bytes.Buffer

	probeOnly bool
	host      string
	graders   []string
	argv      map[string][]string
}

func newRollWorld(t *testing.T) *rollWorld {
	root := t.TempDir()
	s, err := store.Open(filepath.Join(root, "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	w := &rollWorld{t: t, store: s, replies: map[string]shell.Result{}, argv: map[string][]string{}}
	f := &workerFixture{t: t}
	f.write(filepath.Join(root, "plugin", "bin", "lr.binrun"), lrPin("linux-x86_64"), 0o700)
	w.env = map[string]string{"WORKER_CCI_URL": testCCIURL, "WORKER_CCI_SHA256": testCCISHA, "BINRUN_PLUGIN_ROOT": filepath.Join(root, "plugin"), "WORKER_TOOLS": rollTools}
	w.workers = []Worker{
		{Dispatch: "ctx_done", Status: statusCompleted, Workspace: rollTree + "done", Terminal: "term_done"},
		{Dispatch: "ctx_c", Workspace: rollTree + "c", Terminal: "term_c"},
		{Dispatch: "ctx_a", Workspace: rollTree + "a", Terminal: "term_a"},
		{Dispatch: "ctx_a2", Workspace: rollTree + "a", Terminal: "term_a2"},
		{Dispatch: "ctx_mac", Workspace: "repo::/Users/me/worktrees/lane", Terminal: "term_mac"},
		{Dispatch: "ctx_unstarted"},
		{Dispatch: "ctx_b", Workspace: rollTree + "b", Terminal: "term_b"},
	}
	return w
}

func (w *rollWorld) Sprites() (map[SpriteKey]Sprite, error) {
	return map[SpriteKey]Sprite{
		{rollTree + "done", "term_done"}: {Workspace: "pool-done"},
		{rollTree + "a", "term_a"}:       {Workspace: "pool-a"},
		{rollTree + "a", "term_a2"}:      {Workspace: "pool-a"},
		{rollTree + "b", "term_b"}:       {Workspace: "pool-b"},
		{rollTree + "c", "term_c"}:       {Workspace: "pool-c"},
	}, nil
}

func (w *rollWorld) Workers(_ context.Context, run string) ([]Worker, error) {
	if run != rollRun {
		w.t.Fatal(run)
	}
	return w.workers, nil
}

func (w *rollWorld) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	box := c.Argv[3]
	w.boxes = append(w.boxes, box)
	w.argv[box] = c.Argv
	if want := (spritePins{}).installArgv(box, nil); !slices.Equal(c.Argv[:10], want[:10]) || c.Argv[len(c.Argv)-1] != "--" {
		w.t.Fatal(c.Argv)
	}
	result, scripted := w.replies[box]
	if !scripted {
		return shell.Result{Stdout: rollAll}, nil
	}
	if result.Code != 0 {
		return result, &shell.ExitError{Argv: c.Argv, Code: result.Code, Stderr: result.Stderr}
	}
	return result, nil
}

func (w *rollWorld) roll(probe string) string {
	w.t.Helper()
	w.out.Reset()
	roller := SpriteRoll{Runner: w, Orca: w, Fleet: w, Store: w.store, Host: w.host, Graders: w.graders, Now: func() time.Time { return time.Date(2026, 11, 3, 13, 0, 0, 0, time.UTC) }, Out: &w.out}
	err := roller.Run(context.Background(), rollRun, func(name string) string { return w.env[name] }, probe, w.probeOnly)
	var failed *Failed
	if errors.As(err, &failed) {
		return failed.Line
	}
	if err != nil {
		w.t.Fatal(err)
	}
	return ""
}

func (w *rollWorld) census() map[string]map[string]string {
	w.t.Helper()
	tools, err := w.store.SpriteTools()
	if err != nil {
		w.t.Fatal(err)
	}
	by := map[string]map[string]string{}
	for _, tool := range tools {
		if by[tool.Sprite] == nil {
			by[tool.Sprite] = map[string]string{}
		}
		by[tool.Sprite][tool.Name] = tool.Version
	}
	return by
}

func TestARollInstallsOnTheDeskHostThoughItHostsNoWorker(t *testing.T) {
	w := newRollWorld(t)
	w.host = "desk-host"
	if refused := w.roll(""); refused != "" {
		t.Fatal(refused)
	}
	if !slices.Equal(w.boxes, []string{"desk-host", "pool-a", "pool-b", "pool-c"}) {
		t.Fatal("the roll did not visit the host and each live Sprite once", w.boxes)
	}
	if census := w.census(); census["desk-host"]["long-running@skills"] != rollPack {
		t.Fatal(census)
	}
}

const rollGraderTools = `[{"kind":"plugin","name":"long-running@skills","url":"https://example.test/pack.tgz","sha256":"` + testLRSHA + `","version":"` + rollPack + `"},{"kind":"binary","name":"ccx","url":"https://example.test/ccx.tgz","sha256":"` + testCCISHA + `","version":"` + rollCCX + `"},{"kind":"raw","name":"ccn","url":"https://example.test/ccn","sha256":"` + testCCISHA + `","version":"0.75.17"},{"kind":"binary","name":"codex-ask","url":"https://example.test/c.tgz","sha256":"` + testCCISHA + `","version":"1.14.29"}]`

func TestARollGivesAGraderTheGraderPinsAndALiveSpriteThatGradesTheWholeSet(t *testing.T) {
	w := newRollWorld(t)
	w.env["WORKER_TOOLS"] = rollGraderTools
	w.graders = []string{"fleet-1", "pool-a"}
	w.replies["fleet-1"] = shell.Result{Stdout: "installed cci 0.49.36\ninstalled lr 9.9.9\ninstalled ccx " + rollCCX + "\ninstalled ccn 0.75.17\ninstalled codex-ask 1.14.29\n"}

	if refused := w.roll(""); refused != "" {
		t.Fatal(refused)
	}

	if !slices.Equal(w.boxes, []string{"fleet-1", "pool-a", "pool-b", "pool-c"}) {
		t.Fatal("the roll did not visit the grader and each live Sprite once", w.boxes)
	}
	for _, tool := range graderTools {
		if !slices.Contains(w.argv["fleet-1"], tool) {
			t.Fatal("the grader went without", tool, w.argv["fleet-1"])
		}
	}
	if slices.Contains(w.argv["fleet-1"], "long-running@skills") {
		t.Fatal("a grader took the pack a live worker runs", w.argv["fleet-1"])
	}
	if !slices.Contains(w.argv["pool-a"], "long-running@skills") {
		t.Fatal("a grader hosting a live worker lost the pack its worker runs", w.argv["pool-a"])
	}
	if census := w.census()["fleet-1"]; census["ccx"] != rollCCX || census["codex-ask"] != "1.14.29" || census["lr"] != "9.9.9" {
		t.Fatal(census)
	}
}

func TestAProbeOnlyRollCanStartOnAGrader(t *testing.T) {
	w := newRollWorld(t)
	w.graders = []string{"fleet-1"}
	w.probeOnly = true
	w.replies["fleet-1"] = shell.Result{Stdout: "installed cci 0.49.36\ninstalled lr 9.9.9\n"}

	if refused := w.roll("fleet-1"); refused != "" {
		t.Fatal(refused)
	}

	if !slices.Equal(w.boxes, []string{"fleet-1"}) {
		t.Fatal(w.boxes)
	}
}

func TestAGraderFailureMarksBadThePinItTriedNotOneItNeverTook(t *testing.T) {
	w := newRollWorld(t)
	w.env["WORKER_TOOLS"] = rollGraderTools
	w.graders = []string{"fleet-1"}
	said := "refused: ccx from that download prints 0.0.1 for --version, not " + rollCCX
	w.replies["fleet-1"] = shell.Result{Code: 1, Stdout: "installed cci 0.49.36\ninstalled lr 9.9.9\n" + said + "\n"}

	refused := w.roll("")

	if want := "lr sprites roll: ccx " + rollCCX + " failed on fleet-1 and is marked bad, so 3 more Sprites keep what they run: " + said; refused != want {
		t.Fatalf("refused\n%s\nwant\n%s", refused, want)
	}
	if !slices.Equal(w.boxes, []string{"fleet-1"}) {
		t.Fatal("the roll went past a failed grader", w.boxes)
	}
	if why, err := w.store.BadTool("long-running@skills", rollPack); err != nil || why != "" {
		t.Fatal("a pin the grader never took was marked bad:", why, err)
	}
}

func TestAFailureOnTheDeskHostMarksNoPinBadAndTheRollGoesOn(t *testing.T) {
	w := newRollWorld(t)
	w.host = "desk-host"
	w.replies["desk-host"] = shell.Result{Code: 1, Stdout: "installed cci 0.49.36\ninstalled lr 9.9.9\n", Stderr: "jq: error: Could not open file known_marketplaces.json\n"}

	refused := w.roll("")

	if want := "lr sprites roll: the desk host desk-host keeps what it runs, and no pin is marked bad: jq: error: Could not open file known_marketplaces.json"; refused != want {
		t.Fatalf("refused\n%s\nwant\n%s", refused, want)
	}
	if !slices.Equal(w.boxes, []string{"desk-host", "pool-a", "pool-b", "pool-c"}) {
		t.Fatal("the host's failure stopped the roll", w.boxes)
	}
	for tool, version := range map[string]string{"ccx": rollCCX, "long-running@skills": rollPack} {
		if why, err := w.store.BadTool(tool, version); err != nil || why != "" {
			t.Fatalf("%s is marked bad: %q, %v", tool, why, err)
		}
	}
}

func TestTheDeskHostGetsOnlyWhatItsServicesRun(t *testing.T) {
	pins := spritePins{tools: []spriteTool{{Name: "orca@skills"}, {Name: "long-running@skills"}, {Name: "ccx"}, {Name: "capt-hookd"}}}

	got := pins.forHost().tools

	if len(got) != 2 || got[0].Name != "long-running@skills" || got[1].Name != "ccx" {
		t.Fatal(got)
	}
	if len(pins.tools) != 4 {
		t.Fatal("the host's list cut the fleet's", pins.tools)
	}
}

func TestRollHostLeavesAHostAlreadyOnThePinsAlone(t *testing.T) {
	w := newRollWorld(t)
	t.Setenv("BINRUN_PLUGIN_ROOT", w.env["BINRUN_PLUGIN_ROOT"])
	runner := filepath.Join(t.TempDir(), "runner.json")
	raw := `{"host":{"sprite":"desk-host"},"orca":{"sprite":{"env":{"WORKER_CCI_URL":"` + testCCIURL + `","WORKER_CCI_SHA256":"` + testCCISHA + `"},"tools":` + rollTools + `}}}`
	if err := os.WriteFile(runner, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	now := func() time.Time { return time.Date(2026, 11, 4, 3, 0, 0, 0, time.UTC) }
	for range 2 {
		if err := RollHost(context.Background(), w, w.store, runner, now, &w.out); err != nil {
			t.Fatal(err)
		}
	}
	if !slices.Equal(w.boxes, []string{"desk-host"}) {
		t.Fatal("a host on the pins was rolled again", w.boxes)
	}
}

func TestRollGradersInstallsCodexAskOnEachGraderSpriteUntilItRunsThePin(t *testing.T) {
	w := newRollWorld(t)
	t.Setenv("BINRUN_PLUGIN_ROOT", w.env["BINRUN_PLUGIN_ROOT"])
	roll := func(tools string) {
		t.Helper()
		runner := filepath.Join(t.TempDir(), "runner.json")
		raw := `{"finder":{"sprite":["fleet-2","fleet-1"]},"fidelity":{"sprite":"fleet-2"},"orca":{"sprite":{"env":{"WORKER_CCI_URL":"` + testCCIURL + `","WORKER_CCI_SHA256":"` + testCCISHA + `"},"tools":` + tools + `}}}`
		if err := os.WriteFile(runner, []byte(raw), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := RollGraders(context.Background(), w, w.store, runner, func() time.Time { return time.Date(2026, 11, 4, 9, 0, 0, 0, time.UTC) }, &w.out); err != nil {
			t.Fatal(err)
		}
	}
	roll(rollTools)
	if len(w.boxes) != 0 {
		t.Fatal("a drive with no codex-ask pin installed on a grader", w.boxes)
	}
	pinned := `[{"kind":"binary","name":"codex-ask","url":"https://example.test/c.tgz","sha256":"` + testCCISHA + `","version":"1.14.29"}]`
	for _, box := range []string{"fleet-1", "fleet-2"} {
		w.replies[box] = shell.Result{Stdout: "installed codex-ask 1.14.29\n"}
	}
	roll(pinned)
	if !slices.Equal(w.boxes, []string{"fleet-1", "fleet-2"}) {
		t.Fatal(w.boxes)
	}
	roll(pinned)
	if len(w.boxes) != 2 {
		t.Fatal("a grader already on the pin installed again", w.boxes)
	}
}

func TestRollHostInstallsOnlyOnTheDeskHost(t *testing.T) {
	w := newRollWorld(t)
	t.Setenv("BINRUN_PLUGIN_ROOT", w.env["BINRUN_PLUGIN_ROOT"])
	roll := func(host string) {
		t.Helper()
		runner := filepath.Join(t.TempDir(), "runner.json")
		raw := `{"host":{"sprite":"` + host + `"},"orca":{"sprite":{"env":{"WORKER_CCI_URL":"` + testCCIURL + `","WORKER_CCI_SHA256":"` + testCCISHA + `"},"tools":` + rollTools + `}}}`
		if err := os.WriteFile(runner, []byte(raw), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := RollHost(context.Background(), w, w.store, runner, func() time.Time { return time.Date(2026, 11, 3, 13, 0, 0, 0, time.UTC) }, &w.out); err != nil {
			t.Fatal(err)
		}
	}
	roll("")
	if len(w.boxes) != 0 {
		t.Fatal("a drive with no desk host installed somewhere", w.boxes)
	}
	roll("desk-host")
	if !slices.Equal(w.boxes, []string{"desk-host"}) {
		t.Fatal(w.boxes)
	}
	if census := w.census(); census["desk-host"]["lr"] != "9.9.9" {
		t.Fatal(census)
	}
}

func TestARollInstallsOnTheProbeSpriteFirstThenEachLiveSpriteOnceAndRecordsWhatEachRuns(t *testing.T) {
	w := newRollWorld(t)
	if refused := w.roll("pool-c"); refused != "" {
		t.Fatal(refused)
	}
	if !slices.Equal(w.boxes, []string{"pool-c", "pool-a", "pool-b"}) {
		t.Fatal("the roll did not start on the probe and visit each live Sprite once", w.boxes)
	}
	want := map[string]string{"cci": "0.49.36", "lr": "9.9.9", "ccx": rollCCX, "long-running@skills": rollPack}
	census := w.census()
	if len(census) != 3 || !maps.Equal(census["pool-a"], want) || !maps.Equal(census["pool-b"], want) || !maps.Equal(census["pool-c"], want) {
		t.Fatal(census)
	}
	w.boxes = nil
	if refused := w.roll(""); refused != "" || !slices.Equal(w.boxes, []string{"pool-a", "pool-b", "pool-c"}) {
		t.Fatal("without a probe the roll did not start on the first Sprite by name", refused, w.boxes)
	}
}

func TestAPinThatFailsOnTheProbeIsMarkedBadAndNeverRolledOnAgain(t *testing.T) {
	w := newRollWorld(t)
	w.replies["pool-b"] = shell.Result{Code: 1, Stdout: "installed cci 0.49.36\ninstalled lr 9.9.9\nrefused: https://example.test/ccx.tgz is not the archive with SHA-256 " + testCCISHA + "\n"}
	refused := w.roll("pool-b")
	if want := "lr sprites roll: ccx " + rollCCX + " failed on pool-b and is marked bad, so 2 more Sprites keep what they run: refused: https://example.test/ccx.tgz is not the archive with SHA-256 " + testCCISHA; refused != want {
		t.Fatalf("refused\n%s\nwant\n%s", refused, want)
	}
	if !slices.Equal(w.boxes, []string{"pool-b"}) {
		t.Fatal("the roll went past a failed probe", w.boxes)
	}
	if census := w.census(); len(census) != 1 || census["pool-b"]["ccx"] != "" || census["pool-b"]["lr"] != "9.9.9" {
		t.Fatal(census)
	}
	delete(w.replies, "pool-b")
	w.boxes = nil
	refused = w.roll("pool-a")
	if want := "nothing rolled: ccx " + rollCCX + " is marked bad, since its install failed on pool-b: refused: https://example.test/ccx.tgz is not the archive with SHA-256 " + testCCISHA + "; pin another version"; refused != want || len(w.boxes) != 0 {
		t.Fatalf("a bad version rolled again: %v\n%s\nwant\n%s", w.boxes, refused, want)
	}
	w.env["WORKER_TOOLS"] = `[{"kind":"binary","name":"ccx","url":"https://example.test/ccx.tgz","sha256":"` + testCCISHA + `","version":"0.91.69"}]`
	if refused := w.roll("pool-a"); refused != "" || len(w.boxes) != 3 {
		t.Fatal("another version of a bad pin did not roll", refused, w.boxes)
	}
}

func TestAFailureBeforeAnyPinMarksNothingBad(t *testing.T) {
	w := newRollWorld(t)
	w.replies["pool-a"] = shell.Result{Code: 1, Stdout: "installed cci 0.49.36\n", Stderr: "curl: (6) Could not resolve host: github.com\n"}
	if refused := w.roll(""); refused != "lr sprites roll: the cci or lr install failed on pool-a, so 2 more Sprites keep what they run: curl: (6) Could not resolve host: github.com" {
		t.Fatal(refused)
	}
	for _, tool := range []string{"ccx", "long-running@skills"} {
		if why, err := w.store.BadTool(tool, map[string]string{"ccx": rollCCX, "long-running@skills": rollPack}[tool]); err != nil || why != "" {
			t.Fatal(tool, why, err)
		}
	}
}

func TestAnInstallThatRunsOutOfTimeMarksNoPinBad(t *testing.T) {
	w := newRollWorld(t)
	w.replies["pool-a"] = shell.Result{Code: shell.CodeTimedOut, Stdout: "installed cci 0.49.36\ninstalled lr 9.9.9\n"}
	if refused := w.roll(""); refused != "lr sprites roll: the install ran out of time on pool-a, so 2 more Sprites keep what they run and no pin is marked bad: installed lr 9.9.9" {
		t.Fatal(refused)
	}
	if why, err := w.store.BadTool("ccx", rollCCX); err != nil || why != "" {
		t.Fatal(why, err)
	}
}

const unreachableSprite = "Error: failed to start sprite command: failed to connect: read tcp [::1]:49823->[::2]:443: i/o timeout\n"

func TestABoxTheRollCannotReachIsPassedOverAndNamedAtTheEnd(t *testing.T) {
	w := newRollWorld(t)
	w.replies["pool-b"] = shell.Result{Code: 1, Stderr: unreachableSprite}
	if refused := w.roll(""); refused != "" {
		t.Fatal(refused)
	}
	if !slices.Equal(w.boxes, []string{"pool-a", "pool-b", "pool-c"}) {
		t.Fatal(w.boxes)
	}
	if census := w.census(); len(census) != 2 || census["pool-c"]["ccx"] != rollCCX {
		t.Fatal(census)
	}
	if !bytes.HasSuffix(w.out.Bytes(), []byte("lr sprites roll: never reached: pool-b\n")) {
		t.Fatal(w.out.String())
	}
	if why, err := w.store.BadTool("ccx", rollCCX); err != nil || why != "" {
		t.Fatal(why, err)
	}
}

func TestABoxTheSpriteAPICannotAnswerForIsPassedOver(t *testing.T) {
	w := newRollWorld(t)
	w.replies["pool-a"] = shell.Result{Code: 1, Stderr: `Error: failed to fetch sprite version: failed to fetch version: Head "https://api.sprites.dev/v1/sprites/pool-a/exec": net/http: request canceled (Client.Timeout exceeded while awaiting headers)` + "\n"}
	if refused := w.roll(""); refused != "" || !slices.Equal(w.boxes, []string{"pool-a", "pool-b", "pool-c"}) {
		t.Fatal(refused, w.boxes)
	}
	if !bytes.HasSuffix(w.out.Bytes(), []byte("lr sprites roll: never reached: pool-a\n")) {
		t.Fatal(w.out.String())
	}
}

func TestABoxThatTimesOutWithNothingInstalledIsPassedOver(t *testing.T) {
	w := newRollWorld(t)
	w.replies["pool-a"] = shell.Result{Code: shell.CodeTimedOut}
	if refused := w.roll(""); refused != "" || !slices.Equal(w.boxes, []string{"pool-a", "pool-b", "pool-c"}) {
		t.Fatal(refused, w.boxes)
	}
}

func TestAProbeTheRollCannotReachStopsItBeforeAnyOtherBox(t *testing.T) {
	w := newRollWorld(t)
	w.replies["pool-b"] = shell.Result{Code: 1, Stderr: unreachableSprite}
	refused := w.roll("pool-b")
	if want := "lr sprites roll: could not reach the probe pool-b, so 2 more Sprites keep what they run: " + strings.TrimSpace(unreachableSprite); refused != want {
		t.Fatalf("refused\n%s\nwant\n%s", refused, want)
	}
	if !slices.Equal(w.boxes, []string{"pool-b"}) {
		t.Fatal(w.boxes)
	}
}

func TestAProbeOnlyRollStopsAfterTheProbeSoItCanBeCheckedBeforeTheRest(t *testing.T) {
	w := newRollWorld(t)
	w.probeOnly = true
	if refused := w.roll("pool-b"); refused != "" {
		t.Fatal(refused)
	}
	if !slices.Equal(w.boxes, []string{"pool-b"}) {
		t.Fatal("a probe-only roll went past the probe", w.boxes)
	}
	if census := w.census(); len(census) != 1 || census["pool-b"]["ccx"] != rollCCX {
		t.Fatal(census)
	}
}

func TestACensusWithNoSpriteSaysSoInsteadOfPrintingNothing(t *testing.T) {
	var out bytes.Buffer
	writeCensus(&out, nil)
	if out.String() != "lr sprites: no launch or roll has recorded a Sprite's tools\n" {
		t.Fatal(out.String())
	}
}

func TestAProbeWithNoLiveWorkerRollsNothing(t *testing.T) {
	w := newRollWorld(t)
	if refused := w.roll("pool-done"); refused != "nothing rolled: pool-done hosts no live worker of Run "+rollRun+" and runner.json names it no grader" || len(w.boxes) != 0 {
		t.Fatal(refused, w.boxes)
	}
}
