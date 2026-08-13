package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestRepoToplevelOfDetectsGitDir(t *testing.T) {
	clone := filepath.Join(mustTempDir(t), "clone")
	if err := os.MkdirAll(filepath.Join(clone, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	if got := repoToplevelOf(filepath.Join(clone, "lane")); got != clone {
		t.Fatalf("got %q, want %q", got, clone)
	}
}

func TestRepoToplevelOfDetectsGitFile(t *testing.T) {
	wt := filepath.Join(mustTempDir(t), "worktree")
	if err := os.MkdirAll(wt, 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(wt, ".git"), "gitdir: /elsewhere/.git/worktrees/wt\n")
	if got := repoToplevelOf(filepath.Join(wt, "nested", "lane")); got != wt {
		t.Fatalf("got %q, want %q", got, wt)
	}
}

func TestRepoToplevelOfPlainDir(t *testing.T) {
	if got := repoToplevelOf(filepath.Join(mustTempDir(t), "lane")); got != "" {
		t.Fatalf("got %q, want none", got)
	}
}

// TestRepoToplevelOfStopsBeforeHome: a version-controlled home dir must not
// reject the default runs base under ~/.cache, but a repo below $HOME still counts.
func TestRepoToplevelOfStopsBeforeHome(t *testing.T) {
	home := mustTempDir(t)
	t.Setenv("HOME", home)
	if err := os.MkdirAll(filepath.Join(home, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	if got := repoToplevelOf(filepath.Join(home, ".cache", "codex-ask", "runs", "x")); got != "" {
		t.Fatalf("home repo poisoned the runs base: %q", got)
	}
	clone := filepath.Join(home, "code", "clone")
	if err := os.MkdirAll(filepath.Join(clone, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	if got := repoToplevelOf(filepath.Join(clone, "lane")); got != clone {
		t.Fatalf("got %q, want %q", got, clone)
	}
}

// TestDispatchRefusesInRepoScratch: a -s lane inside a git checkout is refused
// regardless of codex-ask's own cwd — the fail-open that let a finder litter a
// clone with codex-q-*/codex-r-* artifacts.
func TestDispatchRefusesInRepoScratch(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	scope := canonicalScope(t)

	clone := filepath.Join(mustTempDir(t), "clone")
	if err := os.MkdirAll(filepath.Join(clone, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}

	var stdout, stderr bytes.Buffer
	c := exec.Command(bin, "-s", filepath.Join(clone, "lane"), "ping") //nolint:gosec // drives the built binary under test
	c.Dir = scope
	c.Env = dispatchEnv(home, "", runs, stubDir, scope)
	c.Stdout, c.Stderr = &stdout, &stderr
	err := c.Run()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 2 {
		t.Fatalf("want exit 2, got %v\nstdout: %s\nstderr: %s", err, stdout.String(), stderr.String())
	}
	if !strings.Contains(stderr.String(), "must be outside the repository at "+clone) {
		t.Fatalf("missing refusal:\n%s", stderr.String())
	}
	if _, err := os.Stat(filepath.Join(clone, "lane")); !os.IsNotExist(err) {
		t.Fatal("refusal still created the lane dir")
	}
}

// TestClassifyExpiredRegistrationIsTerminal: a dispatcher killed between
// publishing meta and recording the worker pid used to leave the lane classified
// pending forever, hanging --watch. Past --await's registration window it reads
// died instead.
func TestClassifyExpiredRegistrationIsTerminal(t *testing.T) {
	lane := mustTempDir(t)
	meta := filepath.Join(lane, "meta")
	writeFile(t, meta, filepath.Join(lane, "codex-r-x")+"\n"+filepath.Join(lane, "codex-q-x.log")+"\n")

	if got := classify(lane).state; got != "pending" {
		t.Fatalf("inside the registration window = %q, want pending", got)
	}
	aged := time.Now().Add(-(registerGraceS + 1) * time.Second)
	if err := os.Chtimes(meta, aged, aged); err != nil {
		t.Fatal(err)
	}
	if got := classify(lane).state; got != "died" {
		t.Fatalf("past the registration window = %q, want died", got)
	}
}

// TestAwaitRefusesInRepoTarget: --await validates its target the way dispatch
// validates --scratch, so a mistargeted recovery never mints a lane.lock inside a
// checkout — the guard --collect and --watch already carry.
func TestAwaitRefusesInRepoTarget(t *testing.T) {
	clone := filepath.Join(mustTempDir(t), "clone")
	lane := filepath.Join(clone, "lane")
	if err := os.MkdirAll(filepath.Join(clone, ".git"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(lane, 0o700); err != nil {
		t.Fatal(err)
	}

	_, stderr, code := askRun(t, mustTempDir(t), stubCodexReply, "--await", lane)
	if code != 2 {
		t.Fatalf("--await into a checkout exit %d, want 2\nstderr: %s", code, stderr)
	}
	if !strings.Contains(stderr, "must be outside the repository at "+clone) {
		t.Fatalf("missing refusal:\n%s", stderr)
	}
	if exists(filepath.Join(lane, "lane.lock")) {
		t.Fatal("refused --await still created a lane lock in the checkout")
	}
}

// TestAwaitRecoversDispatchedLane: the target guard must not cost recovery — both
// operand forms, the printed AWAIT: lane dir and the REPLY_FILE: path, still await.
func TestAwaitRecoversDispatchedLane(t *testing.T) {
	runs := mustTempDir(t)
	lane := filepath.Join(mustTempDir(t), "lane")
	t.Cleanup(func() { killLane(lane) })

	stdout, stderr, code := askRun(t, runs, stubCodexReply, "-s", lane, "--dispatch", "ping")
	if code != 0 {
		t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
	}
	reply := stdoutLine(stdout, "REPLY_FILE: ")
	for _, target := range []string{lane, reply} {
		out, errOut, code := askRun(t, runs, stubCodexReply, "--await", target)
		if code != 0 {
			t.Fatalf("--await %s exit %d\nstderr: %s", target, code, errOut)
		}
		if got := stdoutLine(out, "REPLY_FILE: "); got != reply {
			t.Fatalf("--await %s printed reply %q, want %q", target, got, reply)
		}
	}
}

func TestDispatchAcceptsPlainScratch(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	scope := canonicalScope(t)
	lane := filepath.Join(mustTempDir(t), "lane")

	var stdout, stderr bytes.Buffer
	c := exec.Command(bin, "-s", lane, "--dispatch", "ping") //nolint:gosec // drives the built binary under test
	c.Dir = scope
	c.Env = dispatchEnv(home, "", runs, stubDir, scope)
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Run(); err != nil {
		t.Fatalf("dispatch: %v\nstderr: %s", err, stderr.String())
	}
	t.Cleanup(func() { killLane(lane) })
	if reply := stdoutLine(stdout.String(), "REPLY_FILE: "); filepath.Dir(reply) != lane {
		t.Fatalf("reply %q not staged in lane %s", reply, lane)
	}
}

// TestClassifyIsReadOnly: classify must never delete — a crafted meta must not
// cost an arbitrary <path>.tmp, and a died-past-grace lane's staged reply temp
// belongs to a worker that may still be live (the dispatcher died, not codex).
func TestClassifyIsReadOnly(t *testing.T) {
	crafted := mustTempDir(t)
	victim := filepath.Join(mustTempDir(t), "victim")
	writeFile(t, victim+".tmp", "precious\n")
	writeFile(t, filepath.Join(crafted, "meta"), victim+"\n"+filepath.Join(crafted, "q.log")+"\n")
	writeFile(t, filepath.Join(crafted, "status"), "1\n")
	if got := classify(crafted).state; got != "failed" {
		t.Fatalf("crafted lane = %q, want failed", got)
	}
	if !isFile(victim + ".tmp") {
		t.Fatal("classify deleted the path a crafted meta named")
	}

	lane := mustTempDir(t)
	reply := filepath.Join(lane, "codex-r-x")
	writeFile(t, reply+".tmp", "staged\n")
	meta := filepath.Join(lane, "meta")
	writeFile(t, meta, reply+"\n"+filepath.Join(lane, "codex-q-x.log")+"\n")
	aged := time.Now().Add(-(registerGraceS + 1) * time.Second)
	if err := os.Chtimes(meta, aged, aged); err != nil {
		t.Fatal(err)
	}
	if got := classify(lane).state; got != "died" {
		t.Fatalf("past the registration window = %q, want died", got)
	}
	if !isFile(reply + ".tmp") {
		t.Fatal("classify destroyed a live worker's staged reply")
	}
}

func TestReadPidRejectsZero(t *testing.T) {
	pidFile := filepath.Join(mustTempDir(t), "pid")
	writeFile(t, pidFile, "0\n")
	if _, ok := readPid(pidFile); ok {
		t.Fatal("pid 0 accepted; kill(0,0) signals the caller's own process group")
	}
}

// TestDispatchReapsPriorGenerationReplyTmp: lane reuse reaps the prior
// generation's orphaned <reply>.tmp under dispatch's exclusive lock.
func TestDispatchReapsPriorGenerationReplyTmp(t *testing.T) {
	runs := mustTempDir(t)
	lane := filepath.Join(mustTempDir(t), "lane")
	stdout, stderr, code := askRun(t, runs, stubCodexReply, "-s", lane, "ping")
	if code != 0 {
		t.Fatalf("seed run exit %d\nstderr: %s", code, stderr)
	}
	oldReply := stdoutLine(stdout, "REPLY_FILE: ")
	writeFile(t, oldReply+".tmp", "orphan\n")

	_, stderr, code = askRun(t, runs, stubCodexReply, "-s", lane, "--dispatch", "ping")
	if code != 0 {
		t.Fatalf("reuse dispatch exit %d\nstderr: %s", code, stderr)
	}
	t.Cleanup(func() { killLane(lane) })
	if exists(oldReply + ".tmp") {
		t.Fatal("dispatch left the prior generation's staged reply temp")
	}
}

// TestDispatchReapIsLaneLocal: the reuse sweep only reaps a lane-local
// codex-r-* path, never whatever a corrupt meta names.
func TestDispatchReapIsLaneLocal(t *testing.T) {
	runs := mustTempDir(t)
	lane := filepath.Join(mustTempDir(t), "lane")
	if err := os.MkdirAll(lane, 0o700); err != nil {
		t.Fatal(err)
	}
	victim := filepath.Join(mustTempDir(t), "codex-r-victim")
	writeFile(t, victim+".tmp", "precious\n")
	writeFile(t, filepath.Join(lane, "meta"), victim+"\n"+filepath.Join(lane, "q.log")+"\n")
	writeFile(t, filepath.Join(lane, "status"), "0\n")

	_, stderr, code := askRun(t, runs, stubCodexReply, "-s", lane, "--dispatch", "ping")
	if code != 0 {
		t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
	}
	t.Cleanup(func() { killLane(lane) })
	if !isFile(victim + ".tmp") {
		t.Fatal("dispatch reaped a .tmp outside the lane")
	}
}

// prunableLane builds an aged terminal run dir under runs, prunable by --ps.
func prunableLane(t *testing.T, parent string) string {
	t.Helper()
	sdir, err := os.MkdirTemp(parent, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	reply := filepath.Join(sdir, "codex-r-x")
	writeFile(t, reply, "pong\n")
	writeFile(t, filepath.Join(sdir, "meta"), reply+"\n"+filepath.Join(sdir, "codex-q-x.log")+"\n")
	writeFile(t, filepath.Join(sdir, "status"), "0\n")
	aged := time.Now().Add(-(pruneAgeS + 3600) * time.Second)
	for _, f := range []string{"meta", "status"} {
		if err := os.Chtimes(filepath.Join(sdir, f), aged, aged); err != nil {
			t.Fatal(err)
		}
	}
	return sdir
}

func runPs(t *testing.T, runs string) {
	t.Helper()
	c := exec.Command(codexAskBin(t), "--ps") //nolint:gosec // drives the built binary under test
	c.Env = dispatchEnv(shortHome(t), "", runs, mustTempDir(t), runs)
	if out, err := c.CombinedOutput(); err != nil {
		t.Fatalf("--ps: %v\n%s", err, out)
	}
}

// TestPruneSkipsLockedLane: --ps takes the lane lock non-blocking before rmtree,
// so a lane whose lock is held (mid-reuse, awaited, watched) survives the prune.
func TestPruneSkipsLockedLane(t *testing.T) {
	runs := mustTempDir(t)
	sdir := prunableLane(t, runs)

	lock := acquireLaneLock(sdir, true)
	runPs(t, runs)
	if !isFile(filepath.Join(sdir, "meta")) {
		t.Fatal("--ps pruned a lane whose lock was held")
	}
	releaseLaneLock(lock)

	runPs(t, runs)
	if _, err := os.Stat(sdir); !os.IsNotExist(err) {
		t.Fatal("released aged terminal lane survived the prune")
	}
}

// TestPruneSkipsSymlinkedEntry: the --ps walk is lstat-based, so a symlinked
// registry entry can no longer route the prune's rmtree outside the base.
func TestPruneSkipsSymlinkedEntry(t *testing.T) {
	runs := mustTempDir(t)
	victimContainer := mustTempDir(t)
	victimLane := prunableLane(t, victimContainer)
	if err := os.Symlink(victimContainer, filepath.Join(runs, "escape")); err != nil {
		t.Fatal(err)
	}

	runPs(t, runs)
	if !isFile(filepath.Join(victimLane, "meta")) {
		t.Fatal("--ps pruned through a symlinked registry entry")
	}
}

// TestRelativeXDGCacheHomeRefuses: a relative XDG_CACHE_HOME would put the
// registry — and its prune — under the cwd; refuse before creating anything.
func TestRelativeXDGCacheHomeRefuses(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	cwd := mustTempDir(t)

	var stdout, stderr bytes.Buffer
	c := exec.Command(bin, "--ps") //nolint:gosec // drives the built binary under test
	c.Dir = cwd
	c.Env = []string{"HOME=" + home, "XDG_CACHE_HOME=rel/cache", "PATH=" + os.Getenv("PATH")}
	c.Stdout, c.Stderr = &stdout, &stderr
	err := c.Run()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 2 {
		t.Fatalf("want exit 2, got %v\nstderr: %s", err, stderr.String())
	}
	if !strings.Contains(stderr.String(), "XDG_CACHE_HOME must be an absolute path") {
		t.Fatalf("missing refusal:\n%s", stderr.String())
	}
	if _, err := os.Stat(filepath.Join(cwd, "rel")); !os.IsNotExist(err) {
		t.Fatal("refusal still created the relative base under cwd")
	}
}

// TestWatchDiesOnGenerationSwap: --watch pins the armed generation under the
// shared lane lock the way --await does; a meta swap underneath it dies loudly
// instead of silently watching the replacement run.
func TestWatchDiesOnGenerationSwap(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)
	sdir, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(sdir, "meta"),
		filepath.Join(sdir, "codex-r-a")+"\n"+filepath.Join(sdir, "codex-q-a.log")+"\n")

	c := exec.Command(bin, "--watch", sdir) //nolint:gosec // drives the built binary under test
	c.Env = dispatchEnv(home, "", runs, mustTempDir(t), sdir)
	var stdout, stderr bytes.Buffer
	c.Stdout, c.Stderr = &stdout, &stderr
	if err := c.Start(); err != nil {
		t.Fatal(err)
	}
	waitForSharedLock(t, filepath.Join(sdir, "lane.lock"))
	writeFile(t, filepath.Join(sdir, "meta"),
		filepath.Join(sdir, "codex-r-b")+"\n"+filepath.Join(sdir, "codex-q-b.log")+"\n")

	done := make(chan error, 1)
	go func() { done <- c.Wait() }()
	select {
	case werr := <-done:
		var exit *exec.ExitError
		if !errors.As(werr, &exit) || exit.ExitCode() != 1 {
			t.Fatalf("want exit 1, got %v\nstderr: %s", werr, stderr.String())
		}
	case <-time.After(15 * time.Second):
		_ = c.Process.Kill()
		t.Fatalf("watch never noticed the generation swap; stderr: %s", stderr.String())
	}
	if !strings.Contains(stderr.String(), "generation changed") {
		t.Fatalf("missing generation refusal:\n%s", stderr.String())
	}
}

// TestWatchEmitsWhileSiblingLaneLocked: one exclusively-held lane (a foreground
// dispatcher mid-run) must not stall the other lanes' settle records.
func TestWatchEmitsWhileSiblingLaneLocked(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)
	busy, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(busy, "meta"),
		filepath.Join(busy, "codex-r-a")+"\n"+filepath.Join(busy, "codex-q-a.log")+"\n")
	settledLane, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	reply := filepath.Join(settledLane, "codex-r-b")
	writeFile(t, reply, "pong\n")
	writeFile(t, filepath.Join(settledLane, "meta"),
		reply+"\n"+filepath.Join(settledLane, "codex-q-b.log")+"\n")
	writeFile(t, filepath.Join(settledLane, "status"), "0\n")

	lock := acquireLaneLock(busy, true)
	outPath := filepath.Join(mustTempDir(t), "watch.out")
	outFile, err := os.Create(outPath) //nolint:gosec // the test's own capture file
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = outFile.Close() }()
	c := exec.Command(bin, "--watch", busy, settledLane) //nolint:gosec // drives the built binary under test
	c.Env = dispatchEnv(home, "", runs, mustTempDir(t), runs)
	c.Stdout = outFile
	if err := c.Start(); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) && !strings.Contains(readFile(outPath), reply) {
		time.Sleep(50 * time.Millisecond)
	}
	if !strings.Contains(readFile(outPath), reply) {
		_ = c.Process.Kill()
		t.Fatalf("locked sibling stalled the settled lane's record; output: %s", readFile(outPath))
	}
	if err := c.Process.Signal(syscall.Signal(0)); err != nil {
		t.Fatal("watch exited while the busy lane was still unsettled")
	}

	releaseLaneLock(lock)
	writeFile(t, filepath.Join(busy, "codex-r-a"), "pong\n")
	writeFile(t, filepath.Join(busy, "status"), "0\n")
	done := make(chan error, 1)
	go func() { done <- c.Wait() }()
	select {
	case werr := <-done:
		if werr != nil {
			t.Fatalf("watch exited non-zero: %v\n%s", werr, readFile(outPath))
		}
	case <-time.After(15 * time.Second):
		_ = c.Process.Kill()
		t.Fatalf("watch never settled the released lane; output: %s", readFile(outPath))
	}
	if got := len(strings.Fields(strings.TrimSpace(readFile(outPath)))); got != 2 {
		t.Fatalf("want 2 records, got %d:\n%s", got, readFile(outPath))
	}
}

// TestWatchDuplicateOperandsSettleOnce: a duplicated target must emit one record
// and release its lock once — a double release would abort the watch mid-run.
func TestWatchDuplicateOperandsSettleOnce(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)
	sdir, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	reply := filepath.Join(sdir, "codex-r-x")
	writeFile(t, reply, "pong\n")
	writeFile(t, filepath.Join(sdir, "meta"), reply+"\n"+filepath.Join(sdir, "codex-q-x.log")+"\n")
	writeFile(t, filepath.Join(sdir, "status"), "0\n")

	c := exec.Command(bin, "--watch", sdir, sdir) //nolint:gosec // drives the built binary under test
	c.Env = dispatchEnv(home, "", runs, mustTempDir(t), runs)
	out, err := c.CombinedOutput()
	if err != nil {
		t.Fatalf("watch with duplicate operands: %v\n%s", err, out)
	}
	if got := len(strings.Fields(strings.TrimSpace(string(out)))); got != 1 {
		t.Fatalf("want 1 record, got %d:\n%s", got, out)
	}
}

// TestForegroundLaneLockSurvivesGC: the foreground exclusive lock must outlive
// the collector — GOGC=1 once let the os.File finalizer close (and release) the
// lock mid-run, so a same-lane replacement reached the busy refusal.
func TestForegroundLaneLockSurvivesGC(t *testing.T) {
	bin := codexAskBin(t)
	home := shortHome(t)
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	release := filepath.Join(mustTempDir(t), "release")
	writeStub(t, stubDir, "#!/bin/sh\n"+
		"out=\"\"; prev=\"\"\n"+
		"for a in \"$@\"; do [ \"$prev\" = \"-o\" ] && out=$a; prev=$a; done\n"+
		"cat > /dev/null\n"+
		"while [ ! -f "+release+" ]; do sleep 0.05; done\n"+
		"[ -n \"$out\" ] && echo done > \"$out\"\n")
	scope := canonicalScope(t)
	lane := filepath.Join(mustTempDir(t), "lane")
	t.Cleanup(func() { killLane(lane) })
	env := append(dispatchEnv(home, "", runs, stubDir, scope), "GOGC=1")

	first := exec.Command(bin, "-s", lane, "ping") //nolint:gosec // drives the built binary under test
	first.Dir = scope
	first.Env = env
	if err := first.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.WriteFile(release, []byte("go\n"), 0o600) })
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && !isFile(filepath.Join(lane, "pid")) {
		time.Sleep(20 * time.Millisecond)
	}
	if !isFile(filepath.Join(lane, "pid")) {
		t.Fatal("run never registered a pid")
	}

	var repOut, repErr bytes.Buffer
	replacement := exec.Command(bin, "-s", lane, "ping") //nolint:gosec // drives the built binary under test
	replacement.Dir = scope
	replacement.Env = env
	replacement.Stdout, replacement.Stderr = &repOut, &repErr
	if err := replacement.Start(); err != nil {
		t.Fatal(err)
	}
	repDone := make(chan error, 1)
	go func() { repDone <- replacement.Wait() }()
	select {
	case <-repDone:
		t.Fatalf("same-lane replacement escaped the foreground lock:\n%s%s", repOut.String(), repErr.String())
	case <-time.After(1500 * time.Millisecond):
	}

	writeFile(t, release, "go\n")
	if err := first.Wait(); err != nil {
		t.Fatalf("first run: %v", err)
	}
	if err := <-repDone; err != nil {
		t.Fatalf("replacement exit: %v\nstdout: %s\nstderr: %s", err, repOut.String(), repErr.String())
	}
}

// waitForSharedLock spins until an exclusive non-blocking flock on path fails,
// proving another process holds it shared.
func waitForSharedLock(t *testing.T, path string) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if f, err := os.OpenFile(path, os.O_RDWR, 0); err == nil { //nolint:gosec // probes the test lane's own lock file
			err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
			if err != nil {
				_ = f.Close()
				return
			}
			_ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
			_ = f.Close()
		}
		time.Sleep(25 * time.Millisecond)
	}
	t.Fatal("watch never took the shared lane lock")
}

