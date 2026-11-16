package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

func stubCodex(listing, body string) string {
	return "#!/bin/sh\n[ \"$1\" = mcp ] && { printf '%s' '" + listing + "'; exit 0; }\n" + body
}

const stubCodexReplyBody = "out=\"\"; prev=\"\"\n" +
	"for a in \"$@\"; do [ \"$prev\" = \"-o\" ] && out=$a; prev=$a; done\n" +
	"cat > /dev/null\n" +
	"[ -n \"$out\" ] && echo pong > \"$out\"\n"

// stubCodexReply parses -o like the real codex and writes a fixed reply there.
var stubCodexReply = stubCodex("[]", stubCodexReplyBody)

// stubCodexSleep never completes within the test's window, proving the dispatch
// returned rather than blocked on it.
var stubCodexSleep = stubCodex("[]", "sleep 30\n")

var (
	binOnce sync.Once
	binPath string
	binErr  error
)

// codexAskBin builds the binary once in a PLUGIN_ROOT layout (bin/../AGENTS.md)
// so its fail-closed developer-instructions read resolves.
func codexAskBin(t *testing.T) string {
	t.Helper()
	binOnce.Do(func() {
		root, err := os.MkdirTemp("", "codex-ask-go.")
		if err != nil {
			binErr = err
			return
		}
		codexDir := filepath.Join(root, "codex")
		if err := os.MkdirAll(filepath.Join(codexDir, "bin"), 0o700); err != nil {
			binErr = err
			return
		}
		agents, err := os.ReadFile("AGENTS.md")
		if err != nil {
			binErr = err
			return
		}
		if err := os.WriteFile(filepath.Join(codexDir, "AGENTS.md"), agents, 0o600); err != nil { //nolint:gosec // writes under the test's own MkdirTemp root
			binErr = err
			return
		}
		if err := os.MkdirAll(filepath.Join(codexDir, ".claude-plugin"), 0o700); err != nil {
			binErr = err
			return
		}
		if err := os.WriteFile(filepath.Join(codexDir, ".claude-plugin", "plugin.json"), []byte(`{"name":"codex"}`), 0o600); err != nil { //nolint:gosec // writes under the test's own MkdirTemp root
			binErr = err
			return
		}
		out := filepath.Join(codexDir, "bin", "codex-ask")
		build := exec.Command("go", "build", "-o", out, ".") //nolint:gosec // builds the package under test
		build.Env = envWithoutGOROOT()
		if b, e := build.CombinedOutput(); e != nil {
			binErr = &buildError{e, b}
			return
		}
		binPath = out
	})
	if binErr != nil {
		t.Fatalf("build codex-ask: %v", binErr)
	}
	return binPath
}

type buildError struct {
	err error
	out []byte
}

func (e *buildError) Error() string { return e.err.Error() + "\n" + string(e.out) }

func envWithoutGOROOT() []string {
	var env []string
	for _, e := range os.Environ() {
		if strings.HasPrefix(e, "GOROOT=") {
			continue
		}
		env = append(env, e)
	}
	return env
}

// daemonkitHomeEnv relocates every daemonkit home-derived path: the state
// directory, the socket, the owner record, and Stable's staged program. HOME
// alone would leave all four on the live agent's, since daemonkit resolves them
// through the passwd entry.
const daemonkitHomeEnv = "DAEMONKIT_HOME"

// shortHome points HOME and DAEMONKIT_HOME at a short, symlink-free /tmp dir:
// the daemon socket path must stay under the sun_path limit, and launchd
// refuses a program path reached through a symlink (/tmp is one).
func shortHome(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "cax-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	resolved, err := filepath.EvalSymlinks(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", resolved)
	t.Setenv(daemonkitHomeEnv, resolved)
	return resolved
}

func mustTempDir(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "codex-t.")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	return dir
}

// canonicalScope returns a symlink-free dir so the dispatch's os.Getwd() Scope
// matches the seeded subject's scope byte for byte (macOS /tmp and /var symlink).
func canonicalScope(t *testing.T) string {
	t.Helper()
	resolved, err := filepath.EvalSymlinks(mustTempDir(t))
	if err != nil {
		t.Fatal(err)
	}
	return resolved
}

func writeStub(t *testing.T, dir, script string) {
	t.Helper()
	script = strings.Replace(script, "\n", "\n[ \"$1\" = login ] && exit \"${STUB_CODEX_LOGIN:-0}\"\n", 1)
	if err := os.WriteFile(filepath.Join(dir, "codex"), []byte(script), 0o755); err != nil { //nolint:gosec // test stub must be executable
		t.Fatal(err)
	}
}

func dispatchEnv(home, session, runs, stubDir, scope string) []string {
	env := []string{
		"HOME=" + home,
		daemonkitHomeEnv + "=" + home,
		"CODEX_ASK_RUNS_DIR=" + runs,
		"PATH=" + stubDir + string(os.PathListSeparator) + os.Getenv("PATH"),
		"PWD=" + scope,
	}
	if session != "" {
		env = append(env, "CLAUDE_CODE_SESSION_ID="+session)
	}
	if tmp := os.Getenv("TMPDIR"); tmp != "" {
		env = append(env, "TMPDIR="+tmp)
	}
	return env
}

