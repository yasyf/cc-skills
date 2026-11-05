package orca

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	testBranch   = "yasyf/v3-lane-a-1-1"
	nodeLine     = " node_modules is a shared symlink farm; never run yarn install, ask warm-lane to rebuild for a changed lockfile."
	sparseLine   = " This lane is sparse: widen with `lr sparse add <dir>` before reading outside it."
	deskContract = " Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc. Mail reaches you only between tool calls, so no foreground command runs over five minutes: run a long read in the background and report it by record."
	pluginBin    = "/plugin/bin"
	agentScreen  = "⏵⏵ bypass permissions on (shift+tab to cycle)"
)

type reply struct {
	rc    int
	out   any
	mkdir string
	sh    string
}

func (r reply) body() string {
	switch out := r.out.(type) {
	case nil:
		return ""
	case string:
		return out
	}
	raw, err := json.Marshal(r.out)
	if err != nil {
		panic(err)
	}
	return string(raw)
}

type object = map[string]any

func okResult(result object) reply { return reply{out: object{"ok": true, "result": result}} }

var (
	sent             = reply{out: object{"ok": true, "result": object{}}}
	boundRun         = okResult(object{"run": object{"id": "run_1", "coordinator_handle": "term_coordinator"}})
	timedOut         = reply{rc: 1, out: object{"ok": false, "error": object{"code": "runtime_error", "message": "Timed out waiting for terminal handle after creation"}}}
	idleWait         = okResult(object{"wait": object{"handle": "term_a", "condition": "tui-idle", "satisfied": true, "status": "running", "exitCode": nil}})
	missing          = reply{rc: 1}
	notIdleWait      = reply{rc: 1, out: object{"ok": false, "error": object{"code": "timeout", "message": "timeout"}}}
	startup          = okResult(object{"terminals": []object{{"handle": "term_shell", "agentIdentity": nil}, {"handle": "term_other", "agentIdentity": "claude"}}})
	fencedStart      = reply{rc: 1, out: object{"ok": false, "error": object{"code": refusalFenced, "message": "worker-start requires the coordinator terminal currently bound to the Task Run. Orchestration mutation request ID: req-1."}}}
	unconfigured     = reply{rc: 1, out: object{"ok": false, "error": object{"code": refusalUnconfigured, "message": "Terminal term_a is not running a recognized agent."}}}
	unobserved       = reply{rc: 1, out: object{"ok": true, "result": object{"state": "outcome_unknown", "stage": "turn_start_unobserved", "taskId": "task_a", "dispatchId": "ctx_a"}}}
	readinessTimeout = reply{rc: 1, out: object{"ok": true, "result": object{"state": "failed", "failedStage": "agent_readiness", "taskId": "task_a", "dispatchId": "ctx_a"}}}
)

func listing(agent any, handles ...string) reply {
	if len(handles) == 0 {
		handles = []string{"term_a"}
	}
	terminals := make([]object, 0, 1+len(handles))
	terminals = append(terminals, object{"handle": "term_other", "agentIdentity": "claude"})
	for _, handle := range handles {
		terminals = append(terminals, object{"handle": handle, "agentIdentity": agent})
	}
	return okResult(object{"terminals": terminals})
}

func bareListing() reply {
	return okResult(object{"terminals": []object{{"handle": "term_other", "agentIdentity": "claude"}}})
}

func taskList(ids ...string) reply {
	tasks := make([]object, len(ids))
	for i, id := range ids {
		tasks[i] = object{"id": id}
	}
	return okResult(object{"tasks": tasks})
}

func onScreen(lines ...string) reply {
	return okResult(object{"terminal": object{"source": "screen", "tail": lines}})
}

func workerShown(activity, state string) reply {
	return okResult(object{"worker": object{"state": state}, "projection": object{"stage": object{"activity": activity}}})
}

type harness struct {
	t        *testing.T
	root     string
	env      map[string]string
	parent   string
	brief    string
	main     string
	receipts string

	mu         sync.Mutex
	queues     map[string][]reply
	names      map[string]string
	calls      [][]string
	sleeps     []time.Duration
	onSleep    func()
	elapsed    *time.Duration
	ccxRefuses bool
	ccxQueues  bool
	posted     []client.Draft
	stderr     bytes.Buffer
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", filepath.Join(root, "home"))
	h := &harness{
		t:        t,
		root:     root,
		parent:   filepath.Join(root, "workspaces", "coordinator"),
		brief:    filepath.Join(root, "specs", "lane-a.full.md"),
		main:     filepath.Join(root, "main"),
		receipts: filepath.Join(root, "receipts"),
		queues:   map[string][]reply{},
		names:    map[string]string{},
	}
	h.writeFile(h.brief, "# brief\n")
	if err := os.MkdirAll(h.parent, 0o750); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{".agents/a.md", ".claude/c.md", "tools/t.sh", "go/ci/main.go", "go/other/main.go", "infra/x.tf", "api/app.py", "root.md"} {
		h.writeFile(filepath.Join(h.main, path), path+"\n")
	}
	h.gitIn(h.main, "init", "-q")
	h.gitIn(h.main, "add", "-A")
	h.gitIn(h.main, "commit", "-qm", "base")
	h.gitIn(h.main, "update-ref", "refs/remotes/origin/dev", "HEAD")
	h.env = map[string]string{
		"ORCA_LAUNCH_RUN":          "run_1",
		"ORCA_LAUNCH_REPO":         "repo-1",
		"ORCA_LAUNCH_PARENT":       h.parent,
		"ORCA_LAUNCH_PREFIX":       "v3-",
		"ORCA_LAUNCH_ROOT":         filepath.Dir(h.parent),
		"ORCA_LAUNCH_SPARSE":       "1",
		"ORCA_LAUNCH_SPARSE_FILES": "1",
		"ORCA_LAUNCH_BASE":         "origin/dev",
		"ORCA_LAUNCH_STATE":        h.receipts,
		"ORCA_LAUNCH_CLAUDE_ARGS":  "--channels plugin:cc-review@cc-review",
		"ORCA_TERMINAL_HANDLE":     "term_root",
		"HOME":                     filepath.Join(root, "home"),
		"CODEX_HOME":               filepath.Join(root, "codex"),
		"BINRUN_PLUGIN_ROOT":       "/plugin",
	}
	h.reply("repo show", h.repo(nil))
	return h
}

func newFullHarness(t *testing.T) *harness {
	t.Helper()
	h := newHarness(t)
	delete(h.env, "ORCA_LAUNCH_SPARSE")
	return h
}

func (h *harness) writeFile(path, text string) {
	h.t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		h.t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		h.t.Fatal(err)
	}
}

func (h *harness) gitIn(dir string, args ...string) string {
	h.t.Helper()
	cmd := exec.Command("git", slices.Concat([]string{"-C", dir, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"}, args)...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		h.t.Fatalf("git %v: %v: %s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func (h *harness) gitFails(dir string, args ...string) bool {
	return exec.Command("git", append([]string{"-C", dir}, args...)...).Run() != nil
}

func (h *harness) repo(fields object) reply {
	repo := object{"id": "repo-1", "path": h.main, "gitUsername": "yasyf", "symlinkPaths": []string{}}
	for key, value := range fields {
		repo[key] = value
	}
	return okResult(object{"repo": repo})
}

func (h *harness) worktree() string {
	return filepath.Join(filepath.Dir(h.parent), "v3-lane-a-1-1")
}

func (h *harness) registeredReply() reply {
	return okResult(object{"worktree": object{"path": h.worktree()}})
}

func (h *harness) tickOnSleep() {
	h.elapsed = new(time.Duration)
}

func (h *harness) reply(command string, replies ...reply) {
	h.queues[strings.ReplaceAll(command, " ", "_")] = replies
}

type healthyOptions struct {
	state  string
	screen string
	agent  any
}

func (h *harness) healthy(options healthyOptions) {
	state, scr, agent := options.state, options.screen, options.agent
	if state == "" {
		state = "ready"
	}
	if scr == "" {
		scr = agentScreen
	}
	h.reply("orchestration run-current", boundRun)
	h.reply("worktree set", sent)
	h.reply("worktree create", reply{out: object{"ok": true, "result": object{"worktree": object{"path": h.worktree()}}}, mkdir: h.worktree()})
	h.reply("worktree rm", sent)
	h.reply("terminal create", okResult(object{"terminal": object{"handle": "term_a"}}))
	h.reply("orchestration worker-start", okResult(object{"state": state, "taskId": "task_a", "dispatchId": "ctx_a"}))
	h.reply("terminal read", okResult(object{"terminal": object{"tail": []string{"❯", scr}}}))
	h.reply("terminal list", listing(agent))
	if agent == nil {
		h.reply("terminal wait", notIdleWait)
	} else {
		h.reply("terminal wait", idleWait)
	}
	h.reply("terminal close", sent)
}

func (h *harness) healthyClaude() { h.healthy(healthyOptions{agent: agentClaude}) }

func (h *harness) healthyCodex() { h.healthy(healthyOptions{agent: agentCodex}) }

func (h *harness) noAgent() { h.healthy(healthyOptions{}) }

func (h *harness) checkOut() {
	h.t.Helper()
	wt := h.worktree()
	h.writeFile(filepath.Join(wt, "file"), "x\n")
	h.gitIn(wt, "init", "-q")
	h.gitIn(wt, "add", "file")
	h.gitIn(wt, "commit", "-qm", "base")
	h.writeFile(filepath.Join(h.receipts, "lane-a.worktree"), wt+"\n")
}

func (h *harness) made(emptyIndex bool) string {
	wt := h.worktree()
	commit := "git -C " + wt + " -c user.name=t -c user.email=t@t"
	steps := []string{"git init -q " + wt, "echo x > " + wt + "/file", commit + " add file", commit + " commit -qm base"}
	if emptyIndex {
		steps = append(steps, "git -C "+wt+" read-tree --empty")
	}
	return strings.Join(steps, " && ")
}

func (h *harness) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	switch c.Argv[0] {
	case "orca":
		return h.orcaTool(c)
	case "ccx":
		return h.ccxTool(ctx, c)
	}
	return shell.Exec{}.Run(ctx, c)
}

func (h *harness) orcaTool(c shell.Command) (shell.Result, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	args := c.Argv[1:]
	h.calls = append(h.calls, slices.Clone(args))
	key := strings.Join(args[:2], "_")
	queue, queued := h.queues[key]
	if key == "worktree_show" && !queued {
		return h.defaultShow(args)
	}
	if !queued {
		queue = []reply{{rc: 1}}
	}
	current := queue[0]
	if len(queue) > 1 {
		h.queues[key] = queue[1:]
	}
	if current.mkdir != "" {
		if key == "worktree_create" {
			h.names[flag(args, "--name")] = current.mkdir
		}
		if err := os.MkdirAll(current.mkdir, 0o750); err != nil {
			return shell.Result{}, err
		}
		if _, err := os.Stat(filepath.Join(current.mkdir, ".git")); err != nil {
			if err := exec.Command("git", "init", "-q", current.mkdir).Run(); err != nil {
				return shell.Result{}, err
			}
			if err := exec.Command("git", "-C", current.mkdir, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base").Run(); err != nil {
				return shell.Result{}, err
			}
		}
	}
	if current.sh != "" {
		if out, err := exec.Command("sh", "-c", current.sh).CombinedOutput(); err != nil {
			return shell.Result{}, fmt.Errorf("%w: %s", err, out)
		}
	}
	result := shell.Result{Code: current.rc, Stdout: current.body()}
	if current.rc != 0 {
		return result, &shell.ExitError{Argv: c.Argv, Code: current.rc}
	}
	return result, nil
}

func (h *harness) defaultShow(args []string) (shell.Result, error) {
	selector := flag(args, "--worktree")
	path, named := strings.CutPrefix(selector, "name:")
	if named {
		if known, ok := h.names[path]; ok {
			path = known
		} else {
			path = filepath.Join(filepath.Dir(h.parent), path)
		}
	} else {
		path = strings.TrimPrefix(selector, "path:")
	}
	if !isDir(path) {
		return shell.Result{Code: 1}, &shell.ExitError{Argv: args, Code: 1}
	}
	return shell.Result{Stdout: okResult(object{"worktree": object{"path": path}}).body()}, nil
}

func (h *harness) ccxTool(ctx context.Context, c shell.Command) (shell.Result, error) {
	h.mu.Lock()
	h.calls = append(h.calls, slices.Clone(c.Argv))
	refuses, queues := h.ccxRefuses, h.ccxQueues
	h.mu.Unlock()
	if refuses {
		return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1}
	}
	if queues {
		return shell.Result{Stdout: "queued the daemon claims it when sessions are quiet\n"}, nil
	}
	return shell.Exec{}.Run(ctx, shell.Command{Argv: []string{"git", "worktree", "remove", "--force", flag(c.Argv, "--path")}, Dir: c.Dir})
}

func (h *harness) Post(_ context.Context, d client.Draft) (client.Record, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.posted = append(h.posted, d)
	return client.Record{}, nil
}

func (h *harness) Tail(context.Context, client.Query) ([]client.Record, error) {
	return nil, nil
}

func flag(argv []string, name string) string {
	at := slices.Index(argv, name)
	if at < 0 || at+1 >= len(argv) {
		return ""
	}
	return argv[at+1]
}

func (h *harness) orcaCalls(command string) [][]string {
	h.mu.Lock()
	defer h.mu.Unlock()
	var found [][]string
	for _, call := range h.calls {
		if strings.Join(call[:2], " ") == command {
			found = append(found, call)
		}
	}
	return found
}

func (h *harness) steps() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	steps := make([]string, len(h.calls))
	for i, call := range h.calls {
		steps[i] = strings.Join(call[:2], " ")
	}
	return steps
}

func (h *harness) launchAs(lane, model, effort, brief string) (string, int) {
	h.t.Helper()
	starter := &Starter{
		Runner: h,
		Bus:    h,
		Env:    func(name string) string { return h.env[name] },
		Sleep: func(d time.Duration) {
			h.mu.Lock()
			h.sleeps = append(h.sleeps, d)
			if h.elapsed != nil {
				*h.elapsed += d
			}
			hook := h.onSleep
			h.onSleep = nil
			h.mu.Unlock()
			if hook != nil {
				hook()
			}
		},
		Now: func() time.Time {
			h.mu.Lock()
			defer h.mu.Unlock()
			if h.elapsed == nil {
				return time.Now()
			}
			return time.Unix(fakeNow, 0).Add(*h.elapsed)
		},
		Stderr: &h.stderr,
	}
	line, err := starter.Run(context.Background(), lane, model, effort, brief)
	var failed *Failed
	if errors.As(err, &failed) {
		return failed.Line, 1
	}
	if err != nil {
		h.t.Fatal(err)
	}
	return line, 0
}

func (h *harness) launch() (string, int) {
	h.t.Helper()
	return h.launchAs("lane-a", "opus", "high", h.brief)
}

func (h *harness) launched() string {
	h.t.Helper()
	line, code := h.launch()
	if code != 0 {
		h.t.Fatalf("launch failed: %s", line)
	}
	return line
}

func (h *harness) launchedAs(model, effort string) string {
	h.t.Helper()
	line, code := h.launchAs("lane-a", model, effort, h.brief)
	if code != 0 {
		h.t.Fatalf("launch failed: %s", line)
	}
	return line
}

func (h *harness) spec() string {
	h.t.Helper()
	starts := h.orcaCalls("orchestration worker-start")
	if len(starts) == 0 {
		h.t.Fatal("no worker-start")
	}
	return flag(starts[0], "--spec")
}

func (h *harness) readReceipt(name string) string {
	h.t.Helper()
	raw, err := os.ReadFile(filepath.Join(h.receipts, name))
	if err != nil {
		h.t.Fatal(err)
	}
	return string(raw)
}

func wantLine(t *testing.T, got, want string) {
	t.Helper()
	if got != want {
		t.Fatalf("got\n%s\nwant\n%s", got, want)
	}
}

func wantNoCalls(t *testing.T, h *harness, commands ...string) {
	t.Helper()
	for _, command := range commands {
		if calls := h.orcaCalls(command); len(calls) != 0 {
			t.Errorf("%s ran: %v", command, calls)
		}
	}
}

func readyLine(h *harness) string {
	return "lane-a ready task=task_a dispatch=ctx_a terminal=term_a worktree=" + h.worktree()
}

func bindDrive(h *harness, body object, name string) {
	h.t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		h.t.Fatal(err)
	}
	h.writeFile(filepath.Join(h.root, "home", ".claude", "long-running", "drives", name+".json"), string(raw))
}