// TestNamedLaneMintsUnderRegistry: -l RUN/LANE owns the path — the lane lands
// under the registry's codex-run.<RUN> container, no caller-supplied dir.
func TestNamedLaneMintsUnderRegistry(t *testing.T) {
	runs := canonicalScope(t)
	stdout, stderr, code := askRun(t, runs, stubCodexReply, "-l", "myrun/review", "ping")
	if code != 0 {
		t.Fatalf("exit %d\nstderr: %s", code, stderr)
	}
	lane := filepath.Join(runs, "codex-run.myrun", "review")
	if got := filepath.Dir(stdoutLine(stdout, "REPLY_FILE: ")); got != lane {
		t.Fatalf("reply landed in %q, want %q", got, lane)
	}
	if got := strings.TrimSpace(readFile(filepath.Join(lane, "status"))); got != "0" {
		t.Fatalf("status = %q, want 0", got)
	}
}

// TestBareLaneNameGroupsBySession: -l LANE without RUN groups under the calling
// Claude session's run, so same-session fan-out lanes share a container with
// zero coordination.
func TestBareLaneNameGroupsBySession(t *testing.T) {
	runs := canonicalScope(t)
	_, stderr, code := askRunSession(t, runs, "sess-1", stubCodexReply, "-l", "review", "ping")
	if code != 0 {
		t.Fatalf("exit %d\nstderr: %s", code, stderr)
	}
	if !isFile(filepath.Join(runs, "codex-run.sess-1", "review", "status")) {
		t.Fatal("lane did not land under the session run")
	}

	_, stderr, code = askRun(t, runs, stubCodexReply, "-l", "review", "ping")
	if code != 2 {
		t.Fatalf("bare -l without a session exit %d, want 2\nstderr: %s", code, stderr)
	}
	if !strings.Contains(stderr, "CLAUDE_CODE_SESSION_ID") {
		t.Fatalf("missing session hint:\n%s", stderr)
	}
}