func stdoutLine(out, prefix string) string {
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, prefix) {
			return strings.TrimSpace(strings.TrimPrefix(line, prefix))
		}
	}
	return ""
}

func cmdRecord(t *testing.T, sdir string) cmdSpec {
	t.Helper()
	var spec cmdSpec
	if err := json.Unmarshal([]byte(readFile(filepath.Join(sdir, "cmd"))), &spec); err != nil {
		t.Fatalf("read cmd: %v", err)
	}
	return spec
}

func cmdArgv(t *testing.T, sdir string) []string {
	t.Helper()
	return cmdRecord(t, sdir).Argv
}

func flagValue(argv []string, flag string) string {
	for i, arg := range argv {
		if arg == flag && i+1 < len(argv) {
			return argv[i+1]
		}
	}
	return ""
}

func developerInstructions(t *testing.T, sdir string) string {
	t.Helper()
	const prefix = "developer_instructions="
	for _, arg := range cmdArgv(t, sdir) {
		if strings.HasPrefix(arg, prefix) {
			return strings.TrimPrefix(arg, prefix)
		}
	}
	t.Fatal("cmd argv has no developer_instructions element")
	return ""
}

// TestDispatchAsyncReturnsWithoutBlocking proves --dispatch returns at once (the
// stub codex sleeps far past the call), prints the recovery contract, and lands
// the lane in the registry.
func TestDispatchAsyncReturnsWithoutBlocking(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexSleep)
	scope := canonicalScope(t)

	var stdout, stderr bytes.Buffer
	c := exec.Command(bin, "--dispatch", "ping") //nolint:gosec // drives the built binary under test
	c.Dir = scope
	c.Env = dispatchEnv(home, "", runs, stubDir, scope)
	c.Stdout, c.Stderr = &stdout, &stderr
	start := time.Now()
	if err := c.Run(); err != nil {
		t.Fatalf("dispatch: %v\nstderr: %s", err, stderr.String())
	}
	if elapsed := time.Since(start); elapsed > 15*time.Second {
		t.Fatalf("dispatch blocked %s while the stub codex sleeps 30s", elapsed)
	}

	out := stdout.String()
	reply := stdoutLine(out, "REPLY_FILE: ")
	if reply == "" || stdoutLine(out, "LOG_FILE: ") == "" || stdoutLine(out, "AWAIT: ") == "" {
		t.Fatalf("missing recovery lines:\n%s", out)
	}
	sdir := filepath.Dir(reply)
	t.Cleanup(func() { killLane(sdir) })
	if !isFile(filepath.Join(sdir, "meta")) {
		t.Fatalf("lane %s not registered (no meta)", sdir)
	}

	ps := exec.Command(bin, "--ps") //nolint:gosec // drives the built binary under test
	ps.Env = dispatchEnv(home, "", runs, stubDir, scope)
	psOut, _ := ps.CombinedOutput()
	if !strings.Contains(string(psOut), sdir) {
		t.Fatalf("--ps did not list %s:\n%s", sdir, psOut)
	}
}

// TestDispatchThroughSymlinkFindsAgentsMd provisions bin/codex-ask as a symlink
// (the install-binary.sh layout): AGENTS.md sits beside the symlink's bin/, not
// the real binary's home, and dispatch must still resolve it.
func TestDispatchThroughSymlinkFindsAgentsMd(t *testing.T) {
	target := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	scope := canonicalScope(t)

	pluginRoot := mustTempDir(t)
	if err := os.MkdirAll(filepath.Join(pluginRoot, "bin"), 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(pluginRoot, "AGENTS.md"), "symlink-layout developer instructions\n")
	writePluginManifest(t, pluginRoot)
	link := filepath.Join(pluginRoot, "bin", "codex-ask")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}

	var stdout, stderr bytes.Buffer
	c := exec.Command(link, "ping") //nolint:gosec // drives the symlinked binary under test
	c.Dir = scope
	c.Env = dispatchEnv(home, "", runs, stubDir, scope)
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Run(); err != nil {
		t.Fatalf("dispatch via symlink: %v\nstderr: %s", err, stderr.String())
	}
	out := stdout.String()
	reply := stdoutLine(out, "REPLY_FILE: ")
	sdir := filepath.Dir(reply)
	t.Cleanup(func() { killLane(sdir) })
	if got := strings.TrimSpace(readFile(reply)); got != "pong" {
		t.Fatalf("reply = %q, want pong (stderr: %s)", got, stderr.String())
	}
	if got := developerInstructions(t, sdir); got != "symlink-layout developer instructions\n\n"+boundedFilesystemDiscovery {
		t.Fatalf("developer instructions = %q, want symlink-layout override", got)
	}
	requireBoundedFilesystemDiscovery(t, developerInstructions(t, sdir))
	await := stdoutLine(out, "AWAIT: ")
	if !strings.Contains(await, link) {
		t.Fatalf("AWAIT = %q, want symlink invocation path %q", await, link)
	}
	if strings.Contains(await, target) {
		t.Fatalf("AWAIT = %q, contains resolved target path %q", await, target)
	}
}

