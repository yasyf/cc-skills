package orca

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
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

const (
	workerRun       = "run_1"
	workerLane      = "lane-a"
	workerWorkspace = "pool-spare"
	macGitLogin     = "a-github-login\n"
	remoteTerminal  = "term_remote_1"
	remoteRoot      = "/home/sprite/monorepo"
	remoteBase      = "1111111111111111111111111111111111111111"
	workerBrief     = "# Lane A\n\nWrite the patch and the report beside this brief.\n"
	workerPin       = "9.8.7"
	testCCIURL      = "https://example.test/cci_linux_amd64.tar.gz"
	testCCISHA      = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
	testLRURL       = "https://github.com/yasyf/cc-skills/releases/download/lr--v9.9.9/lr_9.9.9_linux_amd64.tar.gz"
	testLRSHA       = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
	localWorktree   = "/w/lane-a"
	localTerminal   = "term_local_1"
	sponsorBox      = "sponsor-1"
	relayURL        = "https://relay.example.test/"
	ownerKey        = "BOwner=="
	localRepo       = "repo-local"
	stoppedWorker   = "orca orchestration worker-start exited 0 without a ready receipt for Run %s; kept terminal %s, worktree %s, and the receipt in %s"
)

var (
	workerSHA      = hexSum(workerBrief)
	workerBytes    = len(workerBrief)
	versionCall    = []string{"cc-remote", "version"}
	currentRunCall = []string{"orca", "orchestration", "run-current", "--full", "--json"}
	boundWorker    = workerReply{out: object{"ok": true, "result": object{"run": object{"id": workerRun, "coordinator_handle": "term_coordinator"}}}}
	startedReceipt = object{"ok": true, "result": object{"runId": workerRun, "taskId": "task_a", "dispatchId": "ctx_a", "state": "ready", "stage": "remote_input_accepted"}}
	allocationKept = []string{"allocation.json", "before-worker.sh", "brief.md", "prepare.err", "prepare.status", "request.json"}
	prepareKept    = sorted(allocationKept, "prepared.json")
	startKept      = []string{"worker-start.err", "worker-start.json", "worker-start.status"}
	spriteKept     = sorted(prepareKept, slices.Concat(startKept, []string{"install.log", notesReceipt, joinReceipt})...)
	relayFailure   = "cci: relay: 500 internal: the relay failed; retry\n"
	localKept      = sorted(startKept, "request.json")
)

func sorted(base []string, more ...string) []string {
	joined := slices.Concat(base, more)
	slices.Sort(joined)
	return joined
}

func lrPin(platform string) string {
	return "#!/usr/bin/env binrun\n" + rendered(object{"schema": 1, "name": "lr", "kind": "release-binary", "platforms": object{platform: object{
		"hash":      "sha256",
		"digest":    testLRSHA,
		"format":    "tar.gz",
		"path":      "lr",
		"providers": []object{{"type": "github-release", "repo": "yasyf/cc-skills", "tag": "lr--v9.9.9", "name": "lr_9.9.9_linux_amd64.tar.gz"}},
	}}})
}

func hexSum(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

func prepared() object {
	return object{
		"schemaVersion": 1,
		"workspace":     workerWorkspace,
		"provider":      "sprites",
		"profile":       "agents",
		"machine":       workerWorkspace,
		"projectRoot":   remoteRoot,
		"environment":   workerWorkspace,
		"environmentId": "env-1",
		"runtimeId":     "rt-1",
		"managedServer": "0.1.0+15ec2904eb00",
		"repoId":        "repo-1",
		"worktreeId":    "repo-1::" + remoteRoot,
		"agent":         object{"kind": "claude", "model": "claude-opus-5-5", "effort": "xhigh"},
		"terminal":      remoteTerminal,
		"bootstrap":     []string{"trust"},
		"prepared":      true,
		"brief":         object{"path": "/home/sprite/.cc-remote/orca/tasks/" + workerWorkspace + "/brief.md", "sha256": workerSHA, "bytes": workerBytes},
		"baseCommit":    remoteBase,
	}
}

func overlaid(base object, fields object) object {
	merged := maps.Clone(base)
	maps.Copy(merged, fields)
	return merged
}

func without(base object, key string) object {
	merged := maps.Clone(base)
	delete(merged, key)
	return merged
}

func allocationOf(task object, fields object) object {
	return overlaid(object{
		"schemaVersion": 1,
		"lane":          workerLane,
		"workspace":     workerWorkspace,
		"allocation":    "warm",
		"ref":           "dev",
		"head":          task["baseCommit"],
		"replenish":     object{"target": 1, "pid": 123},
		"task":          task,
	}, fields)
}

type workerReply struct {
	code     int
	out, err any
}

func rendered(value any) string {
	switch typed := value.(type) {
	case nil:
		return ""
	case string:
		return typed
	}
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return string(raw)
}

type workerFixture struct {
	t        *testing.T
	root     string
	checkout string
	cli      string
	config   string
	payload  string
	brief    string
	attempt  string
	home     string
	box      string
	env      map[string]string
	replies  map[string][]workerReply
	calls    [][]string
	stdins   map[string]string
	slept    []time.Duration
	configs  []string
	stderr   bytes.Buffer
}

func newWorkerFixture(t *testing.T) *workerFixture {
	t.Helper()
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	f := &workerFixture{
		t:        t,
		root:     root,
		checkout: filepath.Join(root, "checkout"),
		home:     filepath.Join(root, "home"),
		box:      filepath.Join(root, "box"),
		replies:  map[string][]workerReply{},
		stdins:   map[string]string{},
	}
	tools := filepath.Join(f.checkout, "tools", "cc-remote")
	f.cli = filepath.Join(tools, "bin", "cc-remote")
	f.config = filepath.Join(tools, "config.yaml")
	f.payload = filepath.Join(tools, "agents-payload.json")
	f.brief = filepath.Join(root, "briefs", workerLane+".md")
	f.attempt = filepath.Join(root, "briefs", workerLane+".worker")
	f.write(f.cli, "#!/bin/sh\n", 0o700)
	f.write(f.config, "provider: sprites\n", 0o600)
	f.write(f.payload, rendered(object{"bootstrapHelper": object{"version": workerPin}}), 0o600)
	f.write(f.brief, workerBrief, 0o600)
	f.write(filepath.Join(root, "plugin", "bin", "lr.binrun"), lrPin("linux-x86_64"), 0o700)
	if err := os.MkdirAll(f.home, 0o750); err != nil {
		t.Fatal(err)
	}
	f.env = map[string]string{
		"WORKER_RUN":           workerRun,
		"WORKER_CHECKOUT":      f.checkout,
		"WORKER_CCI_URL":       testCCIURL,
		"WORKER_CCI_SHA256":    testCCISHA,
		"ORCA_TERMINAL_HANDLE": "term_coordinator",
		"BINRUN_PLUGIN_ROOT":   filepath.Join(root, "plugin"),
		"HOME":                 f.home,
		sponsorEnv:             sponsorBox,
	}
	return f
}

func (f *workerFixture) write(path, text string, mode os.FileMode) {
	f.t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		f.t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), mode); err != nil {
		f.t.Fatal(err)
	}
}

func (f *workerFixture) reply(command string, replies ...workerReply) {
	f.replies[command] = replies
}

func (f *workerFixture) sprite() {
	f.spriteWith(prepared())
}

func (f *workerFixture) spriteWith(task any) {
	if fields, isObject := task.(object); isObject {
		task = allocationOf(fields, nil)
	}
	f.spriteAllocated(task)
}

func (f *workerFixture) spriteAllocated(response any) {
	f.reply("gh auth token", workerReply{out: macGitLogin})
	f.reply("cc-remote version", workerReply{err: workerPin + "\n"})
	f.reply("orca orchestration run-current", boundWorker)
	f.reply("cc-remote orca prepare", workerReply{out: response, err: "prepared on sprites\n"})
	f.reply("orca orchestration worker-start", workerReply{out: startedReceipt})
	f.reply("cci relay status", workerReply{out: []object{{"mesh": "other", "url": "https://elsewhere.test/", "routes": []string{"x"}}, {"mesh": "default", "url": relayURL, "routes": []string{"drive-a", "drive-b"}}}})
	f.reply("cci node", workerReply{out: object{"id": "n1", "public_key": ownerKey}})
	f.reply("sprite exec", workerReply{}, workerReply{}, workerReply{}, workerReply{}, workerReply{})
}

func (f *workerFixture) local() {
	f.env["WORKER_LOCAL"] = "1"
	f.env["WORKER_REPO"] = localRepo
	f.reply("orca orchestration run-current", boundWorker)
	f.reply("orca worktree list", workerReply{out: object{"ok": true, "result": object{"worktrees": []object{}}}})
	f.reply("orca worktree create", workerReply{out: object{"ok": true, "result": object{"worktree": object{"path": localWorktree}}}})
	f.reply("orca terminal list", workerReply{out: object{"ok": true, "result": object{"terminals": []object{{"handle": localTerminal, "agentIdentity": nil}}}}})
	f.reply("orca terminal send", workerReply{out: object{"ok": true}})
	f.reply("orca terminal show", workerReply{out: object{"ok": true, "result": object{"terminal": object{"agentIdentity": "claude"}}}})
	f.reply("orca terminal wait", workerReply{out: object{"ok": true}})
	f.reply("orca orchestration worker-start", workerReply{out: object{"ok": true, "result": object{"runId": workerRun, "state": "ready", "taskId": "task_l", "dispatchId": "ctx_l"}}})
}

func (f *workerFixture) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	name := filepath.Base(c.Argv[0])
	if name == "tar" {
		return shell.Exec{}.Run(ctx, c)
	}
	argv := slices.Concat([]string{name}, c.Argv[1:])
	f.calls = append(f.calls, argv)
	words := []string{name}
	for _, arg := range c.Argv[1:min(3, len(c.Argv))] {
		if !strings.HasPrefix(arg, "-") {
			words = append(words, arg)
		}
	}
	key := strings.Join(words, " ")
	if name == "cc-remote" {
		f.configs = append(f.configs, c.Env...)
	}
	if name == "sprite" && c.Stdin != "" {
		f.stdins[strings.Join(argv, " ")] = c.Stdin
		if key == "sprite exec" && f.box != "" && strings.Contains(strings.Join(argv, " "), "tar -xzf") {
			if result, err := f.unpack(ctx, c); err != nil {
				return result, err
			}
		}
	}
	queue := f.replies[key]
	current := workerReply{code: 1}
	if len(queue) > 0 {
		current = queue[0]
		f.replies[key] = queue[1:]
	}
	result := shell.Result{Code: current.code, Stdout: rendered(current.out), Stderr: rendered(current.err)}
	if current.code != 0 {
		return result, &shell.ExitError{Argv: c.Argv, Code: current.code, Stderr: result.Stderr}
	}
	return result, nil
}

func (f *workerFixture) unpack(ctx context.Context, c shell.Command) (shell.Result, error) {
	root := strings.Replace(c.Argv[len(c.Argv)-1], "/home/sprite", f.box, 1)
	return shell.Exec{}.Run(ctx, shell.Command{Argv: slices.Concat(c.Argv[slices.Index(c.Argv, "sh"):len(c.Argv)-1], []string{root}), Stdin: c.Stdin})
}

