package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func awaitMode(target string) {
	if !strings.HasPrefix(target, "/") {
		die("codex-ask: --await needs an absolute scratch dir or reply-file path", 2)
	}
	sdir := target
	if fi, err := os.Stat(target); err != nil || !fi.IsDir() { //nolint:gosec // stats the caller's own --await scratch path
		sdir = filepath.Dir(target)
	}
	laneLock := acquireLaneLock(sdir, false)
	defer releaseLaneLock(laneLock)
	if !isFile(join(sdir, "meta")) {
		die(fmt.Sprintf("codex-ask: no recorded codex-ask run at %s", sdir), 2)
	}
	lines := metaLines(join(sdir, "meta"))
	r, log := lineAt(lines, 0), lineAt(lines, 1)
	pollStatus(sdir, r, log)
	verifyGeneration(sdir, r, log)
	fmt.Printf("REPLY_FILE: %s\n", r)
	fmt.Printf("LOG_FILE: %s\n", log)
	reportStatus(readStatus(sdir), r, log)
}

// pollStatus: block until <sdir>/status exists and is non-empty. While the pid is
// absent wait generously (~15s) for the worker to register it; only a recorded-
// then-dead pid with no status is a genuine mid-flight death (recovered if the
// staged reply is complete). The generation check protects async awaiters and
// remains a fail-closed invariant even though foreground dispatch retains its
// exclusive generation lock through reporting.
func pollStatus(sdir, reply, log string) {
	status := join(sdir, "status")
	pidFile := join(sdir, "pid")
	meta := join(sdir, "meta")
	waitPid := 0
	grace := 0
	for {
		verifyGeneration(sdir, reply, log)
		if nonempty(status) {
			return
		}
		switch {
		case !exists(pidFile):
			waitPid++
			if waitPid >= 60 {
				die(fmt.Sprintf("codex-ask: run at %s never registered a pid", sdir), 1)
			}
		case pidAlive(sdir):
			grace = 0
		default:
			grace++
			if grace >= 4 {
				r := lineAt(metaLines(meta), 0)
				if nonempty(r) {
					_ = atomicWrite(status, "0\n") // tolerate a concurrent publisher
					continue
				}
				die(fmt.Sprintf("codex-ask: run at %s exited without recording status", sdir), 1)
			}
		}
		time.Sleep(250 * time.Millisecond)
	}
}

func verifyGeneration(sdir, reply, log string) {
	current := metaLines(join(sdir, "meta"))
	if lineAt(current, 0) != reply || lineAt(current, 1) != log {
		die(fmt.Sprintf("codex-ask: lane %s was reused while waiting (generation changed); use a unique -s directory", sdir), 1)
	}
}

// reportStatus: tail the log on failure, exit codex's code. A 0 exit with an empty
// reply is a silent codex death; a turn that started but never completed died
// mid-turn; an empty/non-numeric status vanished mid-read.
func reportStatus(st, rfile, lfile string) {
	st = strings.TrimSpace(st)
	if !allDigits(st) {
		die("codex-ask: status went away before it could be read (concurrent -s reuse?)", 1)
	}
	code, _ := strconv.Atoi(st)
	if code == 0 && !nonempty(rfile) {
		fmt.Fprintln(os.Stderr, "codex-ask: codex exited 0 but wrote no reply")
		tail(lfile, 20)
		os.Exit(1)
	}
	if code == 0 && turnStarted(lfile) && !hasCompletedMarker(lfile) {
		if grepFile(lfile, turnFailedMarker) {
			fmt.Fprintln(os.Stderr, "codex-ask: codex reported turn.failed (see log tail)")
		} else {
			fmt.Fprintln(os.Stderr, "codex-ask: codex died mid-turn (no turn.completed) — the "+
				"working tree may hold partial, unverified edits; inspect with git/jj status before re-running")
		}
		tail(lfile, 20)
		os.Exit(1)
	}
	if code != 0 {
		tail(lfile, 20)
	}
	os.Exit(code)
}

func readStatus(sdir string) string { return readFile(join(sdir, "status")) }

// pidAlive: kill-0 AND the recorded start-time still matches, so a recycled pid
// reads dead. A blank/missing recorded lstart degrades to bare kill-0.
func pidAlive(sdir string) bool {
	pid, ok := readPid(join(sdir, "pid"))
	if !ok || !kill0(pid) {
		return false
	}
	recorded := ""
	if b, err := os.ReadFile(join(sdir, "lstart")); err == nil { //nolint:gosec // reads the lane's own lstart file, by design
		recorded = strings.TrimSpace(string(b))
	}
	if recorded == "" {
		return true
	}
	return procLstart(pid) == recorded
}

