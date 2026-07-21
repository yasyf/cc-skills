package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/cc-interact/store"
)

// TestMain doubles the test binary as the async fixtures. A "daemon" argv (what
// the launcher spawns to autostart) exits 1 so autostart fails without serving —
// the fail-open path. A CODEX_ASK_TEST_WORKER_SDIR env runs the real worker
// (os.Exit inside), exercising status-write-then-wake with os.Executable as the
// broken autostart target.
func TestMain(m *testing.M) {
	if len(os.Args) > 1 && os.Args[1] == "daemon" {
		os.Exit(1)
	}
	if sdir := os.Getenv("CODEX_ASK_TEST_WORKER_SDIR"); sdir != "" {
		if ms, err := strconv.Atoi(os.Getenv("CODEX_ASK_TEST_WAKE_MS")); err == nil && ms > 0 {
			wakeTimeout = time.Duration(ms) * time.Millisecond
		}
		runWorker(sdir)
		return
	}
	os.Exit(m.Run())
}

// stubCodexReply parses -o like the real codex and writes a fixed reply there.
const stubCodexReply = "#!/bin/sh\n" +
	"out=\"\"; prev=\"\"\n" +
	"for a in \"$@\"; do [ \"$prev\" = \"-o\" ] && out=$a; prev=$a; done\n" +
	"cat > /dev/null\n" +
	"[ -n \"$out\" ] && echo pong > \"$out\"\n"

// stubCodexSleep never completes within the test's window, proving the dispatch
// returned rather than blocked on it.
const stubCodexSleep = "#!/bin/sh\nsleep 30\n"

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

// shortHome points HOME at a short /tmp dir (the daemon socket path must stay
// under the sun_path limit) and returns it.
func shortHome(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "cax-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	t.Setenv("HOME", dir)
	return dir
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
	if err := os.WriteFile(filepath.Join(dir, "codex"), []byte(script), 0o755); err != nil { //nolint:gosec // test stub must be executable
		t.Fatal(err)
	}
}