func TestAWorkerLaunchedInsideADriveCarriesTheDriveToClaude(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env[driveEnv] = "900424b6"
	h.launched()
	command := flag(h.orcaCalls("terminal create")[0], "--command")
	if !strings.HasPrefix(command, "env CLAUDE_LONG_RUNNING_LANE=lane-a CLAUDE_LONG_RUNNING_DRIVE=900424b6 "+claudeLaneEnv+" "+laneCompact+" claude ") {
		t.Fatal(command)
	}
}

func TestALaneMergesItsLocalSettingsAfterTheStrippedOnes(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env[laneSettingsEnv] = `{"enabledPlugins":{"a@b":false}}`
	h.launched()
	command := flag(h.orcaCalls("terminal create")[0], "--command")
	if !strings.HasSuffix(command, " --settings '{"+claudeLaneSettings+`,"enabledPlugins":{"a@b":false}}'`) {
		t.Fatal(command)
	}
}

func TestOnlyTheCoordinatorLaunchesOnTheLongCacheUnderALowCompactionCeiling(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	if line, code := h.launchAs(coordinatorLane, "opus", "high", h.brief); code != 0 {
		t.Fatal(line)
	}
	h.launched()
	creates := h.orcaCalls("terminal create")
	command := flag(creates[0], "--command")
	if !strings.HasPrefix(command, "env CLAUDE_LONG_RUNNING_LANE=root "+claudeLaneEnv+" CLAUDE_CODE_PROMPT_CACHE_TTL=1h CLAUDE_CODE_AUTO_COMPACT_WINDOW=333000 claude ") {
		t.Fatal(command)
	}
	for _, want := range []string{"--disallowedTools " + laneDeniedTools + coordinatorDenied + " ", `,"skillListingBudgetFraction":0.001}'`} {
		if !strings.Contains(command, want) {
			t.Fatalf("%q misses %q", command, want)
		}
	}
	if strings.Contains(command, "claudeMdExcludes") || strings.Contains(command, "--append-system-prompt-file") {
		t.Fatalf("a drive that names no rules kept its instructions out: %q", command)
	}
	other := flag(creates[1], "--command")
	if !strings.HasPrefix(other, "env CLAUDE_LONG_RUNNING_LANE=lane-a "+claudeLaneEnv+" CLAUDE_CODE_AUTO_COMPACT_WINDOW=333000 claude ") || strings.Contains(other, coordinatorDenied) || strings.Contains(other, coordinatorListing) {
		t.Fatal(other)
	}
}

func TestACoordinatorWithNamedRulesLoadsThoseSectionsInsteadOfItsInstructionFiles(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.writeFile(filepath.Join(h.root, "home", ".claude", "CLAUDE.md"), "# Title\n\n## Style\n\nNo comments.\n\n## Routing\n\nUse sol.\n\n### Deep\n\nStill routing.\n\n## Judgment\n\nThink.\n")
	dir := filepath.Dir(h.brief)
	h.writeFile(filepath.Join(dir, coordinatorSections), "Judgment\nRouting\n")
	if line, code := h.launchAs(coordinatorLane, "opus", "high", h.brief); code != 0 {
		t.Fatal(line)
	}
	command := flag(h.orcaCalls("terminal create")[0], "--command")
	rules := filepath.Join(dir, coordinatorRules)
	for _, want := range []string{`"autoMemoryEnabled":false`, `"claudeMdExcludes":["**/CLAUDE.md","**/AGENTS.md"]`, "--append-system-prompt-file " + rules + " "} {
		if !strings.Contains(command, want) {
			t.Fatalf("%q misses %q", command, want)
		}
	}
	got, err := os.ReadFile(rules)
	if err != nil {
		t.Fatal(err)
	}
	if want := "## Judgment\n\nThink.\n## Routing\n\nUse sol.\n\n### Deep\n\nStill routing.\n\n"; string(got) != want {
		t.Fatalf("rules %q, want %q", got, want)
	}
}

func TestARuleSectionTheInstructionsNoLongerHaveRefusesTheLaunch(t *testing.T) {
	if _, err := sectionsOf("## Style\n", []string{"Routing"}); err == nil || !strings.Contains(err.Error(), `"Routing"`) {
		t.Fatalf("got %v, want the missing section named", err)
	}
}

func TestAnIncidentLaneLaunchedInsideADriveCarriesTheDriveToCodex(t *testing.T) {
	h := newHarness(t)
	h.healthyCodex()
	h.env[driveEnv] = "900424b6"
	h.launchedAs(agentIncident, "xhigh")
	command := flag(h.orcaCalls("terminal create")[0], "--command")
	if !strings.HasPrefix(command, "sh -c 'PATH="+pluginBin+":$PATH exec env CLAUDE_LONG_RUNNING_LANE=lane-a CLAUDE_LONG_RUNNING_DRIVE=900424b6 codex ") {
		t.Fatal(command)
	}
}

func TestAWorkerLaunchedByASessionlessRunnerCarriesTheDriveOfItsOrcaRun(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	bindDrive(h, object{"drive": "900424b6", "sessions": []string{}, "orca_run": "run_1"}, "900424b6")
	h.launched()
	command := flag(h.orcaCalls("terminal create")[0], "--command")
	if !strings.HasPrefix(command, "env CLAUDE_LONG_RUNNING_LANE=lane-a CLAUDE_LONG_RUNNING_DRIVE=900424b6 "+claudeLaneEnv+" "+laneCompact+" claude ") {
		t.Fatal(command)
	}
}

func TestAReadyLaunchInADrivePostsTheSpawnedRecordNamingItsLaneAndBrief(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	bindDrive(h, object{"drive": "900424b6", "sessions": []string{}, "orca_run": "run_1", "cci_drive": "brook"}, "900424b6")
	h.env[driveEnv] = "900424b6"
	h.env["CLAUDE_LONG_RUNNING_LANE"] = "orca-desk"
	h.launched()
	want := []client.Draft{{
		Drive: "brook", Lane: "orca-desk", Kind: "state", Topic: "spawn:lane-a", LaneRefs: []string{"lane-a"},
		Attrs: map[string]string{"brief": h.brief, "model": "opus", "effort": "high", "placement": "orca"},
		Text:  "SPAWNED lane-a " + h.brief,
	}}
	if !reflect.DeepEqual(h.posted, want) {
		t.Fatalf("got %+v", h.posted)
	}
}

func TestALaunchWithoutARunIDLaunchesIntoItsDrivesOrcaRun(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	bindDrive(h, object{"drive": "900424b6", "sessions": []string{"session-root"}, "orca_run": "run_1"}, "900424b6")
	h.env[sessionEnv] = "session-root"
	delete(h.env, "ORCA_LAUNCH_RUN")
	h.launched()
	if got := flag(h.orcaCalls("orchestration worker-start")[0], "--run"); got != "run_1" {
		t.Fatal(got)
	}
	h.readReceipt("lane-a.json")
}

func TestAnExplicitRunIDWinsOverTheDrivesOrcaRun(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	bindDrive(h, object{"drive": "900424b6", "sessions": []string{"session-root"}, "orca_run": "run_other"}, "900424b6")
	h.env[sessionEnv] = "session-root"
	h.launched()
	if got := flag(h.orcaCalls("orchestration worker-start")[0], "--run"); got != "run_1" {
		t.Fatal(got)
	}
}

func TestALaunchOutsideAnyDriveWithoutARunIDFailsBeforeTouchingOrca(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	delete(h.env, "ORCA_LAUNCH_RUN")
	line, code := h.launch()
	if code != 1 || !strings.HasPrefix(line, "lane-a failed run: this session's drive binds no Orca run;") {
		t.Fatal(line)
	}
	if len(h.calls) != 0 {
		t.Fatal(h.calls)
	}
}

func (h *harness) mainCheckout() {
	h.t.Helper()
	if err := os.Remove(h.parent); err != nil {
		h.t.Fatal(err)
	}
	h.gitIn(h.main, "worktree", "add", "-q", "--detach", h.parent)
}

func TestALaunchWithoutARepoIDUsesTheOrcaRepoAtTheParentsMainCheckout(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.mainCheckout()
	delete(h.env, "ORCA_LAUNCH_REPO")
	h.launched()
	if got := flag(h.orcaCalls("repo show")[0], "--repo"); got != "path:"+h.main {
		t.Fatal(got)
	}
}

func TestAnExplicitRepoIDNamesTheRepoByID(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launched()
	if got := flag(h.orcaCalls("repo show")[0], "--repo"); got != "id:repo-1" {
		t.Fatal(got)
	}
}

func TestAMainCheckoutOrcaDoesNotKnowFailsNamingItsPath(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.mainCheckout()
	h.reply("repo show", reply{rc: 1, out: object{"ok": false, "error": object{"code": CodeUnregistered, "message": CodeUnregistered}}})
	delete(h.env, "ORCA_LAUNCH_REPO")
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed repo: orca repo show finds no repo at path:"+h.main+" (selector_not_found: selector_not_found); set ORCA_LAUNCH_REPO, or register the main checkout with orca repo add --path")
	wantNoCalls(t, h, "worktree set")
}