// TestLaneNameRejectsBadOperands: a bad -l mints nothing — path-segment rules
// match --mint-root's, and -l/-s never combine.
func TestLaneNameRejectsBadOperands(t *testing.T) {
	runs := mustTempDir(t)
	bads := [][]string{
		{"-l", "../evil", "ping"},
		{"-l", "a/b/c", "ping"},
		{"-l", ".hidden", "ping"},
		{"-l", "", "ping"},
		{"-l", "ok", "-s", "/tmp/x", "ping"},
	}
	for _, args := range bads {
		if _, stderr, code := askRun(t, runs, stubCodexReply, args...); code != 2 {
			t.Fatalf("%v exit %d, want 2\nstderr: %s", args, code, stderr)
		}
	}
	if entries, _ := os.ReadDir(runs); len(entries) != 0 {
		t.Fatalf("refused -l still minted: %v", entries)
	}
}

// TestMintRunRosterIsNameOnlyAndIdempotent: --mint-run speaks names on both
// sides — no path crosses the CLI — and re-minting an existing run is a no-op
// that never removes state.
func TestMintRunRosterIsNameOnlyAndIdempotent(t *testing.T) {
	runs := mustTempDir(t)
	stdout, stderr, code := askRun(t, runs, stubCodexReply, "--mint-run", "sweep", "review", "refute")
	if code != 0 {
		t.Fatalf("exit %d\nstderr: %s", code, stderr)
	}
	for _, want := range []string{"RUN: sweep\n", "LANE: sweep/review\n", "LANE: sweep/refute\n"} {
		if !strings.Contains(stdout, want) {
			t.Fatalf("missing %q in:\n%s", want, stdout)
		}
	}
	if strings.Contains(stdout, runs) {
		t.Fatalf("--mint-run leaked a path:\n%s", stdout)
	}
	for _, lane := range []string{"review", "refute"} {
		if !isDir(filepath.Join(runs, "codex-run.sweep", lane)) {
			t.Fatalf("lane %s not minted", lane)
		}
	}
	marker := filepath.Join(runs, "codex-run.sweep", "review", "status")
	writeFile(t, marker, "0\n")
	if _, stderr, code := askRun(t, runs, stubCodexReply, "--mint-run", "sweep", "review", "refute"); code != 0 {
		t.Fatalf("re-mint exit %d\nstderr: %s", code, stderr)
	}
	if !isFile(marker) {
		t.Fatal("re-mint removed lane state")
	}
	if _, _, code := askRun(t, runs, stubCodexReply, "--mint-run", "sweep"); code != 2 {
		t.Fatalf("laneless --mint-run exit %d, want 2", code)
	}
}