// TestDispatchFromForeignPrefixIgnoresItsAgentsMd provisions codex-ask the way
// the Homebrew cask does, a symlink in <prefix>/bin where <prefix> is another
// project's checkout with its own AGENTS.md, and proves codex gets the embedded
// developer instructions rather than that project's.
func TestDispatchFromForeignPrefixIgnoresItsAgentsMd(t *testing.T) {
	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	scope := canonicalScope(t)

	prefix := mustTempDir(t)
	if err := os.MkdirAll(filepath.Join(prefix, "bin"), 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(prefix, "AGENTS.md"), "# Agent Instructions for Homebrew/brew\n")
	cask := filepath.Join(mustTempDir(t), "codex-ask")
	binary, err := os.ReadFile(codexAskBin(t)) //nolint:gosec // copies the test-built binary fixture
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cask, binary, 0o755); err != nil { //nolint:gosec // test fixture binary must be executable
		t.Fatal(err)
	}
	link := filepath.Join(prefix, "bin", "codex-ask")
	if err := os.Symlink(cask, link); err != nil {
		t.Fatal(err)
	}

	var stdout, stderr bytes.Buffer
	c := exec.Command(link, "ping") //nolint:gosec // drives the symlinked binary under test
	c.Dir = scope
	c.Env = dispatchEnv(home, "", runs, stubDir, scope)
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Run(); err != nil {
		t.Fatalf("dispatch via foreign prefix: %v\nstderr: %s", err, stderr.String())
	}
	sdir := filepath.Dir(stdoutLine(stdout.String(), "REPLY_FILE: "))
	t.Cleanup(func() { killLane(sdir) })
	got := developerInstructions(t, sdir)
	if strings.Contains(got, "Homebrew") {
		t.Fatalf("developer instructions came from the foreign prefix's AGENTS.md: %q", got)
	}
	if !strings.Contains(got, "agent-browser") {
		t.Fatalf("developer instructions missing the embedded agent-browser sentinel: %q", got)
	}
}

func TestCollectAwaitUsesInvokePath(t *testing.T) {
	bin := codexAskBin(t)
	target, err := filepath.EvalSymlinks(bin)
	if err != nil {
		t.Fatal(err)
	}
	home := shortHome(t)
	runs := mustTempDir(t)

	pluginRoot := mustTempDir(t)
	if err := os.MkdirAll(filepath.Join(pluginRoot, "bin"), 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(pluginRoot, "AGENTS.md"), "symlink-layout developer instructions\n")
	writePluginManifest(t, pluginRoot)
	link := filepath.Join(pluginRoot, "bin", "codex-ask")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}

	rootDir, err := os.MkdirTemp(runs, "codex-root.")
	if err != nil {
		t.Fatal(err)
	}
	lane := filepath.Join(rootDir, "lane-a")
	if err := os.Mkdir(lane, 0o700); err != nil {
		t.Fatal(err)
	}
	reply := filepath.Join(lane, "codex-r-x")
	logf := filepath.Join(lane, "codex-q-x.log")
	writeFile(t, filepath.Join(lane, "meta"), reply+"\n"+logf+"\n")

	var stdout, stderr bytes.Buffer
	c := exec.Command(link, "--collect", rootDir) //nolint:gosec // drives the symlinked binary under test
	c.Env = dispatchEnv(home, "", runs, mustTempDir(t), rootDir)
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Run(); err != nil {
		t.Fatalf("collect via symlink: %v\nstderr: %s", err, stderr.String())
	}
	var rec struct {
		Await string `json:"await"`
	}
	if err := json.Unmarshal([]byte(strings.TrimSpace(stdout.String())), &rec); err != nil {
		t.Fatalf("bad collect record %q: %v", stdout.String(), err)
	}
	if !strings.Contains(rec.Await, link) {
		t.Fatalf("await = %q, want symlink invocation path %q", rec.Await, link)
	}
	if strings.Contains(rec.Await, target) {
		t.Fatalf("await = %q, contains resolved target path %q", rec.Await, target)
	}
}