func TestLaunchCreatesAChildWorktreeAndABypassTerminal(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	wantLine(t, h.launched(), readyLine(h))
	wt := h.worktree()
	sets := h.orcaCalls("worktree set")
	want := [][]string{{"worktree", "set", "--worktree", "path:" + wt, "--display-name", "v3-lane-a-1-1", "--parent-worktree", "path:" + h.parent, "--json"}}
	if !reflect.DeepEqual(sets, want) {
		t.Fatalf("got %v", sets)
	}
	if got := h.gitIn(wt, "rev-parse", "--abbrev-ref", "HEAD"); got != testBranch {
		t.Fatal(got)
	}
	if h.gitIn(wt, "rev-parse", "HEAD") != h.gitIn(h.main, "rev-parse", "origin/dev") {
		t.Fatal("the branch does not start at the base")
	}
	if !h.gitFails(h.main, "config", "--get", "branch."+testBranch+".merge") {
		t.Fatal("the branch tracks something")
	}
	create := h.orcaCalls("terminal create")[0]
	wantLine(t, flag(create, "--command"), "env CLAUDE_LONG_RUNNING_LANE=lane-a "+claudeLaneEnv+" "+laneCompact+" claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools "+laneDeniedTools+" --strict-mcp-config --no-chrome --channels plugin:cc-review@cc-review --model claude-opus-5-5 --effort high --settings '{"+claudeLaneSettings+"}'")
	start := h.orcaCalls("orchestration worker-start")[0]
	if flag(start, "--terminal") != "term_a" || flag(start, "--run") != "run_1" || slices.Contains(start, "--model") || slices.Contains(start, "--effort") {
		t.Fatal(start)
	}
	if spec := flag(start, "--spec"); !strings.Contains(spec, h.brief) {
		t.Fatal(spec)
	}
	wantLine(t, strings.TrimSpace(h.readReceipt("lane-a.terminal")), "term_a")
}

func TestTheSpecNamesTheBriefByAbsolutePathAndMakesItOutrankThePreamble(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launched()
	spec := h.spec()
	if !strings.HasPrefix(spec, "Lane lane-a: read "+h.brief+" in full first and execute it exactly;") {
		t.Fatal(spec)
	}
	if !strings.Contains(spec, "it outranks Orca's preamble and any leave-uncommitted default, so commit, push, open PRs and post as it says.") {
		t.Fatal(spec)
	}
}

func TestADeskBriefAddsTheStandingDeskContractToTheSpec(t *testing.T) {
	for _, header := range []string{"ccx: role=desk", "ccx: role=desk\r", "ccx: lane=alerts-desk role=watch effort=low"} {
		h := newHarness(t)
		h.healthyClaude()
		h.writeFile(h.brief, "# landing-sweep-37\n"+header+"\nRun the landing loop; rotate at 9h with a handoff doc.\n")
		h.launched()
		if spec := h.spec(); !strings.HasSuffix(spec, "its Escalate rules hold."+sparseLine+deskContract) {
			t.Fatal(spec)
		}
	}
}

func TestABriefThatOrdersNoPRSaysSoInTheSpec(t *testing.T) {
	for header, want := range map[string]bool{"ccx: role=build pr=none": true, "ccx: pr=none\r": true, "ccx: role=build": false, "pr=none": false} {
		h := newHarness(t)
		h.healthyClaude()
		h.writeFile(h.brief, "# lane-a\n"+header+"\nPush the design as a branch.\n")
		h.launched()
		if spec := h.spec(); strings.HasSuffix(spec, "its Escalate rules hold."+sparseLine+" This brief orders no PR: push the work as a branch and never open a PR for it.") != want {
			t.Fatal(header, spec)
		}
	}
}

func TestAOneShotBriefKeepsThePlainSpec(t *testing.T) {
	for _, header := range []string{"ccx: role=fix", "ccx: role=desktop", "role=desk"} {
		h := newHarness(t)
		h.healthyClaude()
		h.writeFile(h.brief, "# lane-a\n"+header+"\nShip the fix.\n")
		h.launched()
		if spec := h.spec(); !strings.HasSuffix(spec, "its Escalate rules hold."+sparseLine) {
			t.Fatal(spec)
		}
	}
}

func TestANewWorktreeHoldsTheFilesTheReleaseVerbsRead(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	read := []string{"release/targets.yaml", "infra/envs/plat.yaml", "infra/rows/ci-infra/ci/escape-hatch.json"}
	for _, path := range append(slices.Clone(read), "infra/rows/api/index.ts") {
		h.writeFile(filepath.Join(h.main, path), path+"\n")
	}
	h.gitIn(h.main, "add", "-A")
	h.gitIn(h.main, "commit", "-qm", "release inputs")
	h.gitIn(h.main, "update-ref", "refs/remotes/origin/dev", "HEAD")
	h.writeFile(h.brief, "# lane-a\nccx: role=fix\nShip the fix.\n")
	h.launched()
	wt := h.worktree()
	for _, path := range read {
		if !exists(filepath.Join(wt, path)) {
			t.Fatalf("%s is outside the cone", path)
		}
	}
	if exists(filepath.Join(wt, "infra", "rows", "api")) {
		t.Fatal("the cone holds more of infra/rows than the release verbs read")
	}
}

func TestANewWorktreeChecksOutOnlyTheRootFilesTheAgentDirsAndTheBriefsPaths(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.writeFile(h.brief, "# lane-a\nccx: role=fix paths=go/ci,infra\nShip the fix.\n")
	h.launched()
	wt := h.worktree()
	entries, err := os.ReadDir(wt)
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	if want := []string{".agents", ".claude", ".git", "go", "infra", "root.md", "tools"}; !slices.Equal(names, want) {
		t.Fatalf("got %v", names)
	}
	if exists(filepath.Join(wt, "go", "other")) || !exists(filepath.Join(wt, "go", "ci")) {
		t.Fatal("the cone is wrong")
	}
	if got := h.gitIn(wt, "status", "--porcelain"); got != "" {
		t.Fatal(got)
	}
	if want := "lane-a sparse: .agents .claude tools infra/ci/hooks/lib infra/ci/src release infra/envs infra/rows/ci-infra/ci go/ci infra; widen with cd " + wt + " && lr sparse add <dir>"; !strings.Contains(h.stderr.String(), want) {
		t.Fatal(h.stderr.String())
	}
	if !strings.HasSuffix(h.spec(), sparseLine) {
		t.Fatal(h.spec())
	}
}

func TestABriefNamingAPackageChecksOutItsWholeGoModuleAndLocalReplaceTargets(t *testing.T) {
	h := newHarness(t)
	h.writeFile(filepath.Join(h.main, "go", "app", "cmd", "x", "main.go"), "package main\n")
	h.writeFile(filepath.Join(h.main, "go", "app", "internal", "y", "y.go"), "package y\n")
	h.writeFile(filepath.Join(h.main, "go", "app", "go.mod"), "module app\n\nreplace lib => ../lib\n")
	h.writeFile(filepath.Join(h.main, "go", "lib", "go.mod"), "module lib\n\nreplace deep => ../deep\n")
	h.writeFile(filepath.Join(h.main, "go", "deep", "go.mod"), "module deep\n")
	h.gitIn(h.main, "add", "-A")
	h.gitIn(h.main, "commit", "-qm", "modules")
	h.gitIn(h.main, "update-ref", "refs/remotes/origin/dev", "HEAD")
	h.healthyClaude()
	h.writeFile(h.brief, "# lane-a\nccx: role=fix paths=go/app/cmd/x\nShip the fix.\n")
	h.launched()
	wt := h.worktree()
	for _, module := range []string{"app", "lib", "deep"} {
		if !exists(filepath.Join(wt, "go", module, "go.mod")) {
			t.Fatalf("go/%s is not checked out", module)
		}
	}
	if !exists(filepath.Join(wt, "go", "app", "internal", "y", "y.go")) {
		t.Fatal("a package beside the named directory is not checked out")
	}
	if exists(filepath.Join(wt, "go", "other")) {
		t.Fatal("the cone took a module nothing replaces")
	}
}

func TestASparseLaunchWithoutARootAddsTheWorktreeToTheCCXPoolElseBesideItsParent(t *testing.T) {
	for _, pool := range []string{"", "pool"} {
		h := newHarness(t)
		h.healthyClaude()
		delete(h.env, "ORCA_LAUNCH_ROOT")
		root := filepath.Dir(h.parent)
		if pool != "" {
			h.gitIn(h.main, "config", "ccx.worktreeRoot", filepath.Join(h.root, pool))
			root = filepath.Join(h.root, pool, "main")
		}
		if line := h.launched(); !strings.HasSuffix(line, "worktree="+filepath.Join(root, "v3-lane-a-1-1")) {
			t.Fatal(line)
		}
		if !isDir(filepath.Join(root, "v3-lane-a-1-1", ".agents")) {
			t.Fatal("no checkout in the pool")
		}
	}
}

func TestARepoWithNoMoreTrackedFilesThanTheSparseFloorKeepsAFullCheckout(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_SPARSE_FILES"] = "8"
	h.launched()
	wt := h.worktree()
	if !exists(filepath.Join(wt, "api", "app.py")) || !exists(filepath.Join(wt, "go", "other", "main.go")) {
		t.Fatal("not a full checkout")
	}
	if !h.gitFails(wt, "config", "--get", "core.sparseCheckout") || strings.Contains(h.stderr.String(), "sparse:") {
		t.Fatal("sparse")
	}
	if !strings.HasSuffix(h.spec(), "its Escalate rules hold.") {
		t.Fatal(h.spec())
	}
}

func TestARelaunchIntoAFullCheckoutKeepsThePlainSpec(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.checkOut()
	h.launched()
	if !strings.HasSuffix(h.spec(), "its Escalate rules hold.") {
		t.Fatal(h.spec())
	}
}

func TestANameAlreadyCutIsNeverCutAgain(t *testing.T) {
	for _, taken := range []string{"branch", "path", "receipt"} {
		t.Run(taken, func(t *testing.T) {
			h := newHarness(t)
			h.healthyClaude()
			switch taken {
			case "branch":
				h.gitIn(h.main, "branch", testBranch, "origin/dev")
			case "path":
				h.writeFile(filepath.Join(h.worktree(), "stray"), "x\n")
			default:
				h.writeFile(filepath.Join(h.receipts, "lane-a.names"), "v3-lane-a-1-1\n")
			}
			fresh := filepath.Join(filepath.Dir(h.parent), "v3-lane-a-1-2")
			if line := h.launched(); !strings.HasSuffix(line, "worktree="+fresh) {
				t.Fatal(line)
			}
			if got := h.gitIn(fresh, "rev-parse", "--abbrev-ref", "HEAD"); got != "yasyf/v3-lane-a-1-2" {
				t.Fatal(got)
			}
			if names := strings.Split(strings.TrimSpace(h.readReceipt("lane-a.names")), "\n"); names[len(names)-1] != "v3-lane-a-1-2" {
				t.Fatal(names)
			}
		})
	}
}

func TestARelaunchAfterARollbackCutsTheNextName(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	if _, code := h.launch(); code != 1 {
		t.Fatal("the first launch succeeded")
	}
	if exists(h.worktree()) {
		t.Fatal("the worktree survived the rollback")
	}
	h.healthyClaude()
	delete(h.env, "ORCA_LAUNCH_BOOT_SECONDS")
	if line := h.launched(); !strings.HasSuffix(line, "worktree="+filepath.Join(filepath.Dir(h.parent), "v3-lane-a-1-2")) {
		t.Fatal(line)
	}
}

func TestALaunchLinksNodeModulesThroughTheWarmLaneHookBeforeItsWorkerStarts(t *testing.T) {
	h := newHarness(t)
	h.writeFile(filepath.Join(h.main, "tools", "hooks", "warm-lane.sh"), "#!/bin/sh\ntouch linked\n")
	h.gitIn(h.main, "add", "--chmod=+x", "tools/hooks/warm-lane.sh")
	h.gitIn(h.main, "commit", "-qm", "warm-lane")
	h.gitIn(h.main, "update-ref", "refs/remotes/origin/dev", "HEAD")
	h.healthyClaude()
	h.launched()
	if !exists(filepath.Join(h.worktree(), "linked")) {
		t.Fatal("the launch did not run the warm-lane hook")
	}
}

func TestAWorktreeWithTheWarmLaneHookTellsTheWorkerNodeModulesIsShared(t *testing.T) {
	h := newHarness(t)
	h.writeFile(filepath.Join(h.main, "tools", "hooks", "warm-lane.sh"), "#!/bin/sh\n")
	h.gitIn(h.main, "add", "tools/hooks/warm-lane.sh")
	h.gitIn(h.main, "commit", "-qm", "warm-lane")
	h.gitIn(h.main, "update-ref", "refs/remotes/origin/dev", "HEAD")
	h.healthyClaude()
	h.launched()
	if !strings.HasSuffix(h.spec(), "its Escalate rules hold."+nodeLine+sparseLine) {
		t.Fatal(h.spec())
	}
}

func TestANewWorktreeLinksTheReposSymlinkPathsAndCopiesItsWorktreeincludePaths(t *testing.T) {
	h := newHarness(t)
	h.writeFile(filepath.Join(h.main, ".gitignore"), "secrets.yaml\n.env\n")
	h.writeFile(filepath.Join(h.main, ".worktreeinclude"), "# copied\n./api/.env\ninfra/secrets.yaml\napi/app.py\nmissing/.env\n")
	h.gitIn(h.main, "add", ".gitignore", ".worktreeinclude")
	h.gitIn(h.main, "commit", "-qm", "include")
	h.gitIn(h.main, "update-ref", "refs/remotes/origin/dev", "HEAD")
	h.writeFile(filepath.Join(h.main, "infra", "secrets.yaml"), "secret\n")
	h.writeFile(filepath.Join(h.main, "api", ".env"), "TOKEN=1\n")
	h.healthyClaude()
	h.reply("repo show", h.repo(object{"symlinkPaths": []string{"infra/secrets.yaml"}}))
	h.launched()
	wt := h.worktree()
	linked, err := os.Readlink(filepath.Join(wt, "infra", "secrets.yaml"))
	if err != nil || linked != filepath.Join(h.main, "infra", "secrets.yaml") {
		t.Fatalf("link %q, err %v", linked, err)
	}
	info, err := os.Lstat(filepath.Join(wt, "api", ".env"))
	if err != nil || !info.Mode().IsRegular() {
		t.Fatalf("copy %v, err %v", info, err)
	}
	if exists(filepath.Join(wt, "api", "app.py")) || exists(filepath.Join(wt, "missing")) {
		t.Fatal("copied a path it should not")
	}
}