// TestCollectAndWatchByRunName: the gate side speaks the same names as -l — a
// RUN operand resolves to its registry container, and --collect with no operand
// collects the calling session's run.
func TestCollectAndWatchByRunName(t *testing.T) {
	runs := canonicalScope(t)
	for _, lane := range []string{"a", "b"} {
		if _, stderr, code := askRun(t, runs, stubCodexReply, "-l", "grp/"+lane, "ping"); code != 0 {
			t.Fatalf("lane %s exit %d\nstderr: %s", lane, code, stderr)
		}
	}
	stdout, stderr, code := askRun(t, runs, stubCodexReply, "--collect", "grp")
	if code != 0 {
		t.Fatalf("--collect grp exit %d\nstderr: %s", code, stderr)
	}
	for _, want := range []string{`"lane":"a"`, `"lane":"b"`} {
		if !strings.Contains(stdout, want) {
			t.Fatalf("missing %s in:\n%s", want, stdout)
		}
	}

	if _, stderr, code := askRunSession(t, runs, "sess-2", stubCodexReply, "-l", "solo", "ping"); code != 0 {
		t.Fatalf("session lane exit %d\nstderr: %s", code, stderr)
	}
	stdout, stderr, code = askRunSession(t, runs, "sess-2", stubCodexReply, "--collect")
	if code != 0 {
		t.Fatalf("bare --collect exit %d\nstderr: %s", code, stderr)
	}
	if !strings.Contains(stdout, `"lane":"solo"`) {
		t.Fatalf("session collect missed its lane:\n%s", stdout)
	}
	if _, stderr, code := askRun(t, runs, stubCodexReply, "--collect"); code != 2 {
		t.Fatalf("bare --collect without a session exit %d, want 2\nstderr: %s", code, stderr)
	}

	stdout, stderr, code = askRun(t, runs, stubCodexReply, "--watch", "grp")
	if code != 0 {
		t.Fatalf("--watch grp exit %d\nstderr: %s", code, stderr)
	}
	if !strings.Contains(stdout, `"state":"completed"`) {
		t.Fatalf("watch by name emitted no settled record:\n%s", stdout)
	}
}