// TestDispatchBareBinaryEmbedsAgentsMd proves a binary without a disk AGENTS.md
// still passes the embedded developer feed to codex.
func TestDispatchBareBinaryEmbedsAgentsMd(t *testing.T) {
	source := codexAskBin(t)
	bareDir := mustTempDir(t)
	bare := filepath.Join(bareDir, "codex-ask")
	binary, err := os.ReadFile(source) //nolint:gosec // copies the test-built binary fixture
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(bare, binary, 0o755); err != nil { //nolint:gosec // test fixture binary must be executable
		t.Fatal(err)
	}

	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	scope := canonicalScope(t)

	var stdout, stderr bytes.Buffer
	c := exec.Command(bare, "ping") //nolint:gosec // drives the copied binary under test
	c.Dir = scope
	c.Env = dispatchEnv(home, "", runs, stubDir, scope)
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Run(); err != nil {
		t.Fatalf("dispatch bare binary: %v\nstderr: %s", err, stderr.String())
	}
	reply := stdoutLine(stdout.String(), "REPLY_FILE: ")
	if reply == "" {
		t.Fatalf("no REPLY_FILE printed:\n%s", stdout.String())
	}
	sdir := filepath.Dir(reply)
	t.Cleanup(func() { killLane(sdir) })
	if got := strings.TrimSpace(readFile(reply)); got != "pong" {
		t.Fatalf("reply = %q, want pong (stderr: %s)", got, stderr.String())
	}
	if got := developerInstructions(t, sdir); !strings.Contains(got, "agent-browser") {
		t.Fatalf("embedded developer instructions missing agent-browser sentinel")
	}
}

// TestDispatchEnvPluginRootOverridesEmbed covers the binrun layout: the binary
// runs from a cache shard that carries no plugin files, so only the plugin root
// the shim exports can point at the disk AGENTS.md and lanes/.
func TestDispatchEnvPluginRootOverridesEmbed(t *testing.T) {
	source := codexAskBin(t)
	shard := mustTempDir(t)
	cached := filepath.Join(shard, "codex-ask")
	binary, err := os.ReadFile(source) //nolint:gosec // copies the test-built binary fixture
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cached, binary, 0o755); err != nil { //nolint:gosec // test fixture binary must be executable
		t.Fatal(err)
	}

	pluginRoot := mustTempDir(t)
	writeFile(t, filepath.Join(pluginRoot, "AGENTS.md"), "binrun-layout developer instructions\n")
	writePluginManifest(t, pluginRoot)
	if err := os.MkdirAll(filepath.Join(pluginRoot, "lanes"), 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(pluginRoot, "lanes", "review.md"), "binrun-layout review lane\n")

	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	scope := canonicalScope(t)

	var stdout, stderr bytes.Buffer
	c := exec.Command(cached, "--lane", "review", "ping") //nolint:gosec // drives the copied binary under test
	c.Dir = scope
	c.Env = append(dispatchEnv(home, "", runs, stubDir, scope), "BINRUN_PLUGIN_ROOT="+pluginRoot)
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Run(); err != nil {
		t.Fatalf("dispatch with exported plugin root: %v\nstderr: %s", err, stderr.String())
	}
	reply := stdoutLine(stdout.String(), "REPLY_FILE: ")
	if reply == "" {
		t.Fatalf("no REPLY_FILE printed:\n%s", stdout.String())
	}
	sdir := filepath.Dir(reply)
	t.Cleanup(func() { killLane(sdir) })
	want := "binrun-layout developer instructions\n\n" + boundedFilesystemDiscovery + "\n\nbinrun-layout review lane"
	if got := developerInstructions(t, sdir); got != want {
		t.Fatalf("developer instructions = %q, want %q", got, want)
	}
	requireBoundedFilesystemDiscovery(t, developerInstructions(t, sdir))
}

// askRun drives the built binary against a stub codex under the given runs dir,
// returning stdout, stderr, and the exit code.
func askRun(t *testing.T, runs, stub string, args ...string) (string, string, int) {
	t.Helper()
	return askRunSession(t, runs, "", stub, args...)
}

func askRunSession(t *testing.T, runs, session, stub string, args ...string) (string, string, int) {
	t.Helper()
	return askRunHome(t, shortHome(t), runs, session, stub, args...)
}

func askRunHome(t *testing.T, home, runs, session, stub string, args ...string) (string, string, int) {
	t.Helper()
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stub)
	scope := canonicalScope(t)

	var stdout, stderr bytes.Buffer
	c := exec.Command(codexAskBin(t), args...) //nolint:gosec // drives the built binary under test
	c.Dir = scope
	c.Env = dispatchEnv(home, session, runs, stubDir, scope)
	c.Stdout, c.Stderr = &stdout, &stderr
	code := 0
	if err := c.Run(); err != nil {
		var exit *exec.ExitError
		if !errors.As(err, &exit) {
			t.Fatalf("run codex-ask %v: %v\nstderr: %s", args, err, stderr.String())
		}
		code = exit.ExitCode()
	}
	return stdout.String(), stderr.String(), code
}

// laneDir returns the lane a run minted, arming its cleanup.
func laneDir(t *testing.T, stdout string) string {
	t.Helper()
	reply := stdoutLine(stdout, "REPLY_FILE: ")
	if reply == "" {
		t.Fatalf("no REPLY_FILE printed:\n%s", stdout)
	}
	sdir := filepath.Dir(reply)
	t.Cleanup(func() { killLane(sdir) })
	return sdir
}