func effectStart(terminal string) reply {
	return okResult(object{"state": "ready", "taskId": "task_a", "dispatchId": "ctx_a", "effects": []object{{"kind": "terminal", "role": "agent", "id": terminal}}})
}

func TestACodexLaneStartsSolOnTheCodexAgentWithoutACustomTerminal(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	wantLine(t, h.launchedAs(agentCodex, "high"), "lane-a ready task=task_a dispatch=ctx_a terminal=term_a worktree="+h.worktree())
	command := flag(h.orcaCalls("terminal create")[0], "--command")
	if !strings.Contains(command, " codex --dangerously-bypass-approvals-and-sandbox -c model=gpt-6.1-sol -c model_reasoning_effort=high -c model_auto_compact_token_limit=300000"+codexLaneConfig+" -c mcp_servers={}") || strings.Contains(command, "service_tier") {
		t.Fatal(command)
	}
	start := h.orcaCalls("orchestration worker-start")[0]
	if flag(start, "--terminal") != "term_a" || slices.Contains(start, "--agent") || slices.Contains(start, "--model") {
		t.Fatal(start)
	}
	wantLine(t, strings.TrimSpace(h.readReceipt("lane-a.terminal")), "term_a")
	if sets := h.orcaCalls("worktree set"); !slices.Contains(sets[0], "--parent-worktree") || slices.Contains(sets[0], "--no-parent") {
		t.Fatal(sets)
	}
}

func TestACodexLaneReadsABriefCopyThatHandsItsPRProseToOpus(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("orchestration worker-start", effectStart("term_a"))
	h.launchedAs("sol", "high")
	copyPath := filepath.Join(h.receipts, "lane-a.brief.md")
	if !strings.Contains(h.spec(), "read "+copyPath+" in full") {
		t.Fatal(h.spec())
	}
	text := h.readReceipt("lane-a.brief.md")
	for _, want := range []string{"## PR prose", "You write your own PR title", "Never ask the coordinator"} {
		if !strings.Contains(text, want) {
			t.Errorf("copy lacks %q", want)
		}
	}
	if !strings.HasPrefix(text, "# brief\n") || strings.Contains(text, "## PR record") {
		t.Fatal(text)
	}
}

func TestAnIncidentLanePushesItsCandidateWithoutWaitingForItsPRProse(t *testing.T) {
	h := newHarness(t)
	h.healthyCodex()
	h.reply("orchestration worker-start", effectStart("term_a"))
	h.launchedAs(agentIncident, "xhigh")
	text := h.readReceipt("lane-a.brief.md")
	for _, want := range []string{"open the PR within 10 minutes of the candidate", "a short body you write yourself"} {
		if !strings.Contains(text, want) {
			t.Errorf("copy lacks %q", want)
		}
	}
	if strings.Contains(text, "pr-prose") {
		t.Fatal(text)
	}
}

func TestACodexLaneInADriveReadsABriefCopyThatRegistersItFirstAndRecordsItsPROpened(t *testing.T) {
	h := newHarness(t)
	h.env[driveEnv] = "900424b6"
	h.healthyCodex()
	h.reply("orchestration worker-start", effectStart("term_a"))
	h.launchedAs(agentIncident, "xhigh")
	text := h.readReceipt("lane-a.brief.md")
	register := "`cci post --drive '900424b6' --lane 'lane-a' --kind state 'lane-a: registered at its first command'`"
	if !strings.HasPrefix(text, "## First command") || strings.Index(text, register) >= strings.Index(text, "# brief\n") {
		t.Fatal(text)
	}
	if strings.Index(text, "## PR prose") >= strings.Index(text, "## PR record") {
		t.Fatal(text)
	}
	if want := "`'" + pluginBin + "/drive.py' record --opened --drive '900424b6' --session \"$CODEX_THREAD_ID\" --lane 'lane-a' --cwd \"$PWD\" --pr <number>=<head>`"; !strings.Contains(text, want) {
		t.Fatal(text)
	}
}

func TestAClaudeLaneReadsItsBriefItself(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launched()
	if exists(filepath.Join(h.receipts, "lane-a.brief.md")) {
		t.Fatal("a copy exists")
	}
}

func TestAnIncidentLaneRunsCodexOnTheFastTierInItsOwnTerminalInATopLevelWorktree(t *testing.T) {
	h := newHarness(t)
	h.healthyCodex()
	wantLine(t, h.launchedAs(agentIncident, "xhigh"), readyLine(h))
	set := h.orcaCalls("worktree set")[0]
	if !slices.Contains(set, "--no-parent") || slices.Contains(set, "--parent-worktree") {
		t.Fatal(set)
	}
	wantLine(t, flag(h.orcaCalls("terminal create")[0], "--command"), "sh -c 'PATH="+pluginBin+":$PATH exec env CLAUDE_LONG_RUNNING_LANE=lane-a codex --dangerously-bypass-approvals-and-sandbox -c model=gpt-6.1-sol -c service_tier=fast -c model_reasoning_effort=xhigh"+codexCompact+codexLaneConfig+" -c mcp_servers={}'")
	start := h.orcaCalls("orchestration worker-start")[0]
	if flag(start, "--terminal") != "term_a" || slices.Contains(start, "--agent") || slices.Contains(start, "--model") || slices.Contains(start, "--effort") {
		t.Fatal(start)
	}
	wantNoCalls(t, h, "terminal read")
	wantLine(t, strings.TrimSpace(h.readReceipt("lane-a.terminal")), "term_a")
}

func TestAWorkerStartsOnlyTheMCPServersItsLaunchNames(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_MCP_CONFIG"] = "/briefs/slack-mcp.json"
	h.launched()
	if command := flag(h.orcaCalls("terminal create")[0], "--command"); !strings.Contains(command, " --strict-mcp-config --no-chrome --mcp-config /briefs/slack-mcp.json ") {
		t.Fatal(command)
	}
}

func TestOnlyABriefAskingForSlackStartsTheSlackServer(t *testing.T) {
	for brief, want := range map[string]bool{"ccx: role=reader place=local why=mac-question mcp=slack\nScale: one\n": true, "ccx: role=reader place=local why=mac-question\nScale: one\n": false} {
		h := newHarness(t)
		h.healthyClaude()
		h.env["ORCA_LAUNCH_SLACK_MCP_CONFIG"] = "/briefs/slack-only.json"
		h.writeFile(h.brief, brief)
		h.launched()
		if command := flag(h.orcaCalls("terminal create")[0], "--command"); strings.Contains(command, " --mcp-config /briefs/slack-only.json") != want {
			t.Fatalf("%q: %s", brief, command)
		}
	}
}

func TestAnIncidentWorkerTakesItsMCPServersFromTheLaunch(t *testing.T) {
	h := newHarness(t)
	h.healthyCodex()
	h.env["ORCA_LAUNCH_CODEX_MCP"] = `{datadog={url="https://mcp.datadoghq.com"}}`
	h.launchedAs(agentIncident, "xhigh")
	if command := flag(h.orcaCalls("terminal create")[0], "--command"); !strings.HasSuffix(command, ` -c mcp_servers={datadog={url="https://mcp.datadoghq.com"}}'`) {
		t.Fatal(command)
	}
}

func TestAnIncidentWorkerDisablesEveryConfigServerItsLaunchDoesNotName(t *testing.T) {
	h := newHarness(t)
	h.healthyCodex()
	h.writeFile(filepath.Join(h.root, "codex", "config.toml"), "model = \"gpt-6-luna\"\n[mcp_servers.node_repl]\ncommand = \"node_repl\"\n[mcp_servers.slack]\ncommand = \"npx\"\n[mcp_servers.datadog]\nurl = \"https://mcp.datadoghq.com\"\n[plugins.\"computer-history@openai-bundled\"]\nenabled = true\n")
	h.env["ORCA_LAUNCH_CODEX_MCP"] = `{datadog={url="https://mcp.datadoghq.com"}}`
	h.launchedAs(agentIncident, "xhigh")
	if command := flag(h.orcaCalls("terminal create")[0], "--command"); !strings.HasSuffix(command, ` -c mcp_servers={datadog={url="https://mcp.datadoghq.com"}} -c mcp_servers.node_repl.enabled=false -c mcp_servers.slack.enabled=false'`) {
		t.Fatal(command)
	}
}

func TestAClaudeLaneNeverReadsTheCodexConfig(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.writeFile(filepath.Join(h.root, "codex", "config.toml"), "not toml [")
	h.launched()
}

func TestANewWorktreeClosesNoTerminalBeforeItsAgentTerminalOpens(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launched()
	wantNoCalls(t, h, "terminal close")
}

func TestALaunchRunsThePrepareCommandInTheWorktreeItCreatedBeforeTheWorkerStarts(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_PREPARE"] = "touch generated"
	wantLine(t, h.launched(), readyLine(h))
	if !exists(filepath.Join(h.worktree(), "generated")) {
		t.Fatal("the command did not run in the worktree")
	}
	wantLine(t, h.readReceipt("lane-a.prepare.log"), "")
	if exists(filepath.Join(h.receipts, "lane-a.brief.md")) {
		t.Fatal("a copy exists")
	}
	if !strings.Contains(h.spec(), h.brief) {
		t.Fatal(h.spec())
	}
}

func TestAFailedPrepareCommandStillLaunchesAndTellsTheWorkerInItsBrief(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_PREPARE"] = "echo sqlc: command not found >&2; exit 127"
	wantLine(t, h.launched(), readyLine(h))
	log := filepath.Join(h.receipts, "lane-a.prepare.log")
	wantLine(t, h.readReceipt("lane-a.prepare.log"), "sqlc: command not found\n")
	raw, err := os.ReadFile(h.brief)
	if err != nil || string(raw) != "# brief\n" {
		t.Fatalf("the brief changed: %q, err %v", raw, err)
	}
	wantLine(t, h.readReceipt("lane-a.brief.md"), "# brief\n\n\n## Worktree preparation\n\nThe launch ran `echo sqlc: command not found >&2; exit 127` in this worktree and it failed; its output is in "+log+". Run it yourself before you build.\n")
	if !strings.Contains(h.spec(), "read "+filepath.Join(h.receipts, "lane-a.brief.md")+" in full") {
		t.Fatal(h.spec())
	}
}

func TestAFailedPrepareCommandAddsToTheBriefCopyACodexLaneAlreadyReads(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("orchestration worker-start", effectStart("term_a"))
	h.env["ORCA_LAUNCH_PREPARE"] = "false"
	h.launchedAs("sol", "high")
	text := h.readReceipt("lane-a.brief.md")
	prose, prep := strings.Index(text, "## PR prose"), strings.Index(text, "## Worktree preparation")
	if !strings.HasPrefix(text, "# brief\n") || prose <= 0 || prose >= prep {
		t.Fatal(text)
	}
}

func TestAPrepareCommandPastItsCeilingIsStoppedAndTheLaunchGoesOn(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_PREPARE"] = "exec /bin/sleep 60"
	h.env["ORCA_LAUNCH_PREPARE_SECONDS"] = "1"
	started := time.Now()
	h.launched()
	if time.Since(started) > 30*time.Second {
		t.Fatal("the ceiling did not stop the command")
	}
	if !strings.Contains(h.readReceipt("lane-a.brief.md"), "## Worktree preparation") {
		t.Fatal("the worker was not told")
	}
}

func TestARelaunchIntoAnExistingWorktreeRunsNoPrepareCommand(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.checkOut()
	h.env["ORCA_LAUNCH_PREPARE"] = "touch generated"
	h.launched()
	wantNoCalls(t, h, "worktree set")
	if exists(filepath.Join(h.worktree(), "generated")) || exists(filepath.Join(h.receipts, "lane-a.prepare.log")) {
		t.Fatal("the command ran")
	}
}

func TestNoParentPutsAClaudeLaneInATopLevelWorktree(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_NO_PARENT"] = "1"
	h.launched()
	set := h.orcaCalls("worktree set")[0]
	if !slices.Contains(set, "--no-parent") || slices.Contains(set, "--parent-worktree") {
		t.Fatal(set)
	}
}

func TestACodexReadinessTimeoutSendsTheSpecAndRunsUnsupervised(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("orchestration worker-start", reply{rc: 1, out: object{"ok": true, "result": object{"state": "failed", "failedStage": "agent_readiness", "taskId": "task_a", "dispatchId": "ctx_a", "effects": []object{{"kind": "terminal", "role": "agent", "id": "term_codex"}}}}})
	h.reply("terminal send", reply{out: object{"ok": true}})
	wantLine(t, h.launchedAs(agentCodex, "high"), "lane-a unsupervised task=task_a dispatch=ctx_a terminal=term_a worktree="+h.worktree())
	send := h.orcaCalls("terminal send")[0]
	if flag(send, "--terminal") != "term_a" || !strings.Contains(flag(send, "--text"), filepath.Join(h.receipts, "lane-a.brief.md")) || !slices.Contains(send, "--enter") {
		t.Fatal(send)
	}
}