// TestAwaitByRunLaneName: --await RUN/LANE recovers a named dispatch without
// the printed absolute path.
func TestAwaitByRunLaneName(t *testing.T) {
	runs := canonicalScope(t)
	lane := filepath.Join(runs, "codex-run.grp2", "a")
	t.Cleanup(func() { killLane(lane) })
	stdout, stderr, code := askRun(t, runs, stubCodexReply, "-l", "grp2/a", "--dispatch", "ping")
	if code != 0 {
		t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
	}
	reply := stdoutLine(stdout, "REPLY_FILE: ")
	out, errOut, code := askRun(t, runs, stubCodexReply, "--await", "grp2/a")
	if code != 0 {
		t.Fatalf("--await grp2/a exit %d\nstderr: %s", code, errOut)
	}
	if got := stdoutLine(out, "REPLY_FILE: "); got != reply {
		t.Fatalf("--await grp2/a printed reply %q, want %q", got, reply)
	}
	if _, errOut, code := askRun(t, runs, stubCodexReply, "--await", "grp2"); code != 2 {
		t.Fatalf("laneless --await name exit %d, want 2\nstderr: %s", code, errOut)
	}
}

func agedNamedLane(t *testing.T, container, name string) string {
	t.Helper()
	sdir := filepath.Join(container, name)
	if err := os.MkdirAll(sdir, 0o700); err != nil {
		t.Fatal(err)
	}
	reply := filepath.Join(sdir, "codex-r-x")
	writeFile(t, reply, "pong\n")
	writeFile(t, filepath.Join(sdir, "meta"), reply+"\n"+filepath.Join(sdir, "codex-q-x.log")+"\n")
	writeFile(t, filepath.Join(sdir, "status"), "0\n")
	aged := time.Now().Add(-(pruneAgeS + 3600) * time.Second)
	for _, f := range []string{"meta", "status"} {
		if err := os.Chtimes(filepath.Join(sdir, f), aged, aged); err != nil {
			t.Fatal(err)
		}
	}
	return sdir
}