func mustBeEmpty(t *testing.T, runs, what string) {
	t.Helper()
	entries, err := os.ReadDir(runs)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("%s minted %d entries in the runs dir, want none", what, len(entries))
	}
}

// TestLaneAppendsAfterAgentsBaseline proves --lane lands its contract block after
// the AGENTS.md baseline. The test PLUGIN_ROOT carries AGENTS.md alone, so the
// lane text can only come from the embed — the golden harness's layout.
func TestLaneAppendsAfterAgentsBaseline(t *testing.T) {
	stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexReply, "--lane", "review", "ping")
	if code != 0 {
		t.Fatalf("--lane review exit %d\nstderr: %s", code, stderr)
	}
	dev := developerInstructions(t, laneDir(t, stdout))
	const sentinel = "## Lane: review"
	baseline, lane := strings.Index(dev, "agent-browser"), strings.Index(dev, sentinel)
	if baseline < 0 || lane < 0 {
		t.Fatalf("developer instructions missing the baseline or the lane block:\n%s", dev)
	}
	if baseline > lane {
		t.Fatalf("lane block precedes the AGENTS.md baseline:\n%s", dev)
	}
	if !strings.Contains(dev, "\n\n"+sentinel) {
		t.Fatalf("lane block not separated from the baseline by a blank line:\n%s", dev)
	}
	if !strings.Contains(dev, "Finder pass over the stated scope") {
		t.Fatalf("lane block truncated:\n%s", dev)
	}
}

func TestFilesystemDiscoveryPrecedesLaneAndMCPContracts(t *testing.T) {
	for _, args := range [][]string{{"ping"}, {"--lane", "review", "--mcp", "slack", "ping"}} {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexListsMCPServers, args...)
			if code != 0 {
				t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
			}
			requireBoundedFilesystemDiscovery(t, developerInstructions(t, laneDir(t, stdout)))
		})
	}
}

func requireBoundedFilesystemDiscovery(t *testing.T, dev string) {
	t.Helper()
	position := strings.Index(dev, "## Filesystem Discovery")
	if position < 0 {
		t.Fatalf("developer instructions omit bounded filesystem discovery: %s", dev)
	}
	for _, required := range []string{
		"checking those exact filenames in the current working directory and each ancestor directory",
		"check exact instruction filenames along the path from the current checkout root to that directory",
		"Never recursively scan parent directories, sibling worktrees, home, or temporary roots",
		"`find ..` and `rg --files . .. ../..` are forbidden",
		"use caller-known checkout roots and targeted path or glob searches",
	} {
		if !strings.Contains(dev, required) {
			t.Errorf("developer instructions omit %q", required)
		}
	}
	for _, later := range []string{"## Lane:", "## MCP"} {
		if index := strings.Index(dev, later); index >= 0 && index < position {
			t.Errorf("bounded filesystem discovery follows %q", later)
		}
	}
}

func TestUnknownLaneRefusesBeforeMinting(t *testing.T) {
	runs := mustTempDir(t)
	_, stderr, code := askRun(t, runs, stubCodexReply, "--lane", "nope", "ping")
	if code != 2 {
		t.Fatalf("unknown --lane exit %d, want 2\nstderr: %s", code, stderr)
	}
	for _, name := range []string{"review", "refute", "security", "diagnose", "implement", "recon", "-l NAME"} {
		if !strings.Contains(stderr, name) {
			t.Fatalf("stderr does not name %q:\n%s", name, stderr)
		}
	}
	mustBeEmpty(t, runs, "a refused --lane")
}

func TestNamedSchemaMaterializesInLane(t *testing.T) {
	stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexReply, "--schema", "verdict", "ping")
	if code != 0 {
		t.Fatalf("--schema verdict exit %d\nstderr: %s", code, stderr)
	}
	sdir := laneDir(t, stdout)
	got := flagValue(cmdArgv(t, sdir), "--output-schema")
	if filepath.Dir(got) != sdir {
		t.Fatalf("--output-schema = %q, want a file staged in the lane %q", got, sdir)
	}
	// Staged like codex-q-/codex-r-, so a reused --scratch lane never reuses it.
	if !strings.HasPrefix(filepath.Base(got), "codex-schema-") {
		t.Fatalf("--output-schema = %q, want the per-dispatch codex-schema- prefix", got)
	}
	var doc struct {
		Required []string `json:"required"`
	}
	if err := json.Unmarshal([]byte(readFile(got)), &doc); err != nil {
		t.Fatalf("materialized schema is not JSON: %v", err)
	}
	if len(doc.Required) != 2 || doc.Required[0] != "status" {
		t.Fatalf("materialized schema required = %v, want the shipped verdict schema", doc.Required)
	}
}