func TestAnIncidentReadinessTimeoutSendsTheSpecToItsOwnTerminal(t *testing.T) {
	h := newHarness(t)
	h.healthyCodex()
	h.reply("orchestration worker-start", readinessTimeout)
	h.reply("terminal send", reply{out: object{"ok": true}})
	wantLine(t, h.launchedAs(agentIncident, "xhigh"), "lane-a unsupervised task=task_a dispatch=ctx_a terminal=term_a worktree="+h.worktree())
	if got := flag(h.orcaCalls("terminal send")[0], "--terminal"); got != "term_a" {
		t.Fatal(got)
	}
}

func TestAReadinessTimeoutAtAnUpdatePromptNeverSendsTheSpec(t *testing.T) {
	for _, model := range []string{agentCodex, agentIncident} {
		for _, prompt := range []string{updateAvailable, skipUntilNext} {
			h := newHarness(t)
			h.healthy(healthyOptions{agent: agentCodex, screen: prompt})
			h.reply("orchestration worker-start", reply{rc: 1, out: object{"ok": true, "result": object{"state": "failed", "failedStage": "agent_readiness", "taskId": "task_a", "dispatchId": "ctx_a", "effects": []object{{"kind": "terminal", "role": "agent", "id": "term_codex"}}}}})
			h.reply("terminal send", sent)
			terminal := "term_a"
			line, code := h.launchAs("lane-a", model, "high", h.brief)
			if code != 1 {
				t.Fatal(line)
			}
			wantLine(t, line, fmt.Sprintf(`lane-a failed agent_readiness terminal=%s update prompt: '["❯","%s"]'`, terminal, prompt))
			reads := h.orcaCalls("terminal read")
			if len(reads) != 1 || !slices.Equal(reads[0], []string{"terminal", "read", "--terminal", terminal, "--screen", "--json"}) {
				t.Fatal(reads)
			}
			wantNoCalls(t, h, "terminal send")
		}
	}
}

func TestAnUpdatePromptFailureQuotesOneLineOfAtMost300ScreenCharacters(t *testing.T) {
	h := newHarness(t)
	h.healthyCodex()
	prompt := "\nUpdate available!\t\tSkip until next version\r\nRelease notes: https://github.com/openai/codex/releases/latest\n" + strings.Repeat("x", 320)
	h.reply("terminal read", okResult(object{"terminal": object{"tail": prompt}}))
	h.reply("orchestration worker-start", readinessTimeout)
	h.reply("terminal send", sent)
	line, code := h.launchAs("lane-a", agentIncident, "xhigh", h.brief)
	if code != 1 || strings.Contains(line, "\n") {
		t.Fatal(line)
	}
	const prefix = "lane-a failed agent_readiness terminal=term_a update prompt: '"
	quoted := strings.TrimSuffix(strings.TrimPrefix(line, prefix), "'")
	if len(quoted) != 300 || !strings.HasPrefix(quoted, " Update available! Skip until next version Release notes: https://github.com/openai/codex/releases/latest ") {
		t.Fatalf("%d %q", len(quoted), quoted)
	}
	wantNoCalls(t, h, "terminal send")
}

func TestARelaunchRetriesTheRecordedDispatchInTheExistingWorktree(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.checkOut()
	h.writeFile(filepath.Join(h.receipts, "lane-a.json"), `{"result": {"taskId": "task_old", "dispatchId": "ctx_old"}}`)
	h.reply("orchestration task-list", taskList("task_other"), taskList("task_old"))
	h.launched()
	wantNoCalls(t, h, "worktree set")
	lists := h.orcaCalls("orchestration task-list")
	statuses := make([]string, 0, len(lists))
	for _, call := range lists {
		statuses = append(statuses, flag(call, "--status"))
	}
	if !slices.Equal(statuses, []string{statusFailed, "blocked"}) {
		t.Fatal(statuses)
	}
	start := h.orcaCalls("orchestration worker-start")[0]
	if flag(start, "--task") != "task_old" || flag(start, "--retry-of") != "ctx_old" || slices.Contains(start, "--spec") {
		t.Fatal(start)
	}
}

func TestARelaunchAfterTheRecordedTaskCompletedStartsAFreshTask(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.checkOut()
	h.writeFile(filepath.Join(h.receipts, "lane-a.json"), `{"result": {"taskId": "task_old", "dispatchId": "ctx_old"}}`)
	h.reply("orchestration task-list", taskList("task_other"))
	wantLine(t, h.launched(), readyLine(h))
	start := h.orcaCalls("orchestration worker-start")[0]
	if slices.Contains(start, "--task") || slices.Contains(start, "--retry-of") || flag(start, "--task-title") != "v3-lane-a" {
		t.Fatal(start)
	}
	if !strings.Contains(h.readReceipt("lane-a.json"), "task_a") {
		t.Fatal(h.readReceipt("lane-a.json"))
	}
}

func TestALaunchThatStallsTwiceFailsOnceWithLaunchFailedNamingTheSecondWait(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.tickOnSleep()
	h.env["ORCA_LAUNCH_STALL_SECONDS"] = "0"
	h.reply("worktree show", missing)
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	retried := filepath.Join(filepath.Dir(h.parent), "v3-lane-a-1-2")
	wantLine(t, line, "lane-a failed LAUNCH-FAILED: Orca does not list worktree "+retried+" yet for 300s; rolled back worktree="+retried+" branch=yasyf/v3-lane-a-1-2; this was its one retry")
	if strings.Count(h.stderr.String(), "lane-a retries once after a stall: Orca does not list worktree "+h.worktree()+" yet for 300s\n") != 1 {
		t.Fatal(h.stderr.String())
	}
	wantNoCalls(t, h, "terminal create")
	if len(slices.DeleteFunc(slices.Clone(h.sleeps), func(d time.Duration) bool { return d != stallRetry })) != 1 {
		t.Fatal(h.sleeps)
	}
	for _, wt := range []string{h.worktree(), retried} {
		if exists(wt) || h.gitIn(h.main, "branch", "--list", "yasyf/"+filepath.Base(wt)) != "" {
			t.Fatal("the rollback left work behind")
		}
	}
}

func TestALaunchThatStallsOnceRetriesUnderTheSameLaneAndStartsOneWorker(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.tickOnSleep()
	h.env["ORCA_LAUNCH_STALL_SECONDS"] = "0"
	h.reply("worktree show", append(slices.Repeat([]reply{missing}, 76), sent)...)
	retried := filepath.Join(filepath.Dir(h.parent), "v3-lane-a-1-2")
	wantLine(t, h.launched(), "lane-a ready task=task_a dispatch=ctx_a terminal=term_a worktree="+retried)
	if starts := h.orcaCalls("orchestration worker-start"); len(starts) != 1 {
		t.Fatal(starts)
	}
	if strings.Contains(h.stderr.String(), "LAUNCH-FAILED") || exists(h.worktree()) || h.gitIn(h.main, "branch", "--list", testBranch) != "" {
		t.Fatal(h.stderr.String())
	}
}

func TestARefusedLaunchIsNeverRetried(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_STALL_SECONDS"] = "0"
	h.reply("orchestration worker-start", fencedStart)
	if line, code := h.launch(); code != 1 || strings.Contains(line, "one retry") {
		t.Fatal(line)
	}
	if starts := h.orcaCalls("orchestration worker-start"); len(starts) != 1 || strings.Contains(h.stderr.String(), "retries once") {
		t.Fatal(starts)
	}
	if len(h.sleeps) != 0 {
		t.Fatal(h.sleeps)
	}
}

func TestARollbackKeepsABranchThatHoldsCommitsAndSaysWhy(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.reply("terminal create", reply{out: object{"ok": true, "result": object{"terminal": object{"handle": "term_a"}}}, sh: "git -C " + h.worktree() + " -c user.name=t -c user.email=t@t commit -q --allow-empty -m work"})
	line, _ := h.launch()
	if !strings.HasSuffix(line, "; rolled back terminal=term_a; rollback left worktree="+h.worktree()+" branch="+testBranch+" (it holds commits not on origin/dev)") {
		t.Fatal(line)
	}
	if !exists(h.worktree()) || h.gitIn(h.main, "branch", "--list", testBranch) == "" {
		t.Fatal("the rollback dropped a branch holding commits")
	}
}

func TestAWorktreeOrcaListsLateIsAwaitedAndThenNamed(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("worktree show", missing, missing, h.registeredReply())
	h.launched()
	if !slices.Equal(h.sleeps[:2], []time.Duration{startPoll, startPoll}) {
		t.Fatal(h.sleeps)
	}
}

func TestAWorktreeOrcaListsOnlyAfterItsCacheExpiresIsAwaitedPastTheStallLimit(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.tickOnSleep()
	listsAfter := (180+worktreeCacheSeconds)/int(startPoll/time.Second) - 1
	h.reply("worktree show", append(slices.Repeat([]reply{missing}, listsAfter), h.registeredReply())...)
	wantLine(t, h.launched(), readyLine(h))
	if len(h.sleeps) != listsAfter || *h.elapsed <= 180*time.Second {
		t.Fatal(len(h.sleeps), *h.elapsed)
	}
	if sets := h.orcaCalls("worktree set"); len(sets) != 1 || flag(sets[0], "--worktree") != "path:"+h.worktree() {
		t.Fatal(sets)
	}
}

func TestAWorktreeOrcaNeverListsFailsOnceItsCacheCouldHaveExpired(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.tickOnSleep()
	h.env["ORCA_LAUNCH_STALL_SECONDS"] = "60"
	h.reply("worktree show", missing)
	line, code := h.launch()
	if code != 1 || !strings.Contains(line, "LAUNCH-FAILED: Orca does not list worktree ") || !strings.Contains(line, " yet for 360s") {
		t.Fatal(line)
	}
}

func TestAReceiptNamingAWorktreeThatIsNoCheckoutCutsAFreshNameAndLeavesTheDirectory(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.writeFile(filepath.Join(h.worktree(), "stray"), "x\n")
	h.writeFile(filepath.Join(h.receipts, "lane-a.worktree"), h.worktree()+"\n")
	fresh := filepath.Join(filepath.Dir(h.parent), "v3-lane-a-1-2")
	if line := h.launched(); !strings.HasSuffix(line, "worktree="+fresh) {
		t.Fatal(line)
	}
	wantLine(t, strings.TrimSpace(h.readReceipt("lane-a.worktree")), fresh)
	entries, err := os.ReadDir(h.worktree())
	if err != nil || len(entries) != 1 || entries[0].Name() != "stray" {
		t.Fatalf("entries %v, err %v", entries, err)
	}
}

func TestACreateThatPrintsNoHandlePollsForItsTerminalEveryFourSeconds(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("terminal create", timedOut, okResult(object{"terminal": object{"handle": "term_dup"}}))
	h.reply("terminal list", bareListing(), bareListing(), bareListing(), listing(agentClaude))
	h.launched()
	if len(h.orcaCalls("terminal create")) != 1 || !slices.Equal(h.sleeps[:3], []time.Duration{startPoll, startPoll, startPoll}) {
		t.Fatal(h.sleeps)
	}
	if got := flag(h.orcaCalls("orchestration worker-start")[0], "--terminal"); got != "term_a" {
		t.Fatal(got)
	}
}

func TestLaunchLinksABriefWhosePointerPasses500Characters(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	brief := filepath.Join(h.root, strings.Repeat("x", 250), "lane-a.full.md")
	h.writeFile(brief, "# brief\n")
	if line, code := h.launchAs("lane-a", "opus", "high", brief); code != 0 {
		t.Fatal(line)
	}
	spec := h.spec()
	link := strings.SplitN(strings.SplitN(spec, "read ", 2)[1], " in full", 2)[0]
	if strings.Contains(spec, brief) || filepath.Dir(link) != filepath.Join(h.env["HOME"], ".local", "state", "long-running", "orca-launch") || len(filepath.Base(link)) != 8 {
		t.Fatal(spec)
	}
	target, err := os.Readlink(link)
	if err != nil || target != brief {
		t.Fatalf("target %q, err %v", target, err)
	}
}

func TestLaunchLeavesAShortPointerUnlinked(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launched()
	if exists(filepath.Join(h.env["HOME"], ".local")) {
		t.Fatal("a link directory exists")
	}
}

func TestLaunchStartsALaneWhoseWorktreePathAlonePasses500Characters(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	root := filepath.Join(h.root, strings.Repeat("x", 250), strings.Repeat("y", 250))
	h.env["ORCA_LAUNCH_ROOT"] = root
	h.reply("worktree show", okResult(object{"worktree": object{"path": filepath.Join(root, "v3-lane-a-1-1")}}))
	h.launched()
	spec := h.spec()
	if len(spec) <= specLimit || !strings.Contains(spec, "Worktree "+filepath.Join(root, "v3-lane-a-1-1")+", ") {
		t.Fatal(spec)
	}
}

func TestLaunchFailsWhenTheReceiptIsNotReady(t *testing.T) {
	h := newHarness(t)
	h.healthy(healthyOptions{state: statusFailed, agent: agentClaude})
	line, code := h.launch()
	if code != 1 || !strings.HasPrefix(line, "lane-a failed worker-start state=failed") {
		t.Fatal(line)
	}
}