// procLstart: the process start-time via a fixed binary and pinned env, so a TZ or
// locale change between the launch record and a later probe can't reformat the
// timestamp and make a live lane read dead.
func procLstart(pid int) string {
	c := exec.Command("/bin/ps", "-o", "lstart=", "-p", strconv.Itoa(pid)) //nolint:gosec // fixed /bin/ps liveness probe; only the pid varies
	c.Env = envWith("TZ=UTC", "LC_ALL=C")
	out, err := c.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func readPid(pidFile string) (int, bool) {
	s := strings.TrimSpace(readFile(pidFile))
	if !allDigits(s) {
		return 0, false
	}
	n, err := strconv.Atoi(s)
	return n, err == nil
}

func kill0(pid int) bool {
	err := syscall.Kill(pid, 0)
	if err == nil || err == syscall.EPERM {
		return true
	}
	return false
}

// firstLine streams one line (Python readline().rstrip("\n")); a read error
// degrades to "". Never slurps — the file may be a multi-GB codex log.
func firstLine(path string) string {
	f, err := os.Open(path) //nolint:gosec // streams the tool's own dispatch/log file by path, by design
	if err != nil {
		return ""
	}
	defer func() { _ = f.Close() }()
	line, err := bufio.NewReader(f).ReadString('\n')
	if line == "" && err != nil {
		return ""
	}
	return strings.TrimRight(line, "\n")
}

func metaLines(path string) []string {
	b, err := os.ReadFile(path) //nolint:gosec // reads the lane's own meta file by path, by design
	if err != nil {
		return nil
	}
	return splitLines(string(b))
}

// splitLines mirrors Python str.splitlines(): split on \n, \r, and \r\n with no
// trailing empty element for a terminating break. Plain Split("\n") leaves a
// stray "\r" on CRLF meta that corrupts the reply/log paths.
func splitLines(s string) []string {
	var lines []string
	start, i := 0, 0
	for i < len(s) {
		if c := s[i]; c == '\n' || c == '\r' {
			lines = append(lines, s[start:i])
			if c == '\r' && i+1 < len(s) && s[i+1] == '\n' {
				i++
			}
			i++
			start = i
		} else {
			i++
		}
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

func lineAt(lines []string, i int) string {
	if i >= 0 && i < len(lines) {
		return lines[i]
	}
	return ""
}

// grepFile streams line-by-line (Python's per-line `for line in f`); a read error
// degrades to false. Never slurps — the log may be multi-GB.
func grepFile(path, needle string) bool {
	f, err := os.Open(path) //nolint:gosec // streams the tool's own codex log by path, by design
	if err != nil {
		return false
	}
	defer func() { _ = f.Close() }()
	rd := bufio.NewReader(f)
	for {
		line, err := rd.ReadString('\n')
		if strings.Contains(line, needle) {
			return true
		}
		if err != nil {
			return false
		}
	}
}

func turnStarted(log string) bool {
	return log != "" && isFile(log) && grepFile(log, turnStartedMarker)
}

func hasCompletedMarker(log string) bool {
	return log != "" && isFile(log) && grepFile(log, turnCompletedMarker)
}

func nonempty(path string) bool {
	if path == "" {
		return false
	}
	fi, err := os.Stat(path) //nolint:gosec // stats a caller-supplied dispatch path
	return err == nil && fi.Size() > 0
}

// tail prints the last n lines via a bounded ring over a streaming scan (Python's
// deque(f, maxlen=n)); a read error is a no-op. Never slurps the whole log.
func tail(path string, n int) {
	if n <= 0 {
		return
	}
	f, err := os.Open(path) //nolint:gosec // streams the tool's own codex log by path, by design
	if err != nil {
		return
	}
	defer func() { _ = f.Close() }()
	ring := make([]string, n)
	count := 0
	rd := bufio.NewReader(f)
	for {
		line, rerr := rd.ReadString('\n')
		if line != "" {
			ring[count%n] = strings.TrimRight(line, "\n")
			count++
		}
		if rerr != nil {
			break
		}
	}
	start, total := 0, count
	if total > n {
		start, total = count%n, n
	}
	for i := 0; i < total; i++ {
		fmt.Println(ring[(start+i)%n])
	}
}