func dispatchEnv(home, session, runs, stubDir, scope string) []string {
	env := []string{
		"HOME=" + home,
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

// TestOwnerWakeDirectiveLands drives a real in-process daemon: seed the subject,
// register the owner via OpAgentStart, dispatch --dispatch --owner, and observe
// the worker's wake directive arrive naming the reply file and carrying no payload.
func TestOwnerWakeDirectiveLands(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)

	srv, err := buildServer()
	if err != nil {
		t.Fatal(err)
	}
	const session = "sess-wake"
	scope := canonicalScope(t)
	sub, err := store.NewSubjectStore(srv.DB()).
		Create(context.Background(), "0123456789abcdef0123456789abcdef", "codex-wake", session, scope, 0, statusOpen)
	if err != nil {
		t.Fatalf("seed subject: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	served := make(chan error, 1)
	go func() { served <- srv.Serve(ctx) }()
	defer func() {
		cancel()
		<-served
	}()
	waitDaemonReady(t)
	registerOwner(t, session, scope, "owner-1")

	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	var stdout, stderr bytes.Buffer
	c := exec.Command(bin, "--dispatch", "--owner", "owner-1", "ping") //nolint:gosec // drives the built binary under test
	c.Dir = scope
	c.Env = dispatchEnv(home, session, runs, stubDir, scope)
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Run(); err != nil {
		t.Fatalf("dispatch: %v\nstderr: %s", err, stderr.String())
	}
	reply := stdoutLine(stdout.String(), "REPLY_FILE: ")
	if reply == "" {
		t.Fatalf("no REPLY_FILE printed:\n%s", stdout.String())
	}
	t.Cleanup(func() { killLane(filepath.Dir(reply)) })

	waitForWake(t, srv.DB(), sub.ID, "owner-1", reply)
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

// TestOwnerWakeFallsBackToClaudePID seeds the subject with a claude pid, then
// wakes with a rotated session id: resolution must fall back to (pid, scope) —
// the production survival path when CLAUDE_CODE_SESSION_ID rotates mid-run.
func TestOwnerWakeFallsBackToClaudePID(t *testing.T) {
	testExe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	home := shortHome(t)

	srv, err := buildServer()
	if err != nil {
		t.Fatal(err)
	}
	scope := canonicalScope(t)
	sub, err := store.NewSubjectStore(srv.DB()).
		Create(context.Background(), "fedcba9876543210fedcba9876543210", "codex-rot", "sess-original", scope, 4242, statusOpen)
	if err != nil {
		t.Fatalf("seed subject: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	served := make(chan error, 1)
	go func() { served <- srv.Serve(ctx) }()
	defer func() {
		cancel()
		<-served
	}()
	waitDaemonReady(t)
	registerOwner(t, "sess-original", scope, "owner-rot")

	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	stub := filepath.Join(stubDir, "codex")

	sdir, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	reply := filepath.Join(sdir, "codex-r-x")
	replyTmp := reply + ".tmp"
	question := filepath.Join(sdir, "codex-q-x")
	logf := filepath.Join(sdir, "codex-q-x.log")
	writeFile(t, question, "ping\n")
	writeFile(t, filepath.Join(sdir, "meta"), reply+"\n"+logf+"\n")
	spec := cmdSpec{
		Argv: []string{stub, "-o", replyTmp}, Question: question, Reply: reply, ReplyTmp: replyTmp, Log: logf,
		Owner: "owner-rot", Session: "sess-rotated", Scope: scope, ClaudePID: 4242,
	}
	cb, _ := json.Marshal(spec)
	writeFile(t, filepath.Join(sdir, "cmd"), string(cb))

	child := exec.Command(testExe, "-test.run=XXX_NO_TEST_XXX") //nolint:gosec // re-execs the test binary as the worker child
	child.Env = append(os.Environ(),
		"HOME="+home,
		"CODEX_ASK_TEST_WORKER_SDIR="+sdir,
		"CODEX_ASK_TEST_WAKE_MS=5000",
		"PATH="+stubDir+string(os.PathListSeparator)+os.Getenv("PATH"),
	)
	if out, cerr := child.CombinedOutput(); cerr != nil {
		t.Fatalf("worker exited non-zero: %v\n%s", cerr, out)
	}
	waitForWake(t, srv.DB(), sub.ID, "owner-rot", reply)
}

// TestWorkerWakeFailsOpenWhenDaemonUnreachable runs the real worker with no
// daemon and a broken autostart (os.Executable is the test binary, which exits on
// a "daemon" argv): the worker still writes terminal status and exits 0.
func TestWorkerWakeFailsOpenWhenDaemonUnreachable(t *testing.T) {
	testExe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	stub := filepath.Join(stubDir, "codex")

	sdir, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	reply := filepath.Join(sdir, "codex-r-x")
	replyTmp := reply + ".tmp"
	question := filepath.Join(sdir, "codex-q-x")
	logf := filepath.Join(sdir, "codex-q-x.log")
	writeFile(t, question, "ping\n")
	writeFile(t, filepath.Join(sdir, "meta"), reply+"\n"+logf+"\n")
	spec := cmdSpec{
		Argv: []string{stub, "-o", replyTmp}, Question: question, Reply: reply, ReplyTmp: replyTmp, Log: logf,
		Owner: "owner-x", Session: "sess-dead", Scope: "/no/such/scope", ClaudePID: 0,
	}
	cb, _ := json.Marshal(spec)
	writeFile(t, filepath.Join(sdir, "cmd"), string(cb))

	child := exec.Command(testExe, "-test.run=XXX_NO_TEST_XXX") //nolint:gosec // re-execs the test binary as the worker child
	child.Env = append(os.Environ(),
		"HOME="+home,
		"CODEX_ASK_TEST_WORKER_SDIR="+sdir,
		"CODEX_ASK_TEST_WAKE_MS=1500",
		"PATH="+stubDir+string(os.PathListSeparator)+os.Getenv("PATH"),
	)
	out, err := child.CombinedOutput()
	if err != nil {
		t.Fatalf("worker exited non-zero (not fail-open): %v\n%s", err, out)
	}
	if got := strings.TrimSpace(readFile(filepath.Join(sdir, "status"))); got != "0" {
		t.Fatalf("status = %q, want 0 (terminal status must survive a dead daemon)", got)
	}
	if got := strings.TrimSpace(readFile(reply)); got != "pong" {
		t.Fatalf("reply = %q, want pong", got)
	}
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil { //nolint:gosec // test fixture file
		t.Fatal(err)
	}
}

func waitDaemonReady(t *testing.T) {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
		client, err := newClient(ctx)
		if err == nil {
			health, herr := client.Health(ctx)
			_ = client.Close()
			cancel()
			if herr == nil && health.Build == appVersion {
				return
			}
			time.Sleep(20 * time.Millisecond)
			continue
		}
		cancel()
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("daemon never became ready")
}

func registerOwner(t *testing.T, session, scope, agentID string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client, err := newClient(ctx)
	if err != nil {
		t.Fatalf("client: %v", err)
	}
	defer func() { _ = client.Close() }()
	body, _ := json.Marshal(map[string]any{
		"agent_id": agentID, "agent_type": "worker", "session_id": session, "parent_agent_id": "",
	})
	reply, err := client.Do(ctx, daemon.Envelope{
		Op: daemon.OpAgentStart, Session: session, ClaudePID: 0, Scope: scope, Body: body,
	})
	if err != nil {
		t.Fatalf("agent-start: %v", err)
	}
	if !reply.OK {
		t.Fatalf("agent-start not ok: %s", reply.Error)
	}
}

// waitForWake polls the directives store until a directive to agentID names the
// reply file, asserting the wake is content-free (never carries the payload).
func waitForWake(t *testing.T, db *sql.DB, subjectID, agentID, replyFile string) {
	t.Helper()
	deadline := time.Now().Add(25 * time.Second)
	var seen []string
	for time.Now().Before(deadline) {
		seen = directiveTexts(t, db, subjectID, agentID)
		for _, text := range seen {
			if strings.Contains(text, replyFile) {
				if strings.Contains(text, "pong") {
					t.Fatalf("wake directive carried the reply payload: %q", text)
				}
				return
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("wake naming %q never landed; directives: %v", replyFile, seen)
}

func directiveTexts(t *testing.T, db *sql.DB, subjectID, agentID string) []string {
	t.Helper()
	rows, err := db.QueryContext(context.Background(),
		`SELECT text FROM directives WHERE subject_id=? AND agent_id=? ORDER BY id`, subjectID, agentID)
	if err != nil {
		t.Fatalf("query directives: %v", err)
	}
	defer func() { _ = rows.Close() }()
	var out []string
	for rows.Next() {
		var text string
		if err := rows.Scan(&text); err != nil {
			t.Fatalf("scan directive: %v", err)
		}
		out = append(out, text)
	}
	return out
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