func TestAnUnobservedTurnStartLaunchesOnceWorkerShowProvesTheWorkerStarted(t *testing.T) {
	for _, started := range []reply{workerShown("working", "start_unknown"), workerShown("done", "succeeded")} {
		h := newHarness(t)
		h.healthyClaude()
		h.reply("orchestration worker-start", unobserved)
		h.reply("orchestration worker-show", workerShown("unknown", "start_unknown"), started)
		if line := h.launched(); !strings.HasPrefix(line, "lane-a ready task=task_a dispatch=ctx_a terminal=term_a ") {
			t.Fatal(line)
		}
		if len(h.orcaCalls("orchestration worker-show")) != 2 {
			t.Fatal(h.calls)
		}
		wantLine(t, strings.TrimSpace(h.readReceipt("lane-a.terminal")), "term_a")
	}
}

func TestAnUnobservedTurnStartThatNeverStartsFailsNamingTheDispatchAndKeepsIt(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "8"
	h.reply("orchestration worker-start", unobserved)
	h.reply("orchestration worker-show", workerShown("unknown", "start_unknown"))
	line, code := h.launch()
	if code != 1 || !strings.HasPrefix(line, "lane-a failed worker-start outcome_unknown dispatch=ctx_a terminal=term_a ") || !strings.Contains(line, "worker-abandon --dispatch ctx_a") || strings.Contains(line, "rolled back") {
		t.Fatal(line)
	}
	if len(h.orcaCalls("orchestration worker-show")) != 3 {
		t.Fatal(h.calls)
	}
	wantNoCalls(t, h, "terminal close")
	if !strings.Contains(h.readReceipt("lane-a.json"), "ctx_a") {
		t.Fatal("the receipt lost the dispatch")
	}
}

func TestAnIdleAgentOrcaDoesNotRecognizeYetIsStartedAgain(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("orchestration worker-start", unconfigured, unconfigured, okResult(object{"state": "ready", "taskId": "task_a", "dispatchId": "ctx_a"}))
	h.launched()
	if len(h.orcaCalls("orchestration worker-start")) != 3 || !slices.Equal(h.sleeps, []time.Duration{startPoll, startPoll}) {
		t.Fatal(h.sleeps)
	}
}

func TestAWorkerStartTheRuntimeDidNotAnswerIsReplayedUnderItsRequestID(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	unanswered := reply{rc: 1, out: object{"ok": false, "error": object{"code": "runtime_timeout", "message": "Timed out waiting for the Orca runtime to respond.", "data": object{"orchestrationRequestId": "req-7"}}}}
	h.reply("orchestration worker-start", unanswered, okResult(object{"state": "ready", "taskId": "task_a", "dispatchId": "ctx_a"}))
	h.launched()
	starts := h.orcaCalls("orchestration worker-start")
	if len(starts) != 2 || !slices.Equal(starts[1], slices.Concat(starts[0], []string{"--retry-request", "req-7"})) {
		t.Fatalf("worker-start calls %q, want the first replayed under its request id", starts)
	}
}

func TestAnAgentOrcaNeverRecognizesRollsBackAtTheBootCeiling(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "8"
	h.reply("orchestration worker-start", unconfigured)
	line, code := h.launch()
	if code != 1 || len(h.orcaCalls("orchestration worker-start")) != 3 || !strings.HasSuffix(line, "; rolled back terminal=term_a worktree="+h.worktree()+" branch="+testBranch) {
		t.Fatal(line)
	}
}

func TestLaunchFailsWhenTheTerminalIsNotInBypassMode(t *testing.T) {
	h := newHarness(t)
	h.healthy(healthyOptions{agent: agentClaude, screen: "⏸ plan mode on (shift+tab to cycle)"})
	line, code := h.launch()
	if code != 1 || !strings.Contains(line, "shift-tab") || len(h.orcaCalls("terminal read")) != bypassReads {
		t.Fatal(line)
	}
}

func TestLaunchStartsTheWorkerOnceOrcaSeesItsAgentIdle(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("terminal wait", notIdleWait, idleWait)
	h.launched()
	waits := h.orcaCalls("terminal wait")
	if flag(waits[0], "--timeout-ms") != "60000" || flag(waits[1], "--timeout-ms") != "120000" || flag(waits[0], "--for") != "tui-idle" || len(h.sleeps) != 0 {
		t.Fatal(waits)
	}
	steps := h.steps()
	if !slices.Equal(steps[len(steps)-3:], []string{"terminal wait", "orchestration worker-start", "terminal read"}) {
		t.Fatal(steps)
	}
}

func TestAnAgentIdentityWithoutAnIdleAgentNeverReachesWorkerStart(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("terminal wait", notIdleWait)
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "10"
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed boot terminal=term_a: orca terminal wait --for tui-idle reads timeout: timeout after 10s; rolled back terminal=term_a worktree="+h.worktree()+" branch="+testBranch)
	waits := h.orcaCalls("terminal wait")
	if flag(waits[0], "--timeout-ms") != "3333" || flag(waits[1], "--timeout-ms") != "6667" {
		t.Fatal(waits)
	}
	wantNoCalls(t, h, "orchestration worker-start", "terminal send")
}

func TestAnAgentBlockedOnAStartupPromptFailsNamingThePrompt(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("terminal wait", reply{rc: 1, out: object{"ok": true, "result": object{"wait": object{"handle": "term_a", "condition": "tui-idle", "satisfied": false, "status": "running", "exitCode": nil, "blockedReason": "agent-trust-workspace"}}}})
	h.reply("terminal read", onScreen("yasyf@mac ~/v3-lane-a-base> env CLAUDE_LONG_RUNNING_LANE=lane-a claude", "Do you trust the files in this folder?"))
	line, code := h.launch()
	if code != 1 || !strings.Contains(line, "boot terminal=term_a: orca terminal wait --for tui-idle reads agent-trust-workspace after 180s;") {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "terminal send", "orchestration worker-start")
}

func TestAnAgentThatExitedIsNotIdle(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("terminal wait", okResult(object{"wait": object{"handle": "term_a", "condition": "tui-idle", "satisfied": true, "status": "exited", "exitCode": 1}}))
	h.reply("terminal read", onScreen("yasyf@mac ~/v3-lane-a-base> env CLAUDE_LONG_RUNNING_LANE=lane-a claude", "yasyf@mac ~/v3-lane-a-base>"))
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
	line, code := h.launch()
	if code != 1 || !strings.Contains(line, "reads status=exited after 12s") {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "orchestration worker-start")
}

func TestAStartupCommandOrcaDroppedIsTypedIntoTheTerminalOnce(t *testing.T) {
	for model, agent := range map[string]string{"opus": agentClaude, agentIncident: agentCodex} {
		h := newHarness(t)
		h.healthy(healthyOptions{agent: agent})
		h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "40"
		h.reply("terminal create", timedOut)
		h.reply("terminal list", bareListing(), listing(nil))
		h.reply("terminal wait", notIdleWait, idleWait)
		h.reply("terminal read", onScreen("Welcome to fish", "yasyf@mac ~/v3-lane-a-base>"), onScreen("❯", agentScreen))
		h.reply("terminal send", sent)
		if line, code := h.launchAs("lane-a", model, "high", h.brief); code != 0 {
			t.Fatal(line)
		}
		create, send := h.orcaCalls("terminal create")[0], h.orcaCalls("terminal send")[0]
		if flag(send, "--terminal") != "term_a" || flag(send, "--text") != flag(create, "--command") || !slices.Contains(send, "--enter") {
			t.Fatal(send)
		}
		steps := h.steps()
		if slices.Index(steps, "terminal send") >= slices.Index(steps, "orchestration worker-start") {
			t.Fatal(steps)
		}
	}
}

func TestATypedCommandThatStartsNoAgentFailsTheLaunchBeforeWorkerStart(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
	h.reply("terminal read", onScreen("yasyf@mac ~/v3-lane-a-base>"))
	h.reply("terminal send", sent)
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed boot terminal=term_a: orca terminal wait --for tui-idle reads timeout: timeout after 12s; the command was typed into the terminal once; rolled back terminal=term_a worktree="+h.worktree()+" branch="+testBranch)
	if len(h.orcaCalls("terminal send")) != 1 {
		t.Fatal(h.calls)
	}
	wantNoCalls(t, h, "orchestration worker-start")
}

func TestAFailedCommandSendFailsTheLaunchBeforeWorkerStart(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
	h.reply("terminal read", onScreen("yasyf@mac ~/v3-lane-a-base>"))
	h.reply("terminal send", reply{rc: 1, out: "runtime_unavailable"})
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed command send terminal=term_a after Orca dropped the startup command; rolled back terminal=term_a worktree="+h.worktree()+" branch="+testBranch)
	wantNoCalls(t, h, "orchestration worker-start")
}

func TestACommandLineWrappedAcrossScreenRowsIsNotTypedAgain(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
	h.reply("terminal read", onScreen("yasyf@mac ~/v3-lane-a-base> env CLAUDE_LONG_RUNN", "ING_LANE=lane-a claude --model claude-opus-5-5"))
	line, code := h.launch()
	if code != 1 || !strings.HasSuffix(line, "after 12s; rolled back terminal=term_a worktree="+h.worktree()+" branch="+testBranch) {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "terminal send")
}

func TestNoCommandIsTypedWhenTheScreenCannotBeRead(t *testing.T) {
	for _, read := range []reply{{rc: 1}, okResult(object{"terminal": object{"source": screenUnavailable, "tail": []string{"yasyf@mac ~/v3-lane-a-base>"}}})} {
		h := newHarness(t)
		h.noAgent()
		h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "12"
		h.reply("terminal read", read)
		if _, code := h.launch(); code != 1 {
			t.Fatal("the launch succeeded")
		}
		wantNoCalls(t, h, "terminal send")
	}
}

func TestNoCommandIsTypedWhenTheAgentIdlesWithinAThirdOfTheBootCeiling(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launched()
	waits := h.orcaCalls("terminal wait")
	if len(waits) != 1 || flag(waits[0], "--timeout-ms") != "60000" || len(h.orcaCalls("terminal read")) != 1 {
		t.Fatal(waits)
	}
	wantNoCalls(t, h, "terminal send")
}

func TestEveryTerminalListIsScopedToTheLanesWorktree(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launched()
	lists := h.orcaCalls("terminal list")
	if len(lists) != 1 || flag(lists[0], "--worktree") != "path:"+h.worktree() {
		t.Fatal(lists)
	}
}

func TestACreateThatPrintsNoHandleAdoptsTheTerminalItCreated(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("terminal create", reply{out: "Error: socket hang up"}, okResult(object{"terminal": object{"handle": "term_dup"}}))
	h.reply("terminal list", bareListing(), listing(nil), listing(agentClaude))
	wantLine(t, h.launched(), readyLine(h))
	if len(h.orcaCalls("terminal create")) != 1 || flag(h.orcaCalls("orchestration worker-start")[0], "--terminal") != "term_a" {
		t.Fatal(h.calls)
	}
	wantLine(t, h.readReceipt("lane-a.terminal.json"), "Error: socket hang up")
}

func TestARelaunchNeverAdoptsATerminalThatPredatesTheCreate(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.reply("terminal create", reply{}, okResult(object{"terminal": object{"handle": "term_b"}}))
	h.reply("terminal list", listing(agentClaude, "term_a"), listing(agentClaude, "term_a"), listing(agentClaude, "term_a", "term_b"))
	h.launched()
	if len(h.orcaCalls("terminal create")) != 2 || flag(h.orcaCalls("orchestration worker-start")[0], "--terminal") != "term_b" {
		t.Fatal(h.calls)
	}
}

func TestAFailedCreateReportsItsOutputAfterThreeAttempts(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.reply("terminal create", reply{rc: 1, out: "runtime_unavailable"})
	h.reply("terminal list", bareListing())
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed terminal create: runtime_unavailable; rolled back worktree="+h.worktree()+" branch="+testBranch)
	if len(h.orcaCalls("terminal create")) != 3 {
		t.Fatal(h.calls)
	}
}

func TestAnIncidentLaneWaitsForItsCodexToIdle(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	line, code := h.launchAs("lane-a", agentIncident, "xhigh", h.brief)
	if code != 1 || !strings.Contains(line, "boot terminal=term_a: orca terminal wait --for tui-idle reads timeout") {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "orchestration worker-start")
}

func TestAFailedStartKeepsItsDispatchForTheRelaunch(t *testing.T) {
	h := newHarness(t)
	h.healthy(healthyOptions{state: statusFailed, agent: agentClaude})
	if _, code := h.launch(); code != 1 {
		t.Fatal("the launch succeeded")
	}
	h.healthyClaude()
	h.reply("orchestration task-list", taskList("task_a"))
	h.launched()
	starts := h.orcaCalls("orchestration worker-start")
	if !slices.Contains(starts[0], "--spec") || flag(starts[1], "--task") != "task_a" || flag(starts[1], "--retry-of") != "ctx_a" {
		t.Fatal(starts)
	}
}