// TestSchemaStagingIsPerDispatch proves a reused --scratch lane stages a fresh
// schema each generation rather than inheriting the previous one: the stale sweep
// clears only status/pid/started/meta/cmd/register.
func TestSchemaStagingIsPerDispatch(t *testing.T) {
	sdir := filepath.Join(mustTempDir(t), "lane-a")
	runs := mustTempDir(t)
	t.Cleanup(func() { killLane(sdir) })
	first := stageSchemaOnce(t, runs, sdir, "verdict")
	second := stageSchemaOnce(t, runs, sdir, "findings")
	if first == second {
		t.Fatalf("both dispatches staged the same schema file %q", first)
	}
	if !isFile(first) || !isFile(second) {
		t.Fatalf("staged schemas missing: %q %q", first, second)
	}
}

// stageSchemaOnce runs one lane generation to completion, so the next dispatch
// clears the busy-lane guard, and returns the schema path it staged.
func stageSchemaOnce(t *testing.T, runs, sdir, name string) string {
	t.Helper()
	_, stderr, code := askRun(t, runs, stubCodexReply, "-s", sdir, "--schema", name, "ping")
	if code != 0 {
		t.Fatalf("--schema %s exit %d\nstderr: %s", name, code, stderr)
	}
	return flagValue(cmdArgv(t, sdir), "--output-schema")
}

// TestLaneAndSchemaNamesRejectTraversal pins the closed-set check on the names
// that get joined into a plugin path. --schema only joins a bare name; a value
// with a slash is a caller-supplied path by design and never reaches the join.
func TestLaneAndSchemaNamesRejectTraversal(t *testing.T) {
	cases := []struct{ flag, name string }{
		{"--lane", "../../../../etc/hostname"},
		{"--lane", ".."},
		{"--schema", ".."},
	}
	for _, tc := range cases {
		runs := mustTempDir(t)
		_, stderr, code := askRun(t, runs, stubCodexReply, tc.flag, tc.name, "ping")
		if code != 2 {
			t.Fatalf("%s %s exit %d, want 2\nstderr: %s", tc.flag, tc.name, code, stderr)
		}
		mustBeEmpty(t, runs, tc.flag+" "+tc.name)
	}
}

func TestUnknownSchemaNameRefusesBeforeMinting(t *testing.T) {
	runs := mustTempDir(t)
	_, stderr, code := askRun(t, runs, stubCodexReply, "--schema", "nope", "ping")
	if code != 2 {
		t.Fatalf("unknown --schema name exit %d, want 2\nstderr: %s", code, stderr)
	}
	for _, name := range []string{"verdict", "findings", "refutations"} {
		if !strings.Contains(stderr, name) {
			t.Fatalf("stderr does not name schema %q:\n%s", name, stderr)
		}
	}
	mustBeEmpty(t, runs, "a refused --schema name")
}

// TestSchemaExtensionlessFileReachableByPath pins the escape hatch for the
// name-vs-path grammar: a bare word is always a shipped name, so an
// extensionless schema file is reached by a path form, and the refusal says so.
func TestSchemaExtensionlessFileReachableByPath(t *testing.T) {
	schema := filepath.Join(mustTempDir(t), "schema")
	writeFile(t, schema, "{\"type\":\"object\"}\n")
	stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexReply, "--schema", schema, "ping")
	if code != 0 {
		t.Fatalf("--schema %s exit %d\nstderr: %s", schema, code, stderr)
	}
	if got := flagValue(cmdArgv(t, laneDir(t, stdout)), "--output-schema"); got != schema {
		t.Fatalf("--output-schema = %q, want the extensionless path %q", got, schema)
	}

	_, stderr, code = askRun(t, mustTempDir(t), stubCodexReply, "--schema", "schema", "ping")
	if code != 2 {
		t.Fatalf("bare unknown name exit %d, want 2\nstderr: %s", code, stderr)
	}
	if !strings.Contains(stderr, "./NAME") {
		t.Fatalf("refusal does not advertise the ./NAME escape hatch:\n%s", stderr)
	}
}

// TestSchemaPathFormUnchanged pins the pre-existing path form: a readable file
// passes straight through, and an unreadable one keeps its original refusal.
func TestSchemaPathFormUnchanged(t *testing.T) {
	schema := filepath.Join(mustTempDir(t), "schema.json")
	writeFile(t, schema, "{\"type\":\"object\"}\n")
	stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexReply, "--schema", schema, "ping")
	if code != 0 {
		t.Fatalf("--schema %s exit %d\nstderr: %s", schema, code, stderr)
	}
	if got := flagValue(cmdArgv(t, laneDir(t, stdout)), "--output-schema"); got != schema {
		t.Fatalf("--output-schema = %q, want the caller's path %q", got, schema)
	}

	runs := mustTempDir(t)
	missing := filepath.Join(mustTempDir(t), "nope.json")
	_, stderr, code = askRun(t, runs, stubCodexReply, "--schema", missing, "ping")
	if code != 2 {
		t.Fatalf("unreadable --schema exit %d, want 2\nstderr: %s", code, stderr)
	}
	if !strings.Contains(strings.ToLower(stderr), "readable json schema") {
		t.Fatalf("stderr = %q, want the unreadable-schema refusal", stderr)
	}
	mustBeEmpty(t, runs, "an unreadable --schema")
}