func (f *workerFixture) launch(lane, model, effort string) (string, string, error) {
	f.t.Helper()
	starter := &WorkerStarter{
		Runner: f,
		Env:    func(name string) string { return f.env[name] },
		Sleep:  func(wait time.Duration) { f.slept = append(f.slept, wait) },
		Stderr: &f.stderr,
	}
	line, err := starter.Run(context.Background(), lane, model, effort, f.brief)
	var failed *Failed
	if errors.As(err, &failed) {
		return "", failed.Line, nil
	}
	return line, "", err
}

func (f *workerFixture) launched() string {
	f.t.Helper()
	line, failed, err := f.launch(workerLane, "opus", "xhigh")
	if err != nil || failed != "" {
		f.t.Fatal(failed, err)
	}
	return line
}

func (f *workerFixture) refusedWith(failed string, calls ...[]string) {
	f.t.Helper()
	line, got, err := f.launch(workerLane, "opus", "xhigh")
	if err != nil || line != "" || got != failed {
		f.t.Fatalf("line %q, err %v, failed\n%s\nwant\n%s", line, err, got, failed)
	}
	f.wantCalls(calls...)
}

func (f *workerFixture) wantCalls(calls ...[]string) {
	f.t.Helper()
	if len(calls) == 0 && len(f.calls) == 0 {
		return
	}
	if !reflect.DeepEqual(f.calls, calls) {
		f.t.Fatalf("calls\n%v\nwant\n%v", f.calls, calls)
	}
}

func (f *workerFixture) wantNoAttempt() {
	f.t.Helper()
	if present(f.attempt) {
		f.t.Fatal("an attempt exists")
	}
}

func (f *workerFixture) kept() []string {
	f.t.Helper()
	entries, err := os.ReadDir(f.attempt)
	if err != nil {
		f.t.Fatal(err)
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	return names
}

func (f *workerFixture) receiptText(name string) string {
	f.t.Helper()
	raw, err := os.ReadFile(filepath.Join(f.attempt, name))
	if err != nil {
		f.t.Fatal(err)
	}
	return string(raw)
}

func (f *workerFixture) prepareCall(extra ...string) []string {
	return f.prepareCallFor(workerLane, "claude", "claude-opus-5-5", "xhigh", extra...)
}

func (f *workerFixture) prepareCallFor(lane, kind, model, effort string, extra ...string) []string {
	return slices.Concat([]string{"cc-remote", "orca", "prepare", lane, "--warm", "--provider", "sprites", "--profile", "agents", "--agent", kind, "--model", model, "--effort", effort, "--brief-file", filepath.Join(f.attempt, "brief.md"), "--before-worker-file", filepath.Join(f.attempt, "before-worker.sh"), "--env", "CLAUDE_LONG_RUNNING_LANE=" + lane}, extra)
}

func notesCall(workspace string) []string {
	return []string{"sprite", "exec", "-s", workspace, "--", "sh", "-c", fetchNotesScript, "sh", remoteRoot, notesRefs}
}

func joinCall(workspace string) []string {
	return []string{"sprite", "exec", "-s", workspace, "--", "sh", "-c", joinRelay, "sh", "drive-a,drive-b", relayURL, ownerKey}
}

func holdCall(workspace string) []string {
	return []string{"sprite", "exec", "-s", workspace, "--no-stdin", "--", "sh", "-c", holdWorker}
}

func relayCalls(workspace string) [][]string {
	return [][]string{{"cci", "relay", "status", "--json"}, {"cci", "node", "--json"}, {"sprite", "exec", "-s", sponsorBox, "--no-stdin", "--", "cat", sponsorKey}, joinCall(workspace)}
}

func (f *workerFixture) startCalls(task object) [][]string {
	brief := task["brief"].(object)
	spec := fmt.Sprintf("Lane %s: read the whole brief at %s, verify its SHA-256 is %s and its size is %d bytes, then follow it exactly.%s", workerLane, brief["path"], brief["sha256"], brief["bytes"], spriteBuildkite)
	return slices.Concat([][]string{{"gh", "auth", "token"}, notesCall(task["environment"].(string))}, relayCalls(task["environment"].(string)), [][]string{
		slices.Concat([]string{"orca", "orchestration", "worker-start", "--run", workerRun, "--spec", spec, "--task-title", workerLane, "--on", task["environment"].(string), "--worktree", "id:" + task["worktreeId"].(string), "--terminal", task["terminal"].(string)}, []string{"--timeout-ms", "600000", "--full", "--json"}),
	})
}

func (f *workerFixture) fullSpriteCalls() [][]string {
	return slices.Concat([][]string{versionCall, currentRunCall, f.prepareCall()}, f.startCalls(prepared()))
}

func (f *workerFixture) startedSpec() string {
	started := slices.IndexFunc(f.calls, func(call []string) bool { return slices.Contains(call, "--spec") })
	return flag(f.calls[started], "--spec")
}

func (f *workerFixture) heldSpriteCalls() [][]string {
	return append(f.fullSpriteCalls(), holdCall(workerWorkspace))
}

func (f *workerFixture) localCalls(model, effort, repo string) [][]string {
	claude := "env " + claudeLaneEnv + " claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools " + laneDeniedTools + " --strict-mcp-config --no-chrome --model " + model + " --effort " + effort + " --settings '{" + claudeLaneSettings + "}'"
	pointer := "Lane '" + workerLane + "'. Your full brief is the file " + f.brief + ": read it in full with the Read tool before anything else and execute it exactly (Orca truncates pasted specs). Your Orca worktree is " + localWorktree + ". You run in bypass-permissions mode; the brief's Escalate rules still hold."
	return [][]string{
		currentRunCall,
		{"orca", "worktree", "list", "--repo", "id:" + repo, "--limit", "10000", "--json"},
		{"orca", "worktree", "create", "--name", workerLane, "--repo", "id:" + repo, "--base-branch", "origin/dev", "--setup", "run", "--json"},
		{"orca", "terminal", "list", "--worktree", "path:" + localWorktree, "--json"},
		{"orca", "terminal", "send", "--terminal", localTerminal, "--enter", "--text", claude, "--json"},
		{"orca", "terminal", "show", "--terminal", localTerminal, "--json"},
		{"orca", "terminal", "wait", "--terminal", localTerminal, "--for", "tui-idle", "--timeout-ms", "120000", "--json"},
		{"orca", "orchestration", "worker-start", "--run", workerRun, "--spec", pointer, "--task-title", workerLane, "--worktree", "path:" + localWorktree, "--terminal", localTerminal, "--timeout-ms", "600000", "--full", "--json"},
	}
}

func TestAMissingRunOrBriefRefusesBeforeAnyCall(t *testing.T) {
	f := newWorkerFixture(t)
	delete(f.env, "WORKER_RUN")
	f.refusedWith("set WORKER_RUN to the Orca Run id")
	f = newWorkerFixture(t)
	if err := os.Remove(f.brief); err != nil {
		t.Fatal(err)
	}
	f.refusedWith("no brief at " + f.brief)
	f.wantNoAttempt()
}

func TestEachLaunchThatCannotBeRoutedRefusesBeforeAnyCall(t *testing.T) {
	cases := []struct {
		name, model string
		env         map[string]string
		want        string
	}{
		{"haiku", "haiku", nil, "model haiku is neither a claude- nor a gpt- model, so neither claude nor codex runs it; nothing launched"},
		{"gemini", "gemini-3-pro", nil, "model gemini-3-pro is neither a claude- nor a gpt- model, so neither claude nor codex runs it; nothing launched"},
		{"tier on claude", "opus", map[string]string{"WORKER_SERVICE_TIER": "fast"}, "WORKER_SERVICE_TIER is a codex setting, and claude runs claude-opus-5-5; nothing launched"},
		{"fast outside an incident", "gpt-6.1-sol", map[string]string{"WORKER_SERVICE_TIER": "fast", "WORKER_INCIDENT": "true"}, "WORKER_SERVICE_TIER=fast is for incident lanes only; set WORKER_INCIDENT=1 for an incident lane; nothing launched"},
		{"local true", "opus", map[string]string{"WORKER_LOCAL": "true"}, "WORKER_LOCAL is 1 for the explicit local route or unset for a Sprite; nothing launched"},
		{"repo on sprite", "opus", map[string]string{"WORKER_REPO": "repo-1"}, "WORKER_REPO picks a local Orca repository; a Sprite checks out the repository its cc-remote config names, chosen with CC_REMOTE_CONFIG; nothing was prepared"},
		{"fable alias", "fable", nil, "claude-fable-5-1 is Fable, which runs only on the explicit local route, WORKER_LOCAL=1; nothing was prepared, and nothing launched locally instead"},
		{"fable capital", "claude-Fable-6", nil, "claude-Fable-6 is Fable, which runs only on the explicit local route, WORKER_LOCAL=1; nothing was prepared, and nothing launched locally instead"},
		{"base branch on sprite", "opus", map[string]string{"WORKER_BASE_BRANCH": "origin/dev"}, "WORKER_BASE_BRANCH picks a local worktree's base; a Sprite checks out WORKER_REF, a branch or tag, or the cc-remote config's ref; nothing was prepared"},
		{"short expected base", "opus", map[string]string{"WORKER_EXPECT_BASE": "abc1234"}, "WORKER_EXPECT_BASE is a full 40- or 64-character lowercase commit; nothing was prepared"},
		{"local codex", "gpt-6.1-sol", map[string]string{"WORKER_LOCAL": "1", "WORKER_REPO": localRepo}, "the local route starts only claude, and codex runs gpt-6.1-sol; nothing launched"},
		{"local ref", "fable", map[string]string{"WORKER_LOCAL": "1", "WORKER_REPO": localRepo, "WORKER_REF": "dev"}, "WORKER_REF and WORKER_EXPECT_BASE pick a Sprite's checkout; the local route takes WORKER_BASE_BRANCH; nothing launched"},
		{"local without a repo", "fable", map[string]string{"WORKER_LOCAL": "1"}, "set WORKER_REPO to the Orca repository the local route adds its worktree to; nothing launched"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newWorkerFixture(t)
			f.sprite()
			maps.Copy(f.env, c.env)
			line, failed, err := f.launch(workerLane, c.model, "xhigh")
			if err != nil || line != "" || failed != c.want {
				t.Fatalf("%q %v\n%s", line, err, failed)
			}
			f.wantCalls()
			f.wantNoAttempt()
		})
	}
}

func TestALaneCCRemoteCannotNameRefusesBeforeAnyCall(t *testing.T) {
	for _, lane := range []string{"Lane-a", "lane_a", "lane.a", strings.Repeat("a", 56), "-lane", "lane a", "lane/a", ""} {
		f := newWorkerFixture(t)
		f.sprite()
		_, failed, err := f.launch(lane, "opus", "xhigh")
		if err != nil || failed != "a Sprite lane takes up to 55 lowercase letters, digits, and dashes, starting with a letter or digit; nothing was prepared" {
			t.Fatal(lane, failed, err)
		}
		f.wantCalls()
	}
}