func TestARelaunchReusesTheWorktreeItsReceiptNames(t *testing.T) {
	h := newHarness(t)
	elsewhere := filepath.Join(h.root, "elsewhere", "lane-a")
	if err := os.MkdirAll(elsewhere, 0o750); err != nil {
		t.Fatal(err)
	}
	h.gitIn(elsewhere, "init", "-q")
	h.gitIn(elsewhere, "commit", "-q", "--allow-empty", "-m", "base")
	h.writeFile(filepath.Join(h.receipts, "lane-a.worktree"), elsewhere+"\n")
	h.healthyClaude()
	if line := h.launched(); !strings.HasSuffix(line, "worktree="+elsewhere) {
		t.Fatal(line)
	}
	if !strings.Contains(h.spec(), "Worktree "+elsewhere+",") || exists(h.worktree()) {
		t.Fatal(h.spec())
	}
	wantNoCalls(t, h, "worktree set")
	if got := flag(h.orcaCalls("terminal create")[0], "--worktree"); got != "path:"+elsewhere {
		t.Fatal(got)
	}
}

func TestARelaunchReusesTheWorktreeTheFirstLaunchAdded(t *testing.T) {
	h := newHarness(t)
	h.healthy(healthyOptions{state: statusFailed, agent: agentClaude})
	if _, code := h.launch(); code != 1 {
		t.Fatal("the launch succeeded")
	}
	h.healthyClaude()
	h.launched()
	if len(h.orcaCalls("worktree set")) != 1 {
		t.Fatal(h.calls)
	}
	for _, call := range h.orcaCalls("terminal create") {
		if flag(call, "--worktree") != "path:"+h.worktree() {
			t.Fatal(call)
		}
	}
}

func TestADefaultLaunchCreatesAFullChildWorktreeWithOrca(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	wantLine(t, h.launched(), readyLine(h))
	create := h.orcaCalls("worktree create")[0]
	if flag(create, "--name") != "v3-lane-a-1-1" || flag(create, "--repo") != "id:repo-1" || flag(create, "--parent-worktree") != "path:"+h.parent || flag(create, "--base-branch") != "origin/dev" {
		t.Fatal(create)
	}
	wantNoCalls(t, h, "worktree set")
	if h.gitIn(h.main, "branch", "--list", testBranch) != "" {
		t.Fatal("a branch exists")
	}
	wantLine(t, h.readReceipt("lane-a.names"), "v3-lane-a-1-1\n")
	if !strings.HasSuffix(h.spec(), "its Escalate rules hold.") {
		t.Fatal(h.spec())
	}
}

func TestADefaultLaunchExpectsItsWorktreeBesideItsParentWhateverTheCCXPool(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	delete(h.env, "ORCA_LAUNCH_ROOT")
	h.gitIn(h.main, "config", "ccx.worktreeRoot", filepath.Join(h.root, "pool"))
	if err := os.MkdirAll(h.worktree(), 0o750); err != nil {
		t.Fatal(err)
	}
	fresh := filepath.Join(filepath.Dir(h.parent), "v3-lane-a-1-2")
	h.reply("worktree create", reply{out: object{"ok": true, "result": object{"worktree": object{"path": fresh}}}, mkdir: fresh})
	h.launched()
	if got := flag(h.orcaCalls("worktree create")[0], "--name"); got != "v3-lane-a-1-2" || exists(filepath.Join(h.root, "pool")) {
		t.Fatal(got)
	}
}

func TestACreatedWorktreesStartupShellIsClosedBeforeTheAgentTerminalOpens(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.reply("terminal list", startup, listing(agentClaude))
	h.launched()
	closes := h.orcaCalls("terminal close")
	if len(closes) != 1 || !slices.Equal(closes[0], []string{"terminal", "close", "--terminal", "term_shell", "--tab", "--json"}) {
		t.Fatal(closes)
	}
	steps := h.steps()
	if slices.Index(steps, "terminal close") >= slices.Index(steps, "terminal create") {
		t.Fatal(steps)
	}
}

func TestACodexLanesCreatedWorktreeHasItsStartupShellClosed(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.reply("terminal list", startup, listing(agentClaude))
	h.launchedAs(agentCodex, "high")
	closes := h.orcaCalls("terminal close")
	if len(closes) != 1 || flag(closes[0], "--terminal") != "term_shell" {
		t.Fatal(closes)
	}
}

func TestAFailedStartupShellCloseStillLaunches(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.reply("terminal list", startup, listing(agentClaude))
	h.reply("terminal close", reply{rc: 1, out: object{"ok": false, "error": object{"code": "not_found"}}})
	h.launched()
	if !strings.Contains(h.readReceipt("lane-a.startup.json"), "not_found") {
		t.Fatal(h.readReceipt("lane-a.startup.json"))
	}
}

func TestAWorktreeWithASetupTerminalKeepsEveryTerminal(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.reply("worktree create", reply{out: object{"ok": true, "result": object{"worktree": object{"path": h.worktree()}, "setupReceipt": object{"state": "running", "terminalHandle": "term_shell"}}}, mkdir: h.worktree()})
	h.reply("terminal list", startup, listing(agentClaude))
	h.launched()
	wantNoCalls(t, h, "terminal close")
}

func TestARelaunchIntoAnExistingWorktreeClosesNothing(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.checkOut()
	h.reply("terminal list", startup, listing(agentClaude, "term_shell", "term_a"))
	h.launched()
	wantNoCalls(t, h, "worktree create", "terminal close")
}

func TestACreateWhoseResponseWasLostClosesNothing(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.reply("worktree show", missing, h.registeredReply())
	h.reply("worktree create", reply{rc: 1, out: "Error: socket hang up", sh: h.made(false)})
	h.reply("terminal list", startup, listing(agentClaude))
	h.launched()
	wantNoCalls(t, h, "terminal close")
}

func TestACreateWhoseWaitOutlastsTheStallLimitFailsWithLaunchFailedNamingTheWait(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_STALL_SECONDS"] = "0"
	h.reply("worktree create", reply{rc: 1, out: "connection lost"})
	line, code := h.launch()
	if code != 1 || !strings.HasPrefix(line, "lane-a failed LAUNCH-FAILED: Orca has not registered worktree v3-lane-a-1-2 for 0s") || !strings.HasSuffix(line, "; this was its one retry") {
		t.Fatal(line)
	}
	if creates := h.orcaCalls("worktree create"); len(creates) != 2 || flag(creates[0], "--name") != "v3-lane-a-1-1" {
		t.Fatal(creates)
	}
	wantNoCalls(t, h, "terminal create")
}

func TestADroppedWorktreeCreateThatNeverRegistersIsCreatedAgainAfterTheWait(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "8"
	h.reply("worktree create", reply{rc: 1, out: "connection lost"}, reply{out: object{"ok": true, "result": object{"worktree": object{"path": h.worktree()}}}, mkdir: h.worktree()})
	h.launched()
	creates := h.orcaCalls("worktree create")
	if len(creates) != 2 || flag(creates[1], "--name") != "v3-lane-a-1-1" || !slices.Equal(h.sleeps[:2], []time.Duration{startPoll, startPoll}) {
		t.Fatal(creates)
	}
}

func TestAWorktreeCreateThatLostItsResponseIsAwaitedNotRepeated(t *testing.T) {
	for _, lost := range []reply{
		{rc: 1, out: object{"ok": false, "error": object{"code": "runtime_unavailable", "message": "The Orca runtime closed the connection before responding."}}},
		{out: "Error: socket hang up"},
	} {
		h := newFullHarness(t)
		h.healthyClaude()
		lost.mkdir = h.worktree()
		h.reply("worktree create", lost)
		h.reply("worktree show", missing, missing, missing, h.registeredReply())
		if line := h.launched(); !strings.HasSuffix(line, "worktree="+h.worktree()) {
			t.Fatal(line)
		}
		if len(h.orcaCalls("worktree create")) != 1 || !slices.Equal(h.sleeps[:2], []time.Duration{startPoll, startPoll}) {
			t.Fatal(h.calls)
		}
	}
}

func TestAWorktreeOrcaMadeElsewhereAfterItsCreateTimedOutIsFoundByItsName(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	elsewhere := filepath.Join(h.root, "orca-workspaces", "monorepo", "v3-lane-a-1-1")
	h.reply("worktree create", reply{rc: 1, out: object{"ok": false, "error": object{"code": "runtime_timeout", "message": "Timed out waiting for the Orca runtime to respond."}}, mkdir: elsewhere})
	if line := h.launched(); !strings.HasSuffix(line, "worktree="+elsewhere) {
		t.Fatal(line)
	}
	if got := flag(h.orcaCalls("terminal create")[0], "--worktree"); got != "path:"+elsewhere || len(h.orcaCalls("worktree create")) != 1 {
		t.Fatal(got)
	}
}

func TestAWorktreeWhoseCreateLostItsResponseWaitsForOrcaToFillItsIndex(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.reply("worktree show", missing, h.registeredReply())
	h.reply("worktree create", reply{rc: 1, out: object{"ok": false, "error": object{"code": "runtime_unavailable", "message": "closed"}}, sh: h.made(true)})
	h.onSleep = func() { h.gitIn(h.worktree(), "read-tree", "HEAD") }
	h.launched()
	if h.sleeps[0] != startPoll || len(h.orcaCalls("terminal create")) != 1 {
		t.Fatal(h.sleeps)
	}
}

func TestAWorktreeWhoseIndexNeverFillsFailsBeforeAnyTerminalOpens(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "4"
	h.reply("worktree show", missing, h.registeredReply())
	h.reply("worktree create", reply{rc: 1, out: object{"ok": false, "error": object{"code": "runtime_unavailable", "message": "closed"}}, sh: h.made(true)})
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wt := h.worktree()
	wantLine(t, line, "lane-a failed worktree checkout: "+wt+" registered, but its index still differs from HEAD after 4s; rolled back worktree="+wt)
	wantNoCalls(t, h, "terminal create")
}

func TestALostCreateWhoseGitAddWasUndoneRemovesTheDirectoryItLeft(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "4"
	registeredReply := h.registeredReply()
	h.reply("worktree show", missing, registeredReply, registeredReply, missing)
	h.reply("worktree create", reply{rc: 1, out: object{"ok": false, "error": object{"code": "runtime_unavailable", "message": "closed"}}, sh: h.made(true)})
	h.onSleep = func() {
		if err := os.RemoveAll(filepath.Join(h.worktree(), ".git")); err != nil {
			t.Error(err)
		}
	}
	line, code := h.launch()
	if code != 1 || !strings.HasSuffix(line, "; rolled back worktree="+h.worktree()) {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "worktree rm")
	if exists(h.worktree()) || exists(filepath.Join(h.receipts, "lane-a.worktree")) {
		t.Fatal("the rollback left the directory or its receipt")
	}
}

func TestAWorktreeThatNeverRegistersFailsTheLaunchAfterFourCreates(t *testing.T) {
	h := newFullHarness(t)
	h.healthyClaude()
	h.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "4"
	h.reply("worktree create", reply{rc: 1, out: "runtime_unavailable"})
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed worktree create: runtime_unavailable")
	if len(h.orcaCalls("worktree create")) != worktreeCreates {
		t.Fatal(h.calls)
	}
	wantNoCalls(t, h, "terminal create")
}

func TestARelaunchReusesTheWorktreeOrcaCreated(t *testing.T) {
	h := newFullHarness(t)
	elsewhere := filepath.Join(h.root, "elsewhere", "lane-a")
	h.healthyClaude()
	h.reply("worktree create", reply{out: object{"ok": true, "result": object{"worktree": object{"path": elsewhere}}}, mkdir: elsewhere})
	if line := h.launched(); !strings.HasSuffix(line, "worktree="+elsewhere) {
		t.Fatal(line)
	}
	h.healthyClaude()
	h.launched()
	if len(h.orcaCalls("worktree create")) != 1 || flag(h.orcaCalls("terminal create")[1], "--worktree") != "path:"+elsewhere {
		t.Fatal(h.calls)
	}
}

func TestADefaultLaunchThatFailsBeforeWorkerStartRemovesItsWorktreeWithOrca(t *testing.T) {
	h := newFullHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	if _, code := h.launch(); code != 1 {
		t.Fatal("the launch succeeded")
	}
	closes := h.orcaCalls("terminal close")
	if !slices.Equal(closes[len(closes)-1], []string{"terminal", "close", "--terminal", "term_a", "--tab", "--json"}) {
		t.Fatal(closes)
	}
	removes := h.orcaCalls("worktree rm")
	if len(removes) != 1 || !slices.Equal(removes[0], []string{"worktree", "rm", "--worktree", "path:" + h.worktree(), "--force", "--json"}) {
		t.Fatal(removes)
	}
	wantNoCalls(t, h, "ccx vcs")
	if exists(filepath.Join(h.receipts, "lane-a.worktree")) {
		t.Fatal("the receipt survived")
	}
	steps := h.steps()
	if !slices.Equal(steps[len(steps)-2:], []string{"terminal close", "worktree rm"}) {
		t.Fatal(steps)
	}
}

func TestARollbackOrcaRefusesIsNamedInTheFailure(t *testing.T) {
	h := newFullHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.reply("worktree rm", reply{rc: 1, out: object{"ok": false, "error": object{"code": "worktree_locked"}}})
	line, _ := h.launch()
	if !strings.HasSuffix(line, "; rolled back terminal=term_a; rollback left worktree="+h.worktree()+" (its removal failed: "+filepath.Join(h.receipts, "lane-a.rm.log")+")") {
		t.Fatal(line)
	}
	wantLine(t, strings.TrimSpace(h.readReceipt("lane-a.worktree")), h.worktree())
}