func TestLaneComposesWithScratchModelAndDispatch(t *testing.T) {
	sdir := filepath.Join(mustTempDir(t), "lane-a")
	stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexSleep,
		"-s", sdir, "-m", "luna", "--lane", "diagnose", "--dispatch", "ping")
	if code != 0 {
		t.Fatalf("composed dispatch exit %d\nstderr: %s", code, stderr)
	}
	t.Cleanup(func() { killLane(sdir) })
	if got := filepath.Dir(stdoutLine(stdout, "REPLY_FILE: ")); got != sdir {
		t.Fatalf("reply staged in %q, want the --scratch lane %q", got, sdir)
	}
	if argv := cmdArgv(t, sdir); !contains(argv, "model="+modelLuna) {
		t.Fatalf("argv = %v, want model=%s", argv, modelLuna)
	}
	if got := developerInstructions(t, sdir); !strings.Contains(got, "## Lane: diagnose") {
		t.Fatalf("developer instructions missing the diagnose lane block:\n%s", got)
	}
}

func TestDefaultModelIsSolAndAstraStaysSelectable(t *testing.T) {
	for _, tc := range []struct {
		name  string
		flags []string
		want  string
	}{
		{"default", nil, modelSol},
		{"astra", []string{"-m", "astra"}, modelAstra},
		{"sol", []string{"-m", "sol"}, modelSol},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sdir := filepath.Join(mustTempDir(t), "lane-"+tc.name)
			args := append([]string{"-s", sdir}, tc.flags...)
			_, stderr, code := askRun(t, mustTempDir(t), stubCodexSleep, append(args, "--dispatch", "ping")...)
			if code != 0 {
				t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
			}
			t.Cleanup(func() { killLane(sdir) })
			if argv := cmdArgv(t, sdir); !contains(argv, "model="+tc.want) {
				t.Fatalf("argv = %v, want model=%s", argv, tc.want)
			}
		})
	}
}

func TestEffortFlagSetsTheReasoningEffort(t *testing.T) {
	for _, tc := range []struct {
		name  string
		flags []string
		want  string
	}{
		{"default", nil, "xhigh"},
		{"medium", []string{"--effort", "medium"}, "medium"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sdir := filepath.Join(mustTempDir(t), "lane-"+tc.name)
			args := append([]string{"-s", sdir}, tc.flags...)
			_, stderr, code := askRun(t, mustTempDir(t), stubCodexSleep, append(args, "--dispatch", "ping")...)
			if code != 0 {
				t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
			}
			t.Cleanup(func() { killLane(sdir) })
			if argv := cmdArgv(t, sdir); !contains(argv, "model_reasoning_effort="+tc.want) {
				t.Fatalf("argv = %v, want model_reasoning_effort=%s", argv, tc.want)
			}
		})
	}
}

func TestEffortFlagRefusesAnUnknownLevel(t *testing.T) {
	_, stderr, code := askRun(t, mustTempDir(t), stubCodexSleep, "--effort", "extreme", "ping")
	if code != 2 || !strings.Contains(stderr, "--effort takes") {
		t.Fatalf("exit %d, stderr %q, want exit 2 naming --effort", code, stderr)
	}
}

func TestFastTierOnlyUnderIncident(t *testing.T) {
	for _, tc := range []struct {
		name  string
		flags []string
		fast  bool
	}{
		{"standard", nil, false},
		{"luna", []string{"-m", "luna"}, false},
		{"incident", []string{"--incident"}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sdir := filepath.Join(mustTempDir(t), "lane-"+tc.name)
			args := append([]string{"-s", sdir}, tc.flags...)
			_, stderr, code := askRun(t, mustTempDir(t), stubCodexSleep, append(args, "--dispatch", "ping")...)
			if code != 0 {
				t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
			}
			t.Cleanup(func() { killLane(sdir) })
			if argv := cmdArgv(t, sdir); contains(argv, "service_tier=fast") != tc.fast {
				t.Fatalf("argv = %v, want service_tier=fast present=%v", argv, tc.fast)
			}
		})
	}
}

// TestWatchEmitsOnSettleAndExits arms --watch on a pending lane, settles it
// mid-watch, and expects one JSONL record naming the reply plus a clean exit.
func TestWatchEmitsOnSettleAndExits(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)

	sdir, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	reply := filepath.Join(sdir, "codex-r-x")
	logf := filepath.Join(sdir, "codex-q-x.log")
	writeFile(t, filepath.Join(sdir, "meta"), reply+"\n"+logf+"\n")

	c := exec.Command(bin, "--watch", sdir) //nolint:gosec // drives the built binary under test
	c.Env = dispatchEnv(home, "", runs, mustTempDir(t), sdir)
	var stdout bytes.Buffer
	c.Stdout = &stdout
	if err := c.Start(); err != nil {
		t.Fatal(err)
	}
	time.Sleep(500 * time.Millisecond) // still pending: the watch must stay armed
	writeFile(t, reply, "pong\n")
	writeFile(t, filepath.Join(sdir, "status"), "0\n")

	done := make(chan error, 1)
	go func() { done <- c.Wait() }()
	select {
	case werr := <-done:
		if werr != nil {
			t.Fatalf("watch exited non-zero: %v\n%s", werr, stdout.String())
		}
	case <-time.After(15 * time.Second):
		_ = c.Process.Kill()
		t.Fatalf("watch never exited after settle; output: %s", stdout.String())
	}
	var rec psRecord
	if err := json.Unmarshal([]byte(strings.TrimSpace(stdout.String())), &rec); err != nil {
		t.Fatalf("bad watch record %q: %v", stdout.String(), err)
	}
	if rec.State != "completed" || rec.ReplyFile == nil || *rec.ReplyFile != reply {
		t.Fatalf("record = %+v, want completed naming %s", rec, reply)
	}
}