func TestACCRemoteOffTheHelperPinRefusesBeforeAllocation(t *testing.T) {
	cases := []struct {
		reply   workerReply
		printed string
	}{
		{workerReply{err: "9.8.6\n"}, "9.8.6"},
		{workerReply{out: "v" + workerPin + "\n"}, "v" + workerPin},
		{workerReply{err: "cc-remote " + workerPin + "\n"}, "cc-remote " + workerPin},
		{workerReply{}, "nothing"},
	}
	for _, c := range cases {
		f := newWorkerFixture(t)
		f.sprite()
		f.reply("cc-remote version", c.reply)
		f.refusedWith(fmt.Sprintf("%s version printed %s, and %s pins bootstrap helper %s; a Sprite needs the CLI at exactly its helper's release; nothing was prepared", f.cli, c.printed, f.config, workerPin), versionCall)
		f.wantNoAttempt()
	}
}

func TestACCRemoteThatCannotPrintItsVersionRefusesWithItsOwnMessage(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("cc-remote version", workerReply{code: 1, err: "mise ERROR Config files in ~/checkout are not trusted.\n"})
	f.refusedWith(f.cli+" version failed: mise ERROR Config files in ~/checkout are not trusted.; nothing was prepared", versionCall)
}

func TestACheckoutWithoutAHelperPinRefusesBeforeAnyCall(t *testing.T) {
	for _, payload := range []string{"{}", `{"bootstrapHelper": {}}`, `{"bootstrapHelper": {"version": null}}`} {
		f := newWorkerFixture(t)
		f.sprite()
		f.write(f.payload, payload, 0o600)
		f.refusedWith(f.payload + " pins no bootstrap helper version; nothing was prepared")
	}
}

func TestThePinnedCCRemotePrepares(t *testing.T) {
	for _, version := range []workerReply{{err: workerPin + "\n"}, {out: workerPin + "\n"}, {err: "mise cc-remote@" + workerPin + " download\nmise cc-remote@" + workerPin + " installed\n" + workerPin + "\n"}} {
		f := newWorkerFixture(t)
		f.sprite()
		f.reply("cc-remote version", version)
		f.launched()
		f.wantCalls(f.heldSpriteCalls()...)
	}
}

func TestAMissingOrDifferentRunOwnerRefusesBeforePrepare(t *testing.T) {
	cases := []struct {
		reply workerReply
		owner string
	}{
		{workerReply{code: 1}, "no Run"},
		{workerReply{code: 1, out: boundWorker.out}, "no Run"},
		{workerReply{out: object{"ok": false, "error": object{"code": refusalFenced, "message": "fenced"}}}, "no Run"},
		{workerReply{out: object{"ok": true, "result": object{"run": object{"id": "run_2"}}}}, "run_2"},
		{workerReply{out: object{"ok": true, "result": object{}}}, "no Run"},
		{workerReply{out: overlaid(boundWorker.out.(object), object{"error": object{"code": "stale"}})}, "no Run"},
		{workerReply{out: rendered(boundWorker.out) + rendered(boundWorker.out)}, "no Run"},
		{workerReply{out: "not json"}, "no Run"},
	}
	for _, c := range cases {
		f := newWorkerFixture(t)
		f.sprite()
		f.reply("orca orchestration run-current", c.reply)
		f.refusedWith(fmt.Sprintf("terminal term_coordinator coordinates %s, not %s, so nothing launched; launch from the coordinator that already owns Run %s", c.owner, workerRun, workerRun), versionCall, currentRunCall)
		f.wantNoAttempt()
	}
}

func TestAnAttemptThatNeverStartedAWorkerLeavesTheBriefFreeAndKeepsItsReceipts(t *testing.T) {
	for _, shape := range []string{"empty", "failed", "link"} {
		f := newWorkerFixture(t)
		f.sprite()
		if shape == "link" {
			if err := os.Symlink(filepath.Join(filepath.Dir(f.attempt), "gone"), f.attempt); err != nil {
				t.Fatal(err)
			}
		} else if err := os.Mkdir(f.attempt, 0o750); err != nil {
			t.Fatal(err)
		}
		if shape == "failed" {
			f.write(filepath.Join(f.attempt, "prepare.status"), "1\n", 0o600)
		}
		f.launched()
		f.wantCalls(f.heldSpriteCalls()...)
		if kept := f.kept(); !slices.Equal(kept, spriteKept) {
			t.Fatal(kept)
		}
		moved, err := filepath.Glob(f.attempt + ".*")
		if err != nil || len(moved) != 1 {
			t.Fatal(moved, err)
		}
		if shape == "failed" {
			raw, err := os.ReadFile(filepath.Join(moved[0], "prepare.status"))
			if err != nil || string(raw) != "1\n" {
				t.Fatal(string(raw), err)
			}
		}
	}
}

func TestAnAttemptThatReachedWorkerStartRefusesReplay(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.write(filepath.Join(f.attempt, "worker-start.json"), "{}", 0o600)
	f.refusedWith(f.attempt+" holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead", versionCall, currentRunCall)
	if kept := f.kept(); !slices.Equal(kept, []string{"worker-start.json"}) {
		t.Fatal(kept)
	}
}

func TestALegacyDispatchReceiptRefusesInsteadOfRetrying(t *testing.T) {
	for _, local := range []bool{false, true} {
		for _, shape := range []string{"file", "dangling link"} {
			f := newWorkerFixture(t)
			calls := [][]string{versionCall, currentRunCall}
			if local {
				f.local()
				calls = [][]string{currentRunCall}
			} else {
				f.sprite()
			}
			legacy := filepath.Join(filepath.Dir(f.attempt), workerLane+".dispatch.json")
			if shape == "file" {
				f.write(legacy, "{}", 0o600)
			} else if err := os.Symlink(filepath.Join(filepath.Dir(f.attempt), "gone"), legacy); err != nil {
				t.Fatal(err)
			}
			f.refusedWith(legacy+" holds an earlier launch of this brief, and a brief gets one attempt; inspect it instead", calls...)
			f.wantNoAttempt()
		}
	}
}

func TestALaunchRefusedBeforeAnyWorkerStartedRelaunchesOnTheSameBrief(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("cc-remote orca prepare", workerReply{code: 1})
	if _, failed, err := f.launch(workerLane, "opus", "xhigh"); err != nil || failed == "" {
		t.Fatal(failed, err)
	}
	f.sprite()
	f.launched()
	f.wantCalls(slices.Concat([][]string{versionCall, currentRunCall, f.prepareCall()}, f.heldSpriteCalls())...)
	if kept := f.kept(); !slices.Equal(kept, spriteKept) {
		t.Fatal(kept)
	}
}

func TestTheDefaultPreparesOneSpriteAndStartsItOnce(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	line := f.launched()
	var printed object
	if err := json.Unmarshal([]byte(line), &printed); err != nil {
		t.Fatal(err)
	}
	want := object{"state": "ready", "task": "task_a", "dispatch": "ctx_a", "terminal": remoteTerminal, "worktree": remoteRoot, "lane": workerLane, "workspace": workerWorkspace, "allocation": "warm"}
	if !reflect.DeepEqual(printed, want) || strings.Contains(line, "\n") || f.stderr.Len() != 0 {
		t.Fatal(line, f.stderr.String())
	}
	f.wantCalls(f.heldSpriteCalls()...)
	if kept := f.kept(); !slices.Equal(kept, spriteKept) {
		t.Fatal(kept)
	}
	var request object
	if err := json.Unmarshal([]byte(f.receiptText("request.json")), &request); err != nil {
		t.Fatal(err)
	}
	wantRequest := object{
		"lane": workerLane, "run": workerRun, "route": routeSprite,
		"agent": object{"kind": "claude", "model": "claude-opus-5-5", "effort": "xhigh", "serviceTier": nil},
		"ref":   nil, "expectBase": nil,
		"brief": object{"path": filepath.Join(f.attempt, "brief.md"), "sha256": workerSHA, "bytes": float64(workerBytes)},
	}
	if !reflect.DeepEqual(request, wantRequest) {
		t.Fatal(request)
	}
	for name, text := range map[string]string{"prepare.err": "prepared on sprites\n", "prepare.status": "0\n", "worker-start.status": "0\n", "worker-start.json": rendered(startedReceipt)} {
		if got := f.receiptText(name); got != text {
			t.Errorf("%s: %q", name, got)
		}
	}
	var task object
	if err := json.Unmarshal([]byte(f.receiptText("prepared.json")), &task); err != nil || task["workspace"] != workerWorkspace {
		t.Fatal(task, err)
	}
	if !slices.Equal(f.configs, []string{"CC_REMOTE_CONFIG=" + f.config, "CC_REMOTE_CONFIG=" + f.config}) {
		t.Fatal(f.configs)
	}
}

func TestAStandingDeskBriefCarriesTheDeskContractIntoTheSpriteSpec(t *testing.T) {
	f := newWorkerFixture(t)
	text := "# Lane A\nccx: role=desk place=remote\nRun the loop until rotation.\n"
	f.write(f.brief, text, 0o600)
	f.spriteWith(overlaid(prepared(), object{"brief": object{"path": "/box/brief.md", "sha256": hexSum(text), "bytes": len(text)}}))
	f.launched()
	spec := f.startedSpec()
	if !strings.HasSuffix(spec, "then follow it exactly."+spriteBuildkite+standingDesk) {
		t.Fatal(spec)
	}
}

func TestABriefThatOrdersNoPRSaysSoInTheSpriteSpec(t *testing.T) {
	for header, want := range map[string]bool{"ccx: role=build pr=none": true, "ccx: role=build": false} {
		f := newWorkerFixture(t)
		text := "# Lane A\n" + header + "\nPush the design as a branch.\n"
		f.write(f.brief, text, 0o600)
		f.spriteWith(overlaid(prepared(), object{"brief": object{"path": "/box/brief.md", "sha256": hexSum(text), "bytes": len(text)}}))
		f.launched()
		spec := f.startedSpec()
		if strings.HasSuffix(spec, "then follow it exactly."+spriteBuildkite+" This brief orders no PR: push the work as a branch and never open a PR for it.") != want {
			t.Fatal(header, spec)
		}
	}
}

func TestAReadLaneTheMacsLoadSentToASpriteIsToldItsStoreStartsAtLaunch(t *testing.T) {
	for header, want := range map[string]bool{"ccx: role=read place=local": true, "ccx: role=read place=remote": false, "ccx: role=build place=local why=predecessor-worked-here": false} {
		f := newWorkerFixture(t)
		text := "# Lane A\n" + header + "\nRead what the drive recorded.\n"
		f.write(f.brief, text, 0o600)
		f.spriteWith(overlaid(prepared(), object{"brief": object{"path": "/box/brief.md", "sha256": hexSum(text), "bytes": len(text)}}))
		f.launched()
		spec := f.startedSpec()
		if strings.HasSuffix(spec, "cci store starts at its launch: check on the Mac before you report anything absent.") != want {
			t.Fatal(header, spec)
		}
	}
}