func TestAWorktreeThatWasOnlyBrieflyUnregisteredIsNeverRemoved(t *testing.T) {
	h := newFullHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.env["ORCA_LAUNCH_WORKTREE_SECONDS"] = "8"
	h.reply("worktree show", missing, h.registeredReply())
	h.reply("worktree create", reply{rc: 1, out: object{"ok": false, "error": object{"code": "worktree_exists"}}, sh: h.made(false)})
	line, _ := h.launch()
	if !strings.HasSuffix(line, "; rolled back terminal=term_a") {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "worktree rm")
}

func TestLaunchPointsAtTheAbsoluteBrief(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	t.Chdir(h.root)
	if line, code := h.launchAs("lane-a", "opus", "high", "specs/lane-a.full.md"); code != 0 {
		t.Fatal(line)
	}
	if !strings.Contains(h.spec(), "read "+h.brief+" in full") {
		t.Fatal(h.spec())
	}
}

func TestLaunchRejectsAnUnknownEffortInOneLine(t *testing.T) {
	h := newHarness(t)
	line, code := h.launchAs("lane-a", "opus", "extreme", h.brief)
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed effort extreme unknown: use low, medium, high, xhigh, or max")
	if len(h.calls) != 0 {
		t.Fatal(h.calls)
	}
}

func TestLaunchRejectsAnUnknownModelInOneLine(t *testing.T) {
	h := newHarness(t)
	line, code := h.launchAs("lane-a", "gemini", "xhigh", h.brief)
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed model gemini unknown: use opus, sonnet, fable, claude-*, sol, codex, incident, incident-opus, astra, or gpt-*")
	if len(h.calls) != 0 {
		t.Fatal(h.calls)
	}
}

func TestAnAstraLaneStartsGPT6AstraInItsOwnCodexTerminal(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.writeFile(h.brief, "# brief\nOwner pick: owner:m836\n")
	h.launchedAs("astra", "xhigh")
	if command := flag(h.orcaCalls("terminal create")[0], "--command"); !strings.Contains(command, " -c model=gpt-6-astra ") {
		t.Fatal(command)
	}
}

func TestAnIncidentOpusLaneRunsClaudeOpusInFastMode(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launchedAs(modelIncidentOpus, "xhigh")
	command := flag(h.orcaCalls("terminal create")[0], "--command")
	if !strings.HasSuffix(command, " --model claude-opus-5-5 --effort xhigh --settings '{"+claudeLaneSettings+","+claudeFastSettings+"}'") {
		t.Fatal(command)
	}
}

func TestNoClaudeAliasButIncidentOpusTurnsOnFastMode(t *testing.T) {
	for _, model := range []string{"opus", "sonnet", "fable", "claude-opus-5-5"} {
		h := newHarness(t)
		h.healthyClaude()
		h.writeFile(h.brief, "# brief\nOwner pick: owner:m836\n")
		h.launchedAs(model, "xhigh")
		if command := flag(h.orcaCalls("terminal create")[0], "--command"); strings.Contains(command, "fastMode") {
			t.Fatalf("%s: %s", model, command)
		}
	}
}

func TestAnIncidentOpusLaneIsPinnedToTheMacAndPassesTheModelCheck(t *testing.T) {
	if !macPinned("api-fix-opus", modelIncidentOpus, "") {
		t.Fatal("incident-opus can leave the Mac")
	}
	order := Order{Lane: "api-fix-opus", Model: modelIncidentOpus, Effort: "xhigh", Brief: "/b.md"}
	if faults := order.launchFaults("Register slice: lane=api-fix-opus step=all\n", true); len(faults) != 0 {
		t.Fatal(faults)
	}
}

func TestALaunchThatFailsBeforeWorkerStartClosesItsTabAndRemovesTheWorktreeItCreated(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	if _, code := h.launch(); code != 1 {
		t.Fatal("the launch succeeded")
	}
	closes := h.orcaCalls("terminal close")
	if !slices.Equal(closes[len(closes)-1], []string{"terminal", "close", "--terminal", "term_a", "--tab", "--json"}) {
		t.Fatal(closes)
	}
	removes := h.orcaCalls("ccx vcs")
	if len(removes) != 1 || !slices.Equal(removes[0], []string{"ccx", "vcs", "worktree", "rm", "--path", h.worktree(), "--force"}) {
		t.Fatal(removes)
	}
	if exists(filepath.Join(h.receipts, "lane-a.worktree")) || exists(h.worktree()) || h.gitIn(h.main, "branch", "--list", testBranch) != "" {
		t.Fatal("the rollback left work behind")
	}
	steps := h.steps()
	if !slices.Equal(steps[len(steps)-2:], []string{"terminal close", "ccx vcs"}) {
		t.Fatal(steps)
	}
}

func TestARelaunchThatFailsKeepsTheWorktreeItFound(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.checkOut()
	line, _ := h.launch()
	if !strings.HasSuffix(line, "; rolled back terminal=term_a") {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "worktree set", "ccx vcs")
}

func TestARollbackCCXRefusesIsNamedInTheFailure(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.ccxRefuses = true
	line, _ := h.launch()
	if !strings.Contains(line, "; rolled back terminal=term_a; rollback left worktree="+h.worktree()+" (its removal failed: "+filepath.Join(h.receipts, "lane-a.rm.log")+") branch="+testBranch+" (") {
		t.Fatal(line)
	}
	wantLine(t, strings.TrimSpace(h.readReceipt("lane-a.worktree")), h.worktree())
}

func TestAQueuedWorktreeRemovalIsNamedAndKeepsItsBranch(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.ccxQueues = true
	line, _ := h.launch()
	if !strings.HasSuffix(line, "; rolled back terminal=term_a; rollback left worktree="+h.worktree()+" (its removal is queued: "+filepath.Join(h.receipts, "lane-a.rm.log")+") branch="+testBranch+" (its worktree's removal is queued)") {
		t.Fatal(line)
	}
	if h.gitIn(h.main, "branch", "--list", testBranch) == "" {
		t.Fatal("the branch under a queued removal was deleted")
	}
	if exists(filepath.Join(h.receipts, "lane-a.worktree")) {
		t.Fatal("a relaunch would resume in a tree queued for removal")
	}
}

func TestARefusedTerminalCreateFailsAtOnceWithOrcasMessage(t *testing.T) {
	const refusal = "The host is already running as many terminals as Orca allows. Close a terminal you no longer need, then try again."
	h := newHarness(t)
	h.noAgent()
	h.reply("terminal create", reply{rc: 1, out: object{"ok": false, "error": object{"code": "runtime_error", "message": refusal}}})
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed terminal create: runtime_error: "+refusal+"; rolled back worktree="+h.worktree()+" branch="+testBranch)
	if creates := h.orcaCalls("terminal create"); len(creates) != 1 {
		t.Fatal(creates)
	}
	if len(h.sleeps) != 0 || strings.Contains(h.stderr.String(), "retries once") {
		t.Fatalf("sleeps %v, stderr %s", h.sleeps, h.stderr.String())
	}
}

func TestNothingIsRolledBackOnceWorkerStartHasRun(t *testing.T) {
	for _, options := range []healthyOptions{{state: statusFailed, agent: agentClaude}, {agent: agentClaude, screen: "plan mode on"}} {
		h := newHarness(t)
		h.healthy(options)
		if _, code := h.launch(); code != 1 {
			t.Fatal("the launch succeeded")
		}
		wantNoCalls(t, h, "terminal close", "ccx vcs")
	}
}

func TestAnAdoptedTerminalIsNamedNotClosed(t *testing.T) {
	h := newHarness(t)
	h.noAgent()
	h.env["ORCA_LAUNCH_BOOT_SECONDS"] = "4"
	h.reply("terminal create", timedOut)
	h.reply("terminal list", bareListing(), listing(nil))
	line, _ := h.launch()
	if !strings.HasSuffix(line, "; rolled back worktree="+h.worktree()+" branch="+testBranch+"; rollback left terminal=term_a") {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "terminal close")
}

func (h *harness) rebindLine() string {
	return "post the launch to the drive's orca runner, whose terminal coordinates the Run: desk-launch.py lane lane-a opus high " + h.brief + "; a drive with no runner rebinds from its coordinator's Orca terminal: orca orchestration run-use --id run_1"
}

func TestATerminalThatDoesNotCoordinateTheRunCreatesNothingAndNamesTheRebind(t *testing.T) {
	for _, current := range []object{{"run": nil}, {"run": object{"id": "run_other"}}} {
		h := newHarness(t)
		h.healthyClaude()
		answer := okResult(current)
		h.reply("orchestration run-current", answer)
		line, code := h.launch()
		if code != 1 {
			t.Fatal(line)
		}
		bound := "no Run"
		if current["run"] != nil {
			bound = "run_other"
		}
		wantLine(t, line, "lane-a failed coordinator binding: terminal term_root coordinates "+bound+", not run_1 ("+answer.body()+"); "+h.rebindLine())
		if steps := h.steps(); !slices.Equal(steps, []string{"repo show", "orchestration run-current"}) {
			t.Fatal(steps)
		}
		if exists(h.worktree()) {
			t.Fatal("a worktree exists")
		}
	}
}

func TestAFencedWorkerStartRollsBackItsTerminalAndWorktreeAndQuotesOrcasError(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("orchestration worker-start", fencedStart)
	line, code := h.launch()
	if code != 1 {
		t.Fatal(line)
	}
	wantLine(t, line, "lane-a failed worker-start terminal=term_a: consumer_fenced: worker-start requires the coordinator terminal currently bound to the Task Run. Orchestration mutation request ID: req-1.; terminal term_root is not the Run's coordinator: "+h.rebindLine()+"; rolled back terminal=term_a worktree="+h.worktree()+" branch="+testBranch)
	if exists(filepath.Join(h.receipts, "lane-a.json")) || exists(filepath.Join(h.receipts, "lane-a.terminal")) {
		t.Fatal("a receipt exists")
	}
}

func TestAFencedRelaunchClosesItsTerminalAndKeepsTheWorktreeAndReceipt(t *testing.T) {
	h := newHarness(t)
	h.healthy(healthyOptions{state: statusFailed, agent: agentClaude})
	if _, code := h.launch(); code != 1 {
		t.Fatal("the launch succeeded")
	}
	h.healthyClaude()
	h.reply("orchestration worker-start", fencedStart)
	line, _ := h.launch()
	if !strings.HasSuffix(line, "; rolled back terminal=term_a") {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "ccx vcs")
	if !strings.Contains(h.readReceipt("lane-a.json"), "ctx_a") {
		t.Fatal("the receipt lost the dispatch")
	}
}

func TestAWorkerStartThatMayHaveDispatchedNamesWhatItLeft(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("orchestration worker-start", timedOut)
	line, _ := h.launch()
	wantLine(t, line, "lane-a failed worker-start terminal=term_a: runtime_error: Timed out waiting for terminal handle after creation; rollback left terminal=term_a worktree="+h.worktree()+" (worker-start may have dispatched a worker there)")
	wantNoCalls(t, h, "ccx vcs", "terminal close")
}

func TestAnOrcaErrorPrintedAcrossLinesFailsTheLaunchOnOneLine(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("repo show", reply{rc: 1, out: "{\n  \"ok\": false,\n  \"error\": {\n    \"code\": \"runtime_unavailable\",\n    \"message\": \"Could not read Orca runtime metadata. Start the Orca app first.\"\n  }\n}"})
	line, _ := h.launch()
	wantLine(t, line, "lane-a failed repo: orca repo show finds no repo at id:repo-1 (runtime_unavailable: Could not read Orca runtime metadata. Start the Orca app first.); set ORCA_LAUNCH_REPO, or register the main checkout with orca repo add --path")
}

func TestAWorkerStartThatPrintedADispatchBesideARefusalRollsNothingBack(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.reply("orchestration worker-start", reply{rc: 1, out: okResult(object{"state": "starting"}).body() + "\n" + fencedStart.body()})
	line, _ := h.launch()
	if !strings.HasSuffix(line, "; rollback left terminal=term_a worktree="+h.worktree()+" (worker-start may have dispatched a worker there)") {
		t.Fatal(line)
	}
	wantNoCalls(t, h, "ccx vcs")
}

func TestAMacBriefNamesWhyOrTheLaneGoesToASprite(t *testing.T) {
	cases := map[string]bool{
		"ccx: role=build place=local incident=none\n":                    false,
		"ccx: role=build place=local why=orca-terminals incident=none\n": true,
		"ccx: role=build place=remote\n":                                 false,
		"ccx: role=read place=local incident=none\n":                     true,
		"ccx: role=read place=remote\n":                                  false,
		"Scale: one PR\n":                                                false,
	}
	for brief, pinned := range cases {
		if macPinned("a-lane", "opus", brief) != pinned {
			t.Fatalf("%q pinned = %v", brief, !pinned)
		}
	}
}

func TestALaneKeepsTheTerminalTitleOrcaReadsForItsMail(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.launched()
	command := flag(h.orcaCalls("terminal create")[0], "--command")
	if strings.Contains(command, "CLAUDE_CODE_DISABLE_TERMINAL_TITLE") {
		t.Fatal(command)
	}
}
