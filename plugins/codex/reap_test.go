package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func waitGone(t *testing.T, pid int) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for kill0(pid) {
		if time.Now().After(deadline) {
			t.Fatalf("pid %d still alive", pid)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func TestWorkerReapsCodexOrphans(t *testing.T) {
	runs := mustTempDir(t)
	pidFile := filepath.Join(mustTempDir(t), "orphan")
	stub := stubCodex("[]", "perl -e 'setpgrp(0, 0); exec \"sleep\", \"300\"' >/dev/null 2>&1 &\n"+
		"echo $! > "+pidFile+"\n"+stubCodexReplyBody)

	_, stderr, code := askRun(t, runs, stub, "ping")
	if code != 0 {
		t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
	}
	pid, ok := readPid(pidFile)
	if !ok {
		t.Fatal("stub codex recorded no orphan pid")
	}
	t.Cleanup(func() { _ = syscall.Kill(pid, syscall.SIGKILL) })
	waitGone(t, pid)
}

func detachedOrphan(t *testing.T) (sid, pid int) {
	t.Helper()
	c := exec.Command("/bin/sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!")
	c.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	out, err := c.Output()
	if err != nil {
		t.Fatal(err)
	}
	pid, err = strconv.Atoi(strings.TrimSpace(string(out)))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = syscall.Kill(pid, syscall.SIGKILL) })
	return c.Process.Pid, pid
}

func orphanedRun(t *testing.T, runs string, sid int, ended time.Time, status bool) string {
	t.Helper()
	sdir, err := os.MkdirTemp(runs, "codex-ask.")
	if err != nil {
		t.Fatal(err)
	}
	if sdir, err = filepath.EvalSymlinks(sdir); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(sdir, "codex-q-x.log")
	writeFile(t, filepath.Join(sdir, "meta"), filepath.Join(sdir, "codex-r-x")+"\n"+log+"\n")
	writeFile(t, filepath.Join(sdir, "pid"), strconv.Itoa(sid)+"\n")
	writeFile(t, filepath.Join(sdir, "sid"), strconv.Itoa(sid)+"\n")
	writeFile(t, log, "")
	started := time.Now().Add(-2 * time.Hour)
	for _, f := range []string{"meta", "pid"} {
		if err := os.Chtimes(filepath.Join(sdir, f), started, started); err != nil {
			t.Fatal(err)
		}
	}
	if status {
		writeFile(t, filepath.Join(sdir, "status"), "1\n")
		if err := os.Chtimes(filepath.Join(sdir, "status"), ended, ended); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Chtimes(log, ended, ended); err != nil {
		t.Fatal(err)
	}
	return sdir
}

func TestReapKillsDiedRunTree(t *testing.T) {
	runs := mustTempDir(t)
	sid, pid := detachedOrphan(t)
	sdir := orphanedRun(t, runs, sid, time.Now(), false)

	if out, _, _ := askRun(t, runs, stubCodexReply, "--reap", "--dry-run", "--older-than", "3h"); out != "" {
		t.Fatalf("--older-than 3h targeted a 2h-old run:\n%s", out)
	}
	out, stderr, code := askRun(t, runs, stubCodexReply, "--reap", "--dry-run")
	if code != 0 {
		t.Fatalf("--reap --dry-run exit %d\nstderr: %s", code, stderr)
	}
	want := `{"dir":"` + sdir + `","state":"died","sid":` + strconv.Itoa(sid) + `,"pids":[` + strconv.Itoa(pid) + `]}`
	if strings.TrimSpace(out) != want {
		t.Fatalf("--reap --dry-run printed\n%s\nwant\n%s", out, want)
	}
	if !kill0(pid) {
		t.Fatal("--dry-run killed the orphan")
	}

	if _, stderr, code := askRun(t, runs, stubCodexReply, "--reap"); code != 0 {
		t.Fatalf("--reap exit %d\nstderr: %s", code, stderr)
	}
	waitGone(t, pid)
}

func TestReapSparesSessionStartedAfterRunEnded(t *testing.T) {
	runs := mustTempDir(t)
	sid, pid := detachedOrphan(t)
	orphanedRun(t, runs, sid, time.Now().Add(-90*time.Minute), true)

	out, stderr, code := askRun(t, runs, stubCodexReply, "--reap")
	if code != 0 {
		t.Fatalf("--reap exit %d\nstderr: %s", code, stderr)
	}
	if out != "" {
		t.Fatalf("--reap targeted a session that started after the run ended:\n%s", out)
	}
	if !kill0(pid) {
		t.Fatal("--reap killed a process that started after the run ended")
	}
}