func TestASponsorJoinsTheSpriteToTheDefaultMeshBeforeTheWorkerStartsAndNeverLogsTheKey(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("sprite exec", workerReply{}, workerReply{out: "the sponsor key"}, workerReply{})
	f.launched()
	f.wantCalls(f.heldSpriteCalls()...)
	if f.stdins[strings.Join(joinCall(workerWorkspace), " ")] != "the sponsor key" {
		t.Fatal(f.stdins)
	}
	if strings.Contains(f.receiptText(joinReceipt), "the sponsor key") {
		t.Fatal("the log holds the key")
	}
}

func TestAHoldThatFailsToStartLeavesTheWorkerRunningAndSaysSo(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("sprite exec", workerReply{}, workerReply{out: "the sponsor key"}, workerReply{}, workerReply{code: 1, err: "services are down"})
	if line := f.launched(); !strings.Contains(line, `"state":"ready"`) {
		t.Fatal(line)
	}
	f.wantCalls(f.heldSpriteCalls()...)
	if !strings.Contains(f.stderr.String(), "holds no "+workerAwake+" task") {
		t.Fatal(f.stderr.String())
	}
	if !strings.Contains(f.receiptText(holdReceipt), "services are down") {
		t.Fatal(f.receiptText(holdReceipt))
	}
}

func TestARelayFailureThenASuccessJoinsAndStartsTheWorker(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("sprite exec", workerReply{}, workerReply{out: "the sponsor key"}, workerReply{code: 1, err: relayFailure}, workerReply{out: "joined mesh default\n"})
	f.launched()
	spec := f.startCalls(prepared())
	f.wantCalls(slices.Concat([][]string{versionCall, currentRunCall, f.prepareCall()}, spec[:len(spec)-1], [][]string{joinCall(workerWorkspace)}, spec[len(spec)-1:], [][]string{holdCall(workerWorkspace)})...)
	if !slices.Equal(f.slept, joinWaits[:1]) {
		t.Fatal(f.slept)
	}
	if f.stdins[strings.Join(joinCall(workerWorkspace), " ")] != "the sponsor key" {
		t.Fatal(f.stdins)
	}
}

func TestRepeatedRelayFailuresRefuseTheLaunchNamingTheReasonAndStartNoWorker(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("sprite exec", workerReply{}, workerReply{}, workerReply{code: 1, err: relayFailure}, workerReply{code: 1, err: relayFailure}, workerReply{code: 1, err: relayFailure}, workerReply{code: 1, err: relayFailure})
	_, failed, err := f.launch(workerLane, "opus", "xhigh")
	if err != nil {
		t.Fatal(err)
	}
	want := fmt.Sprintf("Sprite %s did not join the cci relay with %s's service key: 4 tries, the last saying cci: relay: 500 internal: the relay failed; retry; nothing started, because a worker with no relay posts nothing anyone reads; %s/%s keeps the output, and the prepared worker stays idle and unattached", workerWorkspace, sponsorBox, f.attempt, joinReceipt)
	if failed != want {
		t.Fatalf("failed\n%s\nwant\n%s", failed, want)
	}
	if !slices.Equal(f.slept, joinWaits) {
		t.Fatal(f.slept)
	}
	if exists(filepath.Join(f.attempt, "worker-start.json")) {
		t.Fatal("a worker started with no relay")
	}
}

func TestAJoinThatCannotReadTheOwnerRefusesWithoutRetrying(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("cci node", workerReply{code: 1})
	_, failed, err := f.launch(workerLane, "opus", "xhigh")
	if err != nil || !strings.Contains(failed, "cci node names no public key") || len(f.slept) != 0 {
		t.Fatal(failed, f.slept, err)
	}
}

func TestASpriteLaunchWithNoSponsorRefusesBeforeAnyCall(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	delete(f.env, sponsorEnv)
	f.refusedWith("set " + sponsorEnv + " to the Sprite holding the drive's enrolled cci service key, because a worker with no relay posts nothing anyone reads; nothing was prepared")
}

func TestAToolInstallThatFailsInPrepareStartsNoWorkerAndIsNeverRetried(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("cc-remote orca prepare", workerReply{code: 1, err: "the --before-worker-file script failed, so no worker started: sh -c: exit 1: refused: an archive off its digest\n"})
	f.refusedWith(fmt.Sprintf("cc-remote orca prepare exited 1; %s keeps its output, and nothing was attached or retried", f.attempt), versionCall, currentRunCall, f.prepareCall())
	if !strings.Contains(f.receiptText("prepare.err"), "refused: an archive off its digest") {
		t.Fatal(f.receiptText("prepare.err"))
	}
}

func TestTheInstallOutputInTheTaskRecordIsKeptForTheCensus(t *testing.T) {
	f := newWorkerFixture(t)
	f.spriteWith(overlaid(prepared(), object{"beforeWorker": "installed cci 0.49.42\ninstalled ccx 0.91.69\n"}))
	f.launched()
	if got := f.receiptText("install.log"); got != "installed cci 0.49.42\ninstalled ccx 0.91.69\n" {
		t.Fatal(got)
	}
}

func TestABoxWithoutAToolTheBriefRunsRefusesNamingItAndStartsNoWorker(t *testing.T) {
	f := newWorkerFixture(t)
	text := "# Watchdog\n\nLoop `lr wait 540 cci:d:w -- cci watch --drive d`; log each finding with `ccn log append <id>`, then lr wait again.\n"
	f.write(f.brief, text, 0o600)
	f.spriteWith(overlaid(prepared(), object{"brief": object{"path": "/box/brief.md", "sha256": hexSum(text), "bytes": len(text)}}))
	f.reply("cc-remote orca prepare", workerReply{code: 1, err: "the --before-worker-file script failed, so no worker started: sh -c: exit 3: installed lr 9\nmissing: ccn lr\n"})
	f.refusedWith(fmt.Sprintf("the Sprite has no ccn, lr, which the brief runs, so no worker started; %s/prepare.err keeps its output", f.attempt), versionCall, currentRunCall, f.prepareCall())
	if !strings.Contains(f.receiptText("before-worker.sh"), "-- cci ccn lr\n") {
		t.Fatal(f.receiptText("before-worker.sh"))
	}
}

func TestAPackWithoutALinuxLRPinRefusesBeforeAnyCall(t *testing.T) {
	f := newWorkerFixture(t)
	descriptor := filepath.Join(f.root, "plugin", "bin", "lr.binrun")
	f.write(descriptor, lrPin("macos-aarch64"), 0o700)
	f.refusedWith(descriptor + " pins no sha256 tar.gz GitHub release of lr for linux-x86_64; nothing was prepared")
	f = newWorkerFixture(t)
	f.env["BINRUN_PLUGIN_ROOT"] = filepath.Join(f.root, "absent")
	f.refusedWith("no lr pin at " + filepath.Join(f.root, "absent", "bin", "lr.binrun") + " to install on the Sprite; run lr through the pack's bin/lr; nothing was prepared")
	f.wantNoAttempt()
}

func TestTheToolInstallPutsThePinnedLROnPathWithTheProxyURLAndRefusesAnotherDigest(t *testing.T) {
	dir := t.TempDir()
	archive := func(name, text string) (string, string) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(text), 0o700); err != nil { //nolint:gosec // the stub has to execute
			t.Fatal(err)
		}
		tgz := filepath.Join(dir, name+".tgz")
		if out, err := exec.Command("tar", "-C", dir, "-czf", tgz, name).CombinedOutput(); err != nil {
			t.Fatal(string(out), err)
		}
		raw, err := os.ReadFile(tgz)
		if err != nil {
			t.Fatal(err)
		}
		return "file://" + tgz, hexSum(string(raw))
	}
	cciURL, cciSHA := archive("cci", "#!/bin/sh\necho cci\n")
	lrURL, lrSHA := archive("lr", "#!/bin/sh\necho lr 9.9.9\n")
	home := ""
	install := func(digest string, needs ...string) (string, error) {
		home = t.TempDir()
		run := exec.Command("sh", slices.Concat([]string{"-c", installTools, "sh", cciURL, cciSHA, lrURL, digest, "lr", "--"}, needs)...)
		run.Env = append(os.Environ(), "HOME="+home)
		if out, err := run.CombinedOutput(); err != nil {
			return filepath.Join(home, ".local", "bin", "lr"), fmt.Errorf("%s: %w", out, err)
		}
		return filepath.Join(home, ".local", "bin", "lr"), nil
	}
	lr, err := install(lrSHA)
	if err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(lr).Output(); err != nil || string(out) != "lr 9.9.9\n" {
		t.Fatal(string(out), err)
	}
	if _, err := install(lrSHA, "cci", "lr"); err != nil {
		t.Fatal("a box holding every tool the brief runs was refused", err)
	}
	var missing *exec.ExitError
	if _, err := install(lrSHA, "lr", "no-such-drive-tool"); !errors.As(err, &missing) || missing.ExitCode() != toolsMissing || !strings.Contains(err.Error(), "missing: no-such-drive-tool\n") {
		t.Fatal("a box without a tool the brief runs was not refused naming it", err)
	}
	if lr, err = install(testLRSHA); err == nil || present(lr) {
		t.Fatal("an archive off the pinned digest installed lr", err)
	}
}

func TestTheToolInstallUnpacksEveryCCIBinaryAndNoDocs(t *testing.T) {
	dir := t.TempDir()
	cciURL, cciSHA := tarball(t, dir, "cci.tgz", map[string]string{"cci": "#!/bin/sh\necho 0.49.58\n", "cci-core": "core", "README.md": "docs"})
	lrURL, lrSHA := tarball(t, dir, "lr.tgz", map[string]string{"lr": "#!/bin/sh\necho 9.9.9\n"})
	home := t.TempDir()
	run := exec.Command("sh", "-c", installTools, "sh", cciURL, cciSHA, lrURL, lrSHA, "lr", "--")
	run.Env = append(os.Environ(), "HOME="+home)
	if out, err := run.CombinedOutput(); err != nil {
		t.Fatal(string(out), err)
	}
	bin := filepath.Join(home, ".local", "bin")
	if !present(filepath.Join(bin, "cci-core")) || present(filepath.Join(bin, "README.md")) {
		t.Fatal("the install must unpack cci-core beside cci and leave the docs out")
	}
}

func TestALaunchCarriesEachToolPinIntoTheInstallAndRefusesAMalformedPinBeforeAnyCall(t *testing.T) {
	f := newWorkerFixture(t)
	f.env["WORKER_TOOLS"] = `[{"kind":"binary","name":"ccx","url":"https://example.test/ccx.tgz","sha256":"` + testCCISHA + `","version":"0.91.68"},{"kind":"plugin","name":"long-running@skills","url":"https://example.test/pack.tgz","sha256":"` + testLRSHA + `","version":"0.7.1199"}]`
	f.sprite()
	f.launched()
	want := "binary ccx https://example.test/ccx.tgz " + testCCISHA + " 0.91.68 plugin long-running@skills https://example.test/pack.tgz " + testLRSHA + " 0.7.1199 --"
	if script := f.receiptText("before-worker.sh"); !strings.Contains(strings.SplitN(script, "\n", 3)[1], want) {
		t.Fatalf("install\n%v\nwant\n%v", script, want)
	}
	for name, pin := range map[string]string{
		"ccx":          `{"kind":"script","name":"ccx","url":"https://example.test/ccx.tgz","sha256":"` + testCCISHA + `","version":"0.91.68"}`,
		"long-running": `{"kind":"plugin","name":"long-running","url":"https://example.test/pack.tgz","sha256":"` + testLRSHA + `","version":"0.7.1199"}`,
		"cc-present":   `{"kind":"binary","name":"cc-present","url":"https://example.test/p.tgz","sha256":"` + strings.ToUpper(testLRSHA) + `","version":"0.54.5"}`,
	} {
		f = newWorkerFixture(t)
		f.env["WORKER_TOOLS"] = "[" + pin + "]"
		f.refusedWith(fmt.Sprintf("WORKER_TOOLS pins %q, not a binary, raw, package or plugin with a name, url, lowercase SHA-256 and version; nothing was prepared", name))
		f.wantNoAttempt()
	}
}