// TestWatchSettlesExpiredRegistration arms --watch on a lane whose dispatcher died
// inside the registration window (meta published, no pid ever recorded): the watch
// settles it as died instead of polling a forever-pending lane.
func TestWatchSettlesExpiredRegistration(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)

	sdir, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	meta := filepath.Join(sdir, "meta")
	reply := filepath.Join(sdir, "codex-r-x")
	writeFile(t, meta, reply+"\n"+filepath.Join(sdir, "codex-q-x.log")+"\n")
	aged := time.Now().Add(-(registerGraceS + 1) * time.Second)
	if err := os.Chtimes(meta, aged, aged); err != nil {
		t.Fatal(err)
	}

	c := exec.Command(bin, "--watch", sdir) //nolint:gosec // drives the built binary under test
	c.Env = dispatchEnv(home, "", runs, mustTempDir(t), sdir)
	var stdout bytes.Buffer
	c.Stdout = &stdout
	if err := c.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- c.Wait() }()
	select {
	case werr := <-done:
		if werr != nil {
			t.Fatalf("watch exited non-zero: %v\n%s", werr, stdout.String())
		}
	case <-time.After(15 * time.Second):
		_ = c.Process.Kill()
		t.Fatalf("watch never settled an expired registration; output: %s", stdout.String())
	}
	var rec psRecord
	if err := json.Unmarshal([]byte(strings.TrimSpace(stdout.String())), &rec); err != nil {
		t.Fatalf("bad watch record %q: %v", stdout.String(), err)
	}
	if rec.State != "died" {
		t.Fatalf("record = %+v, want died", rec)
	}
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil { //nolint:gosec // test fixture file
		t.Fatal(err)
	}
}

func writePluginManifest(t *testing.T, root string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(root, ".claude-plugin"), 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(root, ".claude-plugin", "plugin.json"), `{"name":"codex"}`)
}

// killLane reaps a detached worker's whole process group (setsid'd off the test)
// so a sleeping stub codex never outlives the run.
func killLane(sdir string) {
	pid, ok := readPid(filepath.Join(sdir, "pid"))
	if !ok {
		return
	}
	if pgid, err := syscall.Getpgid(pid); err == nil {
		_ = syscall.Kill(-pgid, syscall.SIGKILL)
	}
	_ = syscall.Kill(pid, syscall.SIGKILL)
}

func TestRemovedSubcommandRefusesBeforeMinting(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	scope := canonicalScope(t)
	for _, args := range [][]string{{"tools"}, {"channel", "--no-tools"}, {"daemon"}, {"agent-start"}, {"direct", "ping"}, {"supervise"}} {
		t.Run(args[0], func(t *testing.T) {
			runs := mustTempDir(t)
			var stderr bytes.Buffer
			c := exec.Command(bin, args...) //nolint:gosec // runs the binary under test
			c.Dir = scope
			c.Env = dispatchEnv(home, "", runs, stubDir, scope)
			c.Stderr = &stderr
			if err := c.Run(); err == nil {
				t.Fatalf("codex-ask %v exited 0", args)
			}
			if want := "codex-ask " + args[0] + ": removed in " + removedSubcommands[args[0]]; !strings.Contains(stderr.String(), want) {
				t.Fatalf("stderr = %q, want %q", stderr.String(), want)
			}
			entries, err := os.ReadDir(runs)
			if err != nil {
				t.Fatal(err)
			}
			if len(entries) != 0 {
				t.Fatalf("codex-ask %v minted %d run entries", args, len(entries))
			}
		})
	}
}

func TestChannelCheckAnOlderSessionsStopHookRunsExitsZeroSilently(t *testing.T) {
	var stdout, stderr bytes.Buffer
	c := exec.Command(codexAskBin(t), "channel-check") //nolint:gosec // runs the binary under test
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Run(); err != nil {
		t.Fatalf("codex-ask channel-check: %v, stderr %q", err, stderr.String())
	}
	if stdout.Len() != 0 || stderr.Len() != 0 {
		t.Fatalf("codex-ask channel-check printed stdout %q stderr %q", stdout.String(), stderr.String())
	}
}

