package main

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

// detachWorker: Go can't fork() mid-process, so mirror the Python double-fork with
// a re-exec. StartProcess a setsid'd middle (its own session escapes the pgroup
// kill); the middle re-execs the worker and exits at once, orphaning it to PID 1
// (escapes the ps-walked descendant kill). The parent records lstart THEN pid,
// closing the window where a reader sees a pid with no recycle guard.
func detachWorker(sdir string) {
	r, w, err := os.Pipe()
	if err != nil {
		_ = atomicWrite(join(sdir, "status"), "126\n")
		return
	}
	devnull, err := os.OpenFile(os.DevNull, os.O_RDWR, 0)
	if err != nil {
		_ = r.Close()
		_ = w.Close()
		_ = atomicWrite(join(sdir, "status"), "126\n")
		return
	}
	proc, err := os.StartProcess(selfPath, []string{selfPath, "--detach-middle", sdir}, &os.ProcAttr{ //nolint:gosec // re-exec of this binary's own resolved path, not user input
		Files: []*os.File{devnull, devnull, devnull, w},
		Sys:   &syscall.SysProcAttr{Setsid: true},
	})
	_ = w.Close()
	_ = devnull.Close()
	if err != nil {
		_ = r.Close()
		_ = atomicWrite(join(sdir, "status"), "126\n")
		return
	}
	_, _ = proc.Wait() // reap the middle child
	buf := make([]byte, 64)
	nRead, _ := r.Read(buf)
	_ = r.Close()
	gpid, e := strconv.Atoi(strings.TrimSpace(string(buf[:nRead])))
	if e != nil || gpid <= 0 {
		_ = atomicWrite(join(sdir, "status"), "126\n")
		return
	}
	_ = atomicWrite(join(sdir, "lstart"), procLstart(gpid)+"\n")
	_ = atomicWrite(join(sdir, "pid"), strconv.Itoa(gpid)+"\n")
}

// detachMiddle: already setsid'd by the parent's StartProcess. Re-exec the worker
// (inheriting this session), pipe its pid back on fd 3, and exit at once so the
// worker is orphaned. A worker-spawn failure exits without a pid → status 126.
func detachMiddle(sdir string) {
	pipe := os.NewFile(3, "pipe")
	devnull, err := os.OpenFile(os.DevNull, os.O_RDWR, 0)
	if err != nil {
		os.Exit(127)
	}
	proc, err := os.StartProcess(selfPath, []string{selfPath, "--worker", sdir}, &os.ProcAttr{ //nolint:gosec // re-exec of this binary's own resolved path, not user input
		Files: []*os.File{devnull, devnull, devnull},
	})
	_ = devnull.Close()
	if err != nil {
		os.Exit(127)
	}
	if pipe != nil {
		_, _ = pipe.WriteString(strconv.Itoa(proc.Pid) + "\n")
		_ = pipe.Close()
	}
	os.Exit(0)
}

// runWorker: the detached side. Runs the pinned codex exec from the cmd file,
// stages the reply (rename only on rc 0 + non-empty), and writes codex's code to
// status. No failure mode leaves the run statusless (which would hang a waiter).
func runWorker(sdir string) {
	replyTmp := ""
	rc := 126
	var cmd cmdSpec
	loaded := false
	func() {
		defer func() {
			if replyTmp != "" {
				_ = os.Remove(replyTmp) // sweep the staging file; Remove tolerates its absence
			}
			_ = atomicWrite(join(sdir, "status"), strconv.Itoa(rc)+"\n")
		}()
		b, err := os.ReadFile(join(sdir, "cmd")) //nolint:gosec // reads the lane's own dispatch cmd file by path, by design
		if err != nil {
			return
		}
		if json.Unmarshal(b, &cmd) != nil || len(cmd.Argv) == 0 {
			return
		}
		loaded = true
		replyTmp = cmd.ReplyTmp
		qin, err := os.Open(cmd.Question)
		if err != nil {
			return
		}
		defer func() { _ = qin.Close() }()
		// Create+truncate the log BEFORE resolving/spawning codex (Python run_worker
		// line 590), so the printed LOG_FILE exists even on a 126/127 spawn failure.
		logf, err := os.OpenFile(cmd.Log, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644) //nolint:gosec // 0o644 matches the Python spec's log-file mode
		if err != nil {
			return
		}
		defer func() { _ = logf.Close() }()
		rc = runCodex(cmd.Argv, qin, logf)
		if rc == 0 && nonempty(cmd.ReplyTmp) {
			_ = os.Rename(cmd.ReplyTmp, cmd.Reply)
		}
	}()
	// Terminal status is durable; wake the dispatching owner subagent, if any —
	// bounded and fail-open, so a dead daemon never crashes the worker.
	if loaded && cmd.Owner != "" {
		wakeOwner(sdir, cmd)
	}
	os.Exit(0)
}