func tarball(t *testing.T, dir, name string, files map[string]string) (string, string) {
	t.Helper()
	root := filepath.Join(dir, name+".d")
	members := slices.Sorted(maps.Keys(files))
	for path, text := range files {
		if err := os.MkdirAll(filepath.Dir(filepath.Join(root, path)), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, path), []byte(text), 0o700); err != nil { //nolint:gosec // the stub has to execute
			t.Fatal(err)
		}
	}
	tgz := filepath.Join(dir, name)
	if out, err := exec.Command("tar", slices.Concat([]string{"-C", root, "-czf", tgz}, members)...).CombinedOutput(); err != nil {
		t.Fatal(string(out), err)
	}
	raw, err := os.ReadFile(tgz)
	if err != nil {
		t.Fatal(err)
	}
	return "file://" + tgz, hexSum(string(raw))
}

func TestTheBeforeWorkerScriptRunsTheInstallWithItsQuotedPinsAndMergesItsOutput(t *testing.T) {
	dir := t.TempDir()
	cciURL, cciSHA := tarball(t, dir, "cci.tgz", map[string]string{"cci": "#!/bin/sh\necho 0.49.36\n"})
	lrURL, lrSHA := tarball(t, dir, "lr.tgz", map[string]string{"lr member": "#!/bin/sh\necho lr version 9.9.9\n"})
	home := t.TempDir()
	run := exec.Command("sh", "-c", spritePins{ccURL: cciURL, ccSHA: cciSHA, lrURL: lrURL, lrSHA: lrSHA, lrMember: "lr member"}.installScript([]string{"no-such-drive-tool"}))
	run.Env = append(os.Environ(), "HOME="+home)
	out, err := run.Output()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != toolsMissing {
		t.Fatal(string(out), err)
	}
	if !strings.Contains(string(out), "installed cci 0.49.36\ninstalled lr 9.9.9\nmissing: no-such-drive-tool\n") {
		t.Fatal(string(out))
	}
}