// TestPruneReapsMintedContainer: fan-out containers used to leak forever — lane
// basenames carry no run prefix, so the prune never matched them and the
// container never emptied. Lanes inside a minted container (aged roster no-runs
// included) now prune, and the emptied container goes with them; unaged lanes
// and caller-named containers survive.
func TestPruneReapsMintedContainer(t *testing.T) {
	runs := mustTempDir(t)
	container := filepath.Join(runs, "codex-root.42")
	agedNamedLane(t, container, "review")
	rosterLane := filepath.Join(container, "refute")
	if err := os.MkdirAll(rosterLane, 0o700); err != nil {
		t.Fatal(err)
	}
	aged := time.Now().Add(-(pruneAgeS + 3600) * time.Second)
	if err := os.Chtimes(rosterLane, aged, aged); err != nil {
		t.Fatal(err)
	}

	freshLane := filepath.Join(runs, "codex-run.fresh", "review")
	if err := os.MkdirAll(freshLane, 0o700); err != nil {
		t.Fatal(err)
	}

	foreign := filepath.Join(runs, "keep")
	agedNamedLane(t, foreign, "lane")

	runPs(t, runs)
	if _, err := os.Stat(container); !os.IsNotExist(err) {
		t.Fatal("aged fan-out container survived the prune")
	}
	if !isDir(freshLane) {
		t.Fatal("unaged roster lane was pruned")
	}
	if !isFile(filepath.Join(foreign, "lane", "meta")) {
		t.Fatal("pruned a lane inside a caller-named container")
	}
}