// runCodex resolves argv[0] to an executable PATH candidate and execs it, returning
// codex's exit code. The 126/127 split rides the real execve errno like Python's
// subprocess (ENOENT->127, else 126), never a pre-Stat guess.
func runCodex(argv []string, stdin, stdout *os.File) int {
	cand, sawEACCES := resolveCandidate(argv[0])
	if cand == "" {
		if sawEACCES {
			return 126 // a non-executable candidate was skipped: execvp returns EACCES
		}
		return 127 // nothing anywhere: ENOENT
	}
	c := exec.Command(cand, argv[1:]...) //nolint:gosec // cand is the codex verb resolved on PATH like execvp; argv is the built dispatch spec
	c.Stdin = stdin
	c.Stdout = stdout
	c.Stderr = stdout
	err := c.Run()
	if err == nil {
		return 0
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		if ws, ok := ee.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			return 128 + int(ws.Signal())
		}
		return ee.ExitCode()
	}
	return spawnErrno(err)
}

// resolveCandidate walks PATH like execvp: a non-executable regular file is skipped
// (sawEACCES) so a later executable one wins, while a dangling/looping symlink is
// left for exec to map. cand "" means nothing existed; sawEACCES then forces 126.
func resolveCandidate(name string) (cand string, sawEACCES bool) {
	if strings.Contains(name, "/") {
		if candExists(name) {
			return name, false
		}
		return "", false
	}
	for _, dir := range filepath.SplitList(os.Getenv("PATH")) {
		c := filepath.Join(dir, name)
		if !strings.Contains(c, "/") {
			c = "./" + c // an empty PATH entry means ".": keep the slash so exec skips LookPath
		}
		if fi, err := os.Stat(c); err == nil { //nolint:gosec // resolving codex on PATH like execvp
			if fi.Mode().IsRegular() && fi.Mode().Perm()&0o111 == 0 {
				sawEACCES = true // non-executable regular file: skip, remember the EACCES
				continue
			}
			return c, sawEACCES // executable (or non-regular): let exec map the rest
		} else if _, lerr := os.Lstat(c); lerr == nil { //nolint:gosec // resolving codex on PATH like execvp
			return c, sawEACCES // dangling/looping symlink: let exec map its errno
		}
	}
	return "", sawEACCES
}

// candExists follows symlinks (Stat) but falls back to Lstat so a dangling or
// looping symlink still counts — the exec attempt maps its errno, not a Stat guess.
func candExists(path string) bool {
	if _, err := os.Stat(path); err == nil {
		return true
	}
	_, err := os.Lstat(path)
	return err == nil
}

// spawnErrno maps a post-resolution exec failure to Python's subprocess split:
// ENOENT (not found, incl. a missing shebang interpreter) -> 127; EACCES / ELOOP /
// any other exec failure -> 126.
func spawnErrno(err error) int {
	var errno syscall.Errno
	if errors.As(err, &errno) && errno == syscall.ENOENT {
		return 127
	}
	return 126
}