func TestAPluginNewToAMarketplaceThePayloadHoldsJoinsAWritableCopyOfIt(t *testing.T) {
	dir := t.TempDir()
	cciURL, cciSHA := tarball(t, dir, "cci.tgz", map[string]string{"cci": "#!/bin/sh\necho 0.49.36\n"})
	lrURL, lrSHA := tarball(t, dir, "lr.tgz", map[string]string{"lr": "#!/bin/sh\necho lr version 9.9.9\n"})
	orcaURL, orcaSHA := tarball(t, dir, "orca.tgz", map[string]string{".claude-plugin/plugin.json": `{"version":"0.1.0"}`, "skills/orca.md": "orca\n"})
	home, baked := t.TempDir(), t.TempDir()
	listing := `{"name":"skills","plugins":[{"name":"long-running","source":"./plugins/long-running"}]}`
	write := func(path, content string) {
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(filepath.Join(baked, ".claude-plugin", "marketplace.json"), listing)
	write(filepath.Join(baked, "plugins", "long-running", "SKILL.md"), "pack\n")
	write(filepath.Join(home, ".claude", "plugins", "installed_plugins.json"), `{"version":2,"plugins":{"long-running@skills":[{"scope":"user","installPath":"/payload/long-running/0.7.692","version":"0.7.692"}]}}`)
	write(filepath.Join(home, ".claude", "plugins", "known_marketplaces.json"), `{}`)
	write(filepath.Join(home, ".claude", "settings.json"), `{"enabledPlugins":{"long-running@skills":true}}`)
	tree := filepath.Join(home, ".local", "share", "cc-remote", "marketplaces", "skills")
	if err := os.MkdirAll(filepath.Dir(tree), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(baked, tree); err != nil {
		t.Fatal(err)
	}
	run := exec.Command("sh", "-c", installTools, "sh", cciURL, cciSHA, lrURL, lrSHA, "lr", "plugin", "orca@skills", orcaURL, orcaSHA, "0.1.0", "--")
	run.Env = append(os.Environ(), "HOME="+home)
	if out, err := run.CombinedOutput(); err != nil {
		t.Fatal(string(out), err)
	}
	for _, path := range []string{tree, filepath.Join(tree, "plugins"), filepath.Join(tree, "plugins", "long-running")} {
		if info, err := os.Lstat(path); err != nil || !info.IsDir() {
			t.Fatal(path, "is no real directory, and Claude refuses a plugin source that leaves its marketplace", err)
		}
	}
	if present(filepath.Join(baked, "orca")) {
		t.Fatal("the install wrote into the payload's tree")
	}
	if !present(filepath.Join(tree, "orca", "skills", "orca.md")) || !present(filepath.Join(tree, "plugins", "long-running", "SKILL.md")) {
		t.Fatal("the copy lost a plugin's source")
	}
	got, err := os.ReadFile(filepath.Join(tree, ".claude-plugin", "marketplace.json"))
	if err != nil || !strings.Contains(string(got), `"source": "./plugins/long-running"`) || !strings.Contains(string(got), `"source": "./orca"`) {
		t.Fatal(string(got), err)
	}
}

func TestAPluginPinInstallsOnABoxWithNoPluginStateYet(t *testing.T) {
	dir := t.TempDir()
	cciURL, cciSHA := tarball(t, dir, "cci.tgz", map[string]string{"cci": "#!/bin/sh\necho 0.49.36\n"})
	lrURL, lrSHA := tarball(t, dir, "lr.tgz", map[string]string{"lr": "#!/bin/sh\necho lr version 9.9.9\n"})
	packURL, packSHA := tarball(t, dir, "pack.tgz", map[string]string{".claude-plugin/plugin.json": `{"version":"0.7.1199"}`})
	home := t.TempDir()
	if err := os.MkdirAll(filepath.Join(home, ".claude"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, ".claude", "settings.json"), []byte(`{}`), 0o600); err != nil {
		t.Fatal(err)
	}

	run := exec.Command("sh", "-c", installTools, "sh", cciURL, cciSHA, lrURL, lrSHA, "lr", "plugin", "long-running@skills", packURL, packSHA, "0.7.1199", "--")
	run.Env = append(os.Environ(), "HOME="+home)
	if out, err := run.CombinedOutput(); err != nil {
		t.Fatal(string(out), err)
	}

	raw, err := os.ReadFile(filepath.Join(home, ".claude", "plugins", "installed_plugins.json"))
	if err != nil || !strings.Contains(string(raw), `"version": "0.7.1199"`) {
		t.Fatal(string(raw), err)
	}
}

func TestAPackInstallMovesAnExistingCurrentLinkAndCreatesNone(t *testing.T) {
	dir := t.TempDir()
	cciURL, cciSHA := tarball(t, dir, "cci.tgz", map[string]string{"cci": "#!/bin/sh\necho 0.49.36\n"})
	lrURL, lrSHA := tarball(t, dir, "lr.tgz", map[string]string{"lr": "#!/bin/sh\necho lr version 9.9.9\n"})
	packURL, packSHA := tarball(t, dir, "pack.tgz", map[string]string{".claude-plugin/plugin.json": `{"version":"0.7.1199"}`})
	install := func(link bool) string {
		home := t.TempDir()
		plugins := filepath.Join(home, ".claude", "plugins")
		if err := os.MkdirAll(plugins, 0o750); err != nil {
			t.Fatal(err)
		}
		baked := `{"version":2,"plugins":{"long-running@skills":[{"scope":"user","installPath":"/payload/long-running/0.7.692","version":"0.7.692"}]}}`
		if err := os.WriteFile(filepath.Join(plugins, "installed_plugins.json"), []byte(baked), 0o600); err != nil {
			t.Fatal(err)
		}
		if link {
			if err := os.MkdirAll(filepath.Join(home, ".claude", "long-running"), 0o750); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink("/payload/long-running/0.7.692", filepath.Join(home, ".claude", "long-running", "current")); err != nil {
				t.Fatal(err)
			}
		}
		run := exec.Command("sh", "-c", installTools, "sh", cciURL, cciSHA, lrURL, lrSHA, "lr", "plugin", "long-running@skills", packURL, packSHA, "0.7.1199", "--")
		run.Env = append(os.Environ(), "HOME="+home)
		if out, err := run.CombinedOutput(); err != nil {
			t.Fatal(string(out), err)
		}
		return home
	}
	linked := install(true)
	got, err := os.Readlink(filepath.Join(linked, ".claude", "long-running", "current"))
	if want := filepath.Join(linked, ".claude", "plugins", "cache", "skills", "long-running", "0.7.1199"); err != nil || got != want {
		t.Fatal(got, want, err)
	}
	if bare := install(false); present(filepath.Join(bare, ".claude", "long-running")) {
		t.Fatal("the install made a current link on a box that had none")
	}
}

func TestThePathLRRunsThePackTheCurrentLinkNamesAcrossPackMoves(t *testing.T) {
	dir := t.TempDir()
	cciURL, cciSHA := tarball(t, dir, "cci.tgz", map[string]string{"cci": "#!/bin/sh\necho 0.49.36\n"})
	lrURL, lrSHA := tarball(t, dir, "lr.tgz", map[string]string{"lr": "#!/bin/sh\necho lr version 0.1.1\n"})
	home := t.TempDir()
	plugins := filepath.Join(home, ".claude", "plugins")
	if err := os.MkdirAll(plugins, 0o750); err != nil {
		t.Fatal(err)
	}
	baked := `{"version":2,"plugins":{"long-running@skills":[{"scope":"user","installPath":"/payload/long-running/0.7.692","version":"0.7.692"}]}}`
	if err := os.WriteFile(filepath.Join(plugins, "installed_plugins.json"), []byte(baked), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(home, ".claude", "long-running"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("/payload/long-running/0.7.692", filepath.Join(home, ".claude", "long-running", "current")); err != nil {
		t.Fatal(err)
	}
	for _, pack := range []string{"0.7.1199", "0.7.1200"} {
		packURL, packSHA := tarball(t, dir, "pack-"+pack+".tgz", map[string]string{
			".claude-plugin/plugin.json": `{"version":"` + pack + `"}`,
			"bin/lr":                     "#!/bin/sh\necho lr version " + pack + "\n",
		})
		run := exec.Command("sh", "-c", installTools, "sh", cciURL, cciSHA, lrURL, lrSHA, "lr", "plugin", "long-running@skills", packURL, packSHA, pack, "--")
		run.Env = append(os.Environ(), "HOME="+home)
		if out, err := run.CombinedOutput(); err != nil {
			t.Fatal(string(out), err)
		}
		said, err := exec.Command(filepath.Join(home, ".local", "bin", "lr"), "--version").Output()
		if err != nil || strings.TrimSpace(string(said)) != "lr version "+pack {
			t.Fatalf("the PATH lr said %q after moving to %s: %v", said, pack, err)
		}
	}
}

func TestAToolPinInstallsOnlyTheArchiveWithItsDigestAndVersion(t *testing.T) {
	dir := t.TempDir()
	cciURL, cciSHA := tarball(t, dir, "cci.tgz", map[string]string{"cci": "#!/bin/sh\necho 0.49.36\n"})
	lrURL, lrSHA := tarball(t, dir, "lr.tgz", map[string]string{"lr": "#!/bin/sh\necho lr version 9.9.9\n"})
	ccxURL, ccxSHA := tarball(t, dir, "ccx.tgz", map[string]string{"ccx": "#!/bin/sh\necho v0.91.68\n", "README.md": "ccx\n"})
	staleURL, staleSHA := tarball(t, dir, "stale.tgz", map[string]string{"ccx": "#!/bin/sh\necho v0.89.18\n"})
	packURL, packSHA := tarball(t, dir, "pack.tgz", map[string]string{".claude-plugin/plugin.json": `{"version":"0.7.1199"}`, "capt-hook/pack.toml": "\n"})
	baked := `{"version":2,"plugins":{"long-running@skills":[{"scope":"user","installPath":"/payload/long-running/0.7.692","version":"0.7.692"}]}}` + "\n"
	home := ""
	install := func(tools ...string) error {
		home = t.TempDir()
		plugins := filepath.Join(home, ".claude", "plugins")
		if err := os.MkdirAll(plugins, 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(plugins, "installed_plugins.json"), []byte(baked), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(plugins, "known_marketplaces.json"), []byte(`{"skills":{"installLocation":"/payload/skills"}}`), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(home, ".claude", "settings.json"), []byte(`{"enabledPlugins":{"long-running@skills":true},"model":"opus"}`), 0o600); err != nil {
			t.Fatal(err)
		}
		run := exec.Command("sh", slices.Concat([]string{"-c", installTools, "sh", cciURL, cciSHA, lrURL, lrSHA, "lr"}, tools, []string{"--"})...)
		run.Env = append(os.Environ(), "HOME="+home)
		if out, err := run.CombinedOutput(); err != nil {
			return fmt.Errorf("%s: %w", out, err)
		}
		return nil
	}
	recorded := func() string {
		raw, err := os.ReadFile(filepath.Join(home, ".claude", "plugins", "installed_plugins.json"))
		if err != nil {
			t.Fatal(err)
		}
		return string(raw)
	}
	ccx := func() string { return filepath.Join(home, ".local", "bin", "ccx") }

	if err := install("binary", "ccx", ccxURL, ccxSHA, "0.91.68", "plugin", "long-running@skills", packURL, packSHA, "0.7.1199"); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(ccx(), "--version").Output(); err != nil || string(out) != "v0.91.68\n" {
		t.Fatal(string(out), err)
	}
	pack := filepath.Join(home, ".claude", "plugins", "cache", "skills", "long-running", "0.7.1199")
	var moved struct {
		Plugins map[string][]struct {
			Scope       string `json:"scope"`
			InstallPath string `json:"installPath"`
			Version     string `json:"version"`
		} `json:"plugins"`
	}
	if err := json.Unmarshal([]byte(recorded()), &moved); err != nil {
		t.Fatal(err)
	}
	if entry := moved.Plugins["long-running@skills"]; len(entry) != 1 || entry[0].Scope != "user" || entry[0].InstallPath != pack || entry[0].Version != "0.7.1199" || !present(filepath.Join(pack, "capt-hook", "pack.toml")) {
		t.Fatal(recorded())
	}

	if err := install("binary", "ccx", ccxURL, testCCISHA, "0.91.68"); err == nil || present(ccx()) {
		t.Fatal("a binary archive off its pinned digest installed", err)
	}
	if err := install("plugin", "long-running@skills", packURL, testLRSHA, "0.7.1199"); err == nil || recorded() != baked || present(filepath.Join(home, ".claude", "plugins", "cache")) {
		t.Fatal("a pack archive off its pinned digest moved the install", err, recorded())
	}
	if err := install("binary", "ccx", staleURL, staleSHA, "0.91.68"); err == nil || present(ccx()) || present(ccx()+".new") {
		t.Fatal("a binary that prints another version installed", err)
	}
	if err := install("plugin", "long-running@skills", packURL, packSHA, "0.7.1200"); err == nil || recorded() != baked {
		t.Fatal("a pack archive of another version moved the install", err, recorded())
	}

	inboxURL, inboxSHA := tarball(t, dir, "inbox.tgz", map[string]string{".claude-plugin/plugin.json": `{"name":"cc-inbox","version":"0.49.47"}`, "hooks/hooks.json": "{}\n"})
	if err := install("plugin", "cc-inbox@cc-inbox", inboxURL, inboxSHA, "0.49.47"); err != nil {
		t.Fatal(err)
	}
	var added struct {
		Plugins map[string][]struct {
			Scope       string `json:"scope"`
			InstallPath string `json:"installPath"`
			Version     string `json:"version"`
			InstalledAt string `json:"installedAt"`
		} `json:"plugins"`
	}
	if err := json.Unmarshal([]byte(recorded()), &added); err != nil {
		t.Fatal(err)
	}
	inbox := filepath.Join(home, ".claude", "plugins", "cache", "cc-inbox", "cc-inbox", "0.49.47")
	if entry := added.Plugins["cc-inbox@cc-inbox"]; len(entry) != 1 || entry[0].Scope != "user" || entry[0].InstallPath != inbox || entry[0].Version != "0.49.47" || entry[0].InstalledAt == "" || len(added.Plugins["long-running@skills"]) != 1 {
		t.Fatal(recorded())
	}
	tree := filepath.Join(home, ".local", "share", "cc-remote", "marketplaces", "cc-inbox")
	marketplace, err := os.ReadFile(filepath.Join(tree, ".claude-plugin", "marketplace.json"))
	if err != nil || !strings.Contains(string(marketplace), `"source": "./cc-inbox"`) || !present(filepath.Join(tree, "cc-inbox", "hooks", "hooks.json")) {
		t.Fatal(string(marketplace), err)
	}
	known, err := os.ReadFile(filepath.Join(home, ".claude", "plugins", "known_marketplaces.json"))
	if err != nil || !strings.Contains(string(known), `"path": "`+tree+`"`) || !strings.Contains(string(known), `/payload/skills`) {
		t.Fatal(string(known), err)
	}
	settings, err := os.ReadFile(filepath.Join(home, ".claude", "settings.json"))
	if err != nil || !strings.Contains(string(settings), `"cc-inbox@cc-inbox": true`) || !strings.Contains(string(settings), `"model": "opus"`) {
		t.Fatal(string(settings), err)
	}

	host := `#!/bin/sh
case $1 in
version) echo '{"schema":1,"build":"BUILD"}' ;;
package-install) cp "$0" "$HOME/.local/bin/capt-hookd" && touch "$HOME/installed" ;;
esac
`
	hookdURL, hookdSHA := tarball(t, dir, "hookd.tgz", map[string]string{"capt-hookd": strings.ReplaceAll(host, "BUILD", "12.117.9")})
	if err := install("package", "capt-hookd", hookdURL, hookdSHA, "12.117.9"); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(filepath.Join(home, ".local", "bin", "capt-hookd"), "version").Output(); err != nil || !strings.Contains(string(out), `"build":"12.117.9"`) {
		t.Fatal(string(out), err)
	}
	if err := install("package", "capt-hookd", hookdURL, hookdSHA, "12.117.10"); err == nil || present(filepath.Join(home, "installed")) {
		t.Fatal("a package that reports another build ran its install", err)
	}
	if err := install("package", "capt-hookd", hookdURL, testCCISHA, "12.117.9"); err == nil || present(filepath.Join(home, "installed")) {
		t.Fatal("a package archive off its pinned digest ran its install", err)
	}
}

func TestAnExplicitCLIRunsAgainstTheCheckoutsConfigAtTheHelperPin(t *testing.T) {
	f := newWorkerFixture(t)
	selected := filepath.Join(f.root, "selected", "cc-remote")
	f.write(selected, "#!/bin/sh\n", 0o700)
	f.env["WORKER_CC_REMOTE"] = selected
	f.sprite()
	f.launched()
	f.wantCalls(f.heldSpriteCalls()...)
}

func TestAMissingCCRemoteRefuses(t *testing.T) {
	f := newWorkerFixture(t)
	missing := filepath.Join(f.home, "absent", "cc-remote")
	f.env["WORKER_CC_REMOTE"] = missing
	f.sprite()
	f.refusedWith("WORKER_CC_REMOTE names no cc-remote command at " + missing + "; unset it to run this checkout's pinned one; nothing was prepared")
}

func TestAnExplicitConfigOfAnotherCheckoutLeavesTheVersionToCCRemote(t *testing.T) {
	f := newWorkerFixture(t)
	other := filepath.Join(f.root, "other", "config.yaml")
	f.write(other, "provider: sprites\n", 0o600)
	f.env["CC_REMOTE_CONFIG"] = other
	f.sprite()
	f.launched()
	f.wantCalls(slices.Concat([][]string{currentRunCall, f.prepareCall()}, f.startCalls(prepared()), [][]string{holdCall(workerWorkspace)})...)
	if !slices.Equal(f.configs, []string{"CC_REMOTE_CONFIG=" + other}) {
		t.Fatal(f.configs)
	}
}

func TestACodexModelKeepsItsEffortTierRefAndMCP(t *testing.T) {
	f := newWorkerFixture(t)
	task := overlaid(prepared(), object{"agent": object{"kind": "codex", "model": "gpt-6.1-sol", "effort": "xhigh", "serviceTier": "fast", "mcp": []string{"{}"}}})
	f.spriteAllocated(allocationOf(task, object{"ref": "release/2026.10"}))
	maps.Copy(f.env, map[string]string{"WORKER_SERVICE_TIER": "fast", "WORKER_INCIDENT": "1", "WORKER_REF": "release/2026.10", "WORKER_MCP_CONFIG": "{}"})
	if _, failed, err := f.launch(workerLane, "gpt-6.1-sol", "xhigh"); err != nil || failed != "" {
		t.Fatal(failed, err)
	}
	f.wantCalls(slices.Concat([][]string{versionCall, currentRunCall, f.prepareCallFor(workerLane, "codex", "gpt-6.1-sol", "xhigh", "--ref", "release/2026.10", "--service-tier", "fast", "--mcp-config", "{}")}, f.startCalls(task), [][]string{holdCall(workerWorkspace)})...)
}

func TestAnExpectedBaseMismatchLeavesThePreparedWorkerIdle(t *testing.T) {
	expected := strings.Repeat("3", 64)
	f := newWorkerFixture(t)
	f.env["WORKER_EXPECT_BASE"] = expected
	f.sprite()
	f.refusedWith(fmt.Sprintf("the Sprite checked out %s, not WORKER_EXPECT_BASE %s, so the prepared worker stays idle and unattached, and nothing is prepared again", remoteBase, expected), versionCall, currentRunCall, f.prepareCall())
	if kept := f.kept(); !slices.Equal(kept, prepareKept) {
		t.Fatal(kept)
	}
}

func TestAnExpectedBaseThatMatchesAttaches(t *testing.T) {
	f := newWorkerFixture(t)
	f.env["WORKER_EXPECT_BASE"] = remoteBase
	f.sprite()
	f.launched()
	f.wantCalls(f.heldSpriteCalls()...)
}

func TestAFailedPrepareKeepsItsRawOutputAndAttachesNothing(t *testing.T) {
	partial := rendered(allocationOf(overlaid(prepared(), object{"prepared": false}), nil))
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("cc-remote orca prepare", workerReply{code: 1, out: partial, err: "cc-remote: provider timed out\n"})
	f.refusedWith(fmt.Sprintf("cc-remote orca prepare exited 1; %s keeps its output, and nothing was attached or retried", f.attempt), versionCall, currentRunCall, f.prepareCall())
	if kept := f.kept(); !slices.Equal(kept, allocationKept) || f.receiptText("allocation.json") != partial || f.receiptText("prepare.status") != "1\n" {
		t.Fatal(kept)
	}
}

func TestAPreparedRecordThatDiffersFromTheRequestAttachesNothing(t *testing.T) {
	const (
		workspace = "a task other than the allocated Sprite agents workspace"
		anAgent   = "an agent other than claude claude-opus-5-5 at xhigh effort"
		electron  = "an Orca runtime serving on Electron, not its managed server"
		placement = "a task without its environment, terminal, repoId::projectRoot worktree, and brief path"
	)
	briefFault := fmt.Sprintf("a brief other than %d bytes with SHA-256 %s", workerBytes, workerSHA)
	task := prepared()
	base := task["agent"].(object)
	brief := task["brief"].(object)
	cases := []struct {
		allocated any
		reason    string
	}{
		{"", "not one JSON object"},
		{rendered(task) + rendered(task), "not one JSON object"},
		{rendered([]object{task}), "not one JSON object"},
		{"prepared", "not readable as JSON"},
		{overlaid(task, object{"prepared": false}), "prepared is not true"},
		{without(task, "managedServer"), electron},
		{overlaid(task, object{"managedServer": ""}), electron},
		{overlaid(task, object{"workspace": "lane-b"}), workspace},
		{overlaid(task, object{"provider": "namespace"}), workspace},
		{overlaid(task, object{"profile": "tests"}), workspace},
		{overlaid(task, object{"agent": overlaid(base, object{"kind": "codex"})}), anAgent},
		{overlaid(task, object{"agent": overlaid(base, object{"effort": "high"})}), anAgent},
		{overlaid(task, object{"agent": overlaid(base, object{"serviceTier": "fast"})}), anAgent},
		{overlaid(task, object{"brief": overlaid(brief, object{"sha256": strings.Repeat("0", 64)})}), briefFault},
		{overlaid(task, object{"brief": overlaid(brief, object{"bytes": workerBytes + 1})}), briefFault},
		{without(task, "environment"), placement},
		{overlaid(task, object{"terminal": ""}), placement},
		{overlaid(task, object{"worktreeId": "repo-1::/home/sprite/other"}), placement},
		{overlaid(task, object{"brief": overlaid(brief, object{"path": nil})}), placement},
	}
	for _, c := range cases {
		f := newWorkerFixture(t)
		f.spriteWith(c.allocated)
		f.refusedWith(fmt.Sprintf("%s/allocation.json holds %s, so the prepared worker stays idle and unattached", f.attempt, c.reason), versionCall, currentRunCall, f.prepareCall())
		if kept := f.kept(); !slices.Equal(kept, allocationKept) {
			t.Fatal(c.reason, kept)
		}
	}
}

func TestASpriteWorkerInsideADriveCarriesTheLaneAndTheDriveToItsAgent(t *testing.T) {
	f := newWorkerFixture(t)
	f.write(filepath.Join(f.home, ".claude", "long-running", "drives", "brook.json"), `{"drive":"brook","orca_run":"`+workerRun+`"}`, 0o600)
	f.sprite()
	f.launched()
	f.wantCalls(slices.Concat([][]string{versionCall, currentRunCall, f.prepareCall("--env", "CLAUDE_LONG_RUNNING_DRIVE=brook")}, f.startCalls(prepared()), [][]string{holdCall(workerWorkspace)})...)
}

func TestAnEmptyPoolCreatesTheRequestedLaneOnce(t *testing.T) {
	task := overlaid(prepared(), object{"workspace": workerLane, "machine": workerLane, "environment": workerLane})
	f := newWorkerFixture(t)
	f.spriteAllocated(allocationOf(task, object{"workspace": workerLane, "allocation": "created"}))
	var printed object
	if err := json.Unmarshal([]byte(f.launched()), &printed); err != nil || printed["workspace"] != workerLane || printed["allocation"] != "created" {
		t.Fatal(printed, err)
	}
	f.wantCalls(slices.Concat([][]string{versionCall, currentRunCall, f.prepareCall()}, f.startCalls(task), [][]string{holdCall(workerLane)})...)
}

func TestAnAllocationMustBindTheRequestedLaneAndActualTask(t *testing.T) {
	task := prepared()
	cases := []struct {
		allocated object
		reason    string
	}{
		{object{"schemaVersion": 2}, "an allocation other than schema 1 for lane lane-a"},
		{object{"lane": "lane-b"}, "an allocation other than schema 1 for lane lane-a"},
		{object{"workspace": "../pool"}, "an invalid allocated workspace"},
		{object{"allocation": "existing"}, "an allocation kind inconsistent with its workspace"},
		{object{"allocation": "created"}, "an allocation kind inconsistent with its workspace"},
		{object{"workspace": workerLane}, "an allocation kind inconsistent with its workspace"},
		{object{"ref": "release"}, "a ref other than the requested branch or tag"},
		{object{"task": nil}, "a task other than the allocated Sprite agents workspace"},
		{object{"head": strings.Repeat("2", 40)}, "an allocation HEAD inconsistent with its task"},
	}
	for _, c := range cases {
		f := newWorkerFixture(t)
		f.env["WORKER_REF"] = "dev"
		f.spriteAllocated(allocationOf(task, c.allocated))
		f.refusedWith(fmt.Sprintf("%s/allocation.json holds %s, so the prepared worker stays idle and unattached", f.attempt, c.reason), versionCall, currentRunCall, f.prepareCall("--ref", "dev"))
	}
}

func TestAFailedSpriteStartKeepsItsPreparedWorkerAndReceipts(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("orca orchestration worker-start", workerReply{code: 1, out: object{"ok": false, "error": object{"code": refusalFenced, "message": "fenced"}, "result": object{"taskId": "task_a", "dispatchId": "ctx_a"}}})
	f.refusedWith(fmt.Sprintf("orca orchestration worker-start exited 1: consumer_fenced: fenced; kept terminal %s, worktree %s, and the receipt in %s", remoteTerminal, remoteRoot, f.attempt), f.fullSpriteCalls()...)
	if kept := f.kept(); !slices.Equal(kept, spriteKept) || f.receiptText("worker-start.status") != "1\n" {
		t.Fatal(kept)
	}
}

func TestAnUnprovenReadyNeverPrintsSuccess(t *testing.T) {
	result := startedReceipt["result"].(object)
	cases := []any{
		"",
		"not json",
		rendered(startedReceipt) + rendered(startedReceipt),
		overlaid(startedReceipt, object{"ok": false}),
		overlaid(startedReceipt, object{"error": object{"code": "stale"}}),
		object{"ok": true, "result": overlaid(result, object{"state": "outcome_unknown"})},
		object{"ok": true, "result": overlaid(result, object{"runId": "run_2"})},
		object{"ok": true, "result": without(result, "taskId")},
		object{"ok": true, "result": overlaid(result, object{"dispatchId": "ctx a"})},
	}
	for _, receipt := range cases {
		f := newWorkerFixture(t)
		f.sprite()
		f.reply("orca orchestration worker-start", workerReply{out: receipt})
		f.refusedWith(fmt.Sprintf(stoppedWorker, workerRun, remoteTerminal, remoteRoot, f.attempt), f.fullSpriteCalls()...)
	}
}

func TestAMacFileTheBriefNamesIsOnTheBoxAtThePathTheWorkerReadsBeforeItStarts(t *testing.T) {
	f := newWorkerFixture(t)
	plan := filepath.Join(f.home, ".claude", "scratch", "plan.md")
	f.write(plan, "the plan\n", 0o600)
	f.write(f.brief, "Read "+plan+" first.\n", 0o600)
	sent := "Read " + carriedBox + plan + " first.\n"
	f.spriteWith(overlaid(prepared(), object{"brief": object{"path": "/box/brief.md", "sha256": hexSum(sent), "bytes": len(sent)}}))
	f.launched()
	if got := f.receiptText("brief.md"); got != sent {
		t.Fatal(got)
	}
	unpacked, err := os.ReadFile(filepath.Join(f.box, "drive", "carried", strings.TrimPrefix(plan, "/")))
	if err != nil || string(unpacked) != "the plan\n" {
		t.Fatal(string(unpacked), err)
	}
	at := func(match func([]string) bool) int { return slices.IndexFunc(f.calls, match) }
	copied := at(func(call []string) bool { return call[0] == "sprite" && !slices.Contains(call, "--no-stdin") })
	started := at(func(call []string) bool { return slices.Contains(call, "worker-start") })
	if copied < 0 || copied >= started {
		t.Fatal(f.calls)
	}
}

func TestABriefNamingWhatNoSpriteCanReceiveRefusesNamingItAndPreparesNothing(t *testing.T) {
	f := newWorkerFixture(t)
	gone := filepath.Join(f.home, "gone.md")
	f.write(f.brief, "Read "+gone+".\n", 0o600)
	f.sprite()
	f.refusedWith(fmt.Sprintf("brief %s names what no Sprite can receive: %s does not exist on this Mac; nothing was prepared", f.brief, gone), versionCall, currentRunCall)
}

func TestASpriteThatCannotFetchTheCCNotesRefsStillStartsItsWorkerAndKeepsTheLog(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("sprite exec", workerReply{code: 1, err: "fatal: could not read Username for 'https://github.com'"}, workerReply{}, workerReply{})
	_, failed, err := f.launch(workerLane, "opus", "xhigh")
	if err != nil {
		t.Fatal(err)
	}
	if failed != "" {
		t.Fatal(failed)
	}
	if !strings.Contains(f.receiptText(notesReceipt), "could not read Username") {
		t.Fatal(f.receiptText(notesReceipt))
	}
	if !exists(filepath.Join(f.attempt, "worker-start.json")) {
		t.Fatal("the worker did not start")
	}
}

func TestTheNotesFetchTakesTheMacsTokenOnStdinAndWritesItNowhere(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	_, failed, err := f.launch(workerLane, "opus", "xhigh")
	if err != nil || failed != "" {
		t.Fatal(failed, err)
	}
	if got := f.stdins[strings.Join(notesCall(workerWorkspace), " ")]; got != macGitLogin {
		t.Fatalf("the fetch read %q on stdin", got)
	}
	for _, call := range f.calls {
		if strings.Contains(strings.Join(call, " "), macGitLogin) {
			t.Fatal("a command line holds the token:", call)
		}
	}
	if got := f.receiptText(notesReceipt); got != "" {
		t.Fatal(got)
	}
}

func TestTheNotesFetchHandsGitTheTokenThroughACredentialHelperItsArgumentsDoNotHold(t *testing.T) {
	dir := t.TempDir()
	logged := filepath.Join(dir, "logged")
	fake := "#!/bin/sh\nprintf '%s\\n' \"$@\" > " + logged + "\nprintf 'env=%s\\n' \"$NOTES_TOKEN\" >> " + logged + "\n"
	newWorkerFixture(t).write(filepath.Join(dir, "git"), fake, 0o700)
	run := exec.Command("sh", "-c", fetchNotesScript, "sh", "/box/root", notesRefs)
	run.Stdin = strings.NewReader(macGitLogin)
	run.Env = append(os.Environ(), "PATH="+dir+":"+os.Getenv("PATH"))
	if out, err := run.CombinedOutput(); err != nil {
		t.Fatal(string(out), err)
	}
	raw, err := os.ReadFile(logged)
	if err != nil {
		t.Fatal(err)
	}
	args, env, _ := strings.Cut(string(raw), "env=")
	if strings.Contains(args, strings.TrimSpace(macGitLogin)) {
		t.Fatal("git's arguments hold the token:", args)
	}
	if strings.TrimSpace(env) != strings.TrimSpace(macGitLogin) {
		t.Fatalf("git's environment holds %q", env)
	}
	for _, want := range []string{"credential.helper=\n", "password=$NOTES_TOKEN", "/box/root\nfetch\n--quiet\norigin\n" + notesRefs} {
		if !strings.Contains(args, want) {
			t.Fatalf("git ran with\n%s\nwant %q", args, want)
		}
	}
}

func TestAMacWithNoGitHubTokenStillStartsItsWorkerAndKeepsTheLog(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("gh auth token", workerReply{code: 1, err: "You are not logged into any GitHub hosts"})
	_, failed, err := f.launch(workerLane, "opus", "xhigh")
	if err != nil || failed != "" {
		t.Fatal(failed, err)
	}
	if !strings.Contains(f.receiptText(notesReceipt), "not logged into") {
		t.Fatal(f.receiptText(notesReceipt))
	}
	if !exists(filepath.Join(f.attempt, "worker-start.json")) {
		t.Fatal("the worker did not start")
	}
}

func TestARawPinInstallsTheDownloadedFileWithItsDigestAndVersion(t *testing.T) {
	dir := t.TempDir()
	cciURL, cciSHA := tarball(t, dir, "cci.tgz", map[string]string{"cci": "#!/bin/sh\necho 0.49.36\n"})
	lrURL, lrSHA := tarball(t, dir, "lr.tgz", map[string]string{"lr": "#!/bin/sh\necho lr version 9.9.9\n"})
	asset := filepath.Join(dir, "cc-notes_linux_amd64")
	body := []byte("#!/bin/sh\necho 'v0.74.48 (f77f0fd192d0eee5)'\n")
	if err := os.WriteFile(asset, body, 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(body)
	digest := hex.EncodeToString(sum[:])
	install := func(version string) (string, error) {
		home := t.TempDir()
		run := exec.Command("sh", "-c", installTools, "sh", cciURL, cciSHA, lrURL, lrSHA, "lr", "raw", "ccn", "file://"+asset, digest, version, "--")
		run.Env = append(os.Environ(), "HOME="+home)
		out, err := run.CombinedOutput()
		if err != nil {
			return filepath.Join(home, ".local", "bin", "ccn"), fmt.Errorf("%s: %w", out, err)
		}
		return filepath.Join(home, ".local", "bin", "ccn"), nil
	}
	ccn, err := install("0.74.48")
	if err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command(ccn, "--version").Output(); err != nil || !strings.HasPrefix(string(out), "v0.74.48 ") {
		t.Fatal(string(out), err)
	}
	if ccn, err := install("0.74.47"); err == nil || present(ccn) {
		t.Fatal("a file printing another version installed", err)
	}
}

func TestAFailedCopyLeavesTheWorkerIdle(t *testing.T) {
	f := newWorkerFixture(t)
	plan := filepath.Join(f.home, ".claude", "scratch", "plan.md")
	f.write(plan, "the plan\n", 0o600)
	f.write(f.brief, "Read "+plan+" first.\n", 0o600)
	sent := "Read " + carriedBox + plan + " first.\n"
	f.spriteWith(overlaid(prepared(), object{"brief": object{"path": "/box/brief.md", "sha256": hexSum(sent), "bytes": len(sent)}}))
	f.reply("sprite exec", workerReply{code: 1})
	_, failed, err := f.launch(workerLane, "opus", "xhigh")
	if err != nil || !strings.HasPrefix(failed, "copying the files the brief names") {
		t.Fatal(failed, err)
	}
	if exists(filepath.Join(f.attempt, "worker-start.json")) {
		t.Fatal("the worker started")
	}
}

func TestExplicitLocalUsesOnlyTheExistingLocalPrimitives(t *testing.T) {
	f := newWorkerFixture(t)
	f.local()
	line, failed, err := f.launch(workerLane, "fable", "xhigh")
	if err != nil || failed != "" {
		t.Fatal(failed, err)
	}
	if want := rendered(workerStarted{"ready", "task_l", "ctx_l", localTerminal, localWorktree}); line != want {
		t.Fatal(line)
	}
	f.wantCalls(f.localCalls("claude-fable-5-1", "xhigh", localRepo)...)
	if kept := f.kept(); !slices.Equal(kept, localKept) {
		t.Fatal(kept)
	}
}

func TestExplicitLocalKeepsTheExactModelAndEffort(t *testing.T) {
	f := newWorkerFixture(t)
	f.local()
	if _, failed, err := f.launch(workerLane, "claude-opus-5-5", "max"); err != nil || failed != "" {
		t.Fatal(failed, err)
	}
	f.wantCalls(f.localCalls("claude-opus-5-5", "max", localRepo)...)
}

func TestAFailedLocalStartKeepsItsTerminalAndWorktree(t *testing.T) {
	f := newWorkerFixture(t)
	f.local()
	f.reply("orca orchestration worker-start", workerReply{code: 1, out: object{"ok": false, "error": object{"code": refusalFenced, "message": "fenced"}}})
	_, failed, err := f.launch(workerLane, "fable", "xhigh")
	if err != nil || failed != fmt.Sprintf("orca orchestration worker-start exited 1: consumer_fenced: fenced; kept terminal %s, worktree %s, and the receipt in %s", localTerminal, localWorktree, f.attempt) {
		t.Fatal(failed, err)
	}
	if kept := f.kept(); !slices.Equal(kept, localKept) {
		t.Fatal(kept)
	}
}

func TestALocalStartWithoutAReadyReceiptPrintsNoSuccess(t *testing.T) {
	f := newWorkerFixture(t)
	f.local()
	f.reply("orca orchestration worker-start", workerReply{out: object{"ok": true, "result": object{"state": "ready", "taskId": "task_l", "dispatchId": "ctx_l"}}})
	_, failed, err := f.launch(workerLane, "fable", "xhigh")
	if err != nil || failed != fmt.Sprintf(stoppedWorker, workerRun, localTerminal, localWorktree, f.attempt) {
		t.Fatal(failed, err)
	}
}

func TestALocalIdleTimeoutKeepsItsTerminalAndWorktree(t *testing.T) {
	f := newWorkerFixture(t)
	f.local()
	f.reply("orca terminal wait", workerReply{code: 1, out: "timed out"})
	_, failed, err := f.launch(workerLane, "fable", "xhigh")
	if err != nil || failed != fmt.Sprintf("claude in %s never went idle; kept it and worktree %s", localTerminal, localWorktree) {
		t.Fatal(failed, err)
	}
	if f.stderr.String() != "orca terminal wait failed: timed out\n" {
		t.Fatal(f.stderr.String())
	}
	calls := f.localCalls("claude-fable-5-1", "xhigh", localRepo)
	f.wantCalls(calls[:len(calls)-1]...)
	if kept := f.kept(); !slices.Equal(kept, []string{"request.json"}) {
		t.Fatal(kept)
	}
}

func runtimeRefusal(code string) workerReply {
	return workerReply{code: 1, out: object{"ok": false, "error": object{"code": code, "message": "Timed out waiting for the remote Orca runtime to respond.", "data": object{"orchestrationRequestId": "req_start"}}}}
}

func (f *workerFixture) workerStarts() [][]string {
	var starts [][]string
	for _, call := range f.calls {
		if len(call) > 2 && call[2] == "worker-start" {
			starts = append(starts, call)
		}
	}
	return starts
}

func TestAWorkerStartTheRemoteRuntimeTimedOutReplaysTheSameRequestAndStarts(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("orca orchestration worker-start", runtimeRefusal("runtime_timeout"), workerReply{out: startedReceipt})

	f.launched()

	starts := f.workerStarts()
	if len(starts) != 2 || !slices.Equal(starts[1], slices.Concat(starts[0], []string{"--retry-request", "req_start"})) {
		t.Fatalf("worker-start calls %q, want the first replayed under its request id", starts)
	}
	if !slices.Equal(f.slept, joinWaits[:1]) {
		t.Fatal(f.slept)
	}
}

func TestAWorkerStartTheRuntimeKeepsRefusingFailsNamingItsTriesAndLastError(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("orca orchestration worker-start", runtimeRefusal("remote_runtime_unavailable"), runtimeRefusal("remote_runtime_unavailable"), runtimeRefusal("remote_runtime_unavailable"), runtimeRefusal("remote_runtime_unavailable"))

	_, failed, err := f.launch(workerLane, "opus", "xhigh")

	if err != nil || !strings.Contains(failed, "worker-start exited 1 after 4 tries: remote_runtime_unavailable: Timed out") {
		t.Fatalf("failed %q, err %v", failed, err)
	}
	if len(f.workerStarts()) != 4 || !slices.Equal(f.slept, joinWaits) {
		t.Fatalf("%d worker-starts, slept %v", len(f.workerStarts()), f.slept)
	}
}

func TestAWorkerStartRefusalThatMeansNoIsNeverReplayed(t *testing.T) {
	f := newWorkerFixture(t)
	f.sprite()
	f.reply("orca orchestration worker-start", runtimeRefusal(CodeFenced))

	_, failed, err := f.launch(workerLane, "opus", "xhigh")

	if err != nil || !strings.Contains(failed, "worker-start exited 1: dispatch_inactive") || len(f.workerStarts()) != 1 || len(f.slept) != 0 {
		t.Fatalf("failed %q, err %v, %d worker-starts, slept %v", failed, err, len(f.workerStarts()), f.slept)
	}
}
