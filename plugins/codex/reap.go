package main

import (
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	reapAge      = time.Hour
	reapGrace    = 2 * time.Second
	lstartLayout = "Mon Jan _2 15:04:05 2006"
)

type member struct {
	pid     int
	started time.Time
}

type reapRecord struct {
	Dir   string `json:"dir"`
	State string `json:"state"`
	Sid   int    `json:"sid"`
	Pids  []int  `json:"pids"`
}

// liveSessions maps session id to live processes, excluding this one. A run's
// tree is its session: codex gives each MCP server its own process group.
func liveSessions() map[int][]member {
	c := exec.Command("/bin/ps", "-A", "-o", "pid=,lstart=")
	c.Env = envWith("TZ=UTC", "LC_ALL=C")
	out, err := c.Output()
	if err != nil {
		return nil
	}
	self := os.Getpid()
	sessions := map[int][]member{}
	for _, line := range strings.Split(string(out), "\n") {
		f := strings.Fields(line)
		if len(f) != 6 {
			continue
		}
		pid, err := strconv.Atoi(f[0])
		if err != nil || pid <= 0 || pid == self {
			continue
		}
		started, err := time.Parse(lstartLayout, strings.Join(f[1:], " "))
		if err != nil {
			continue
		}
		if sid, err := syscall.Getsid(pid); err == nil {
			sessions[sid] = append(sessions[sid], member{pid, started})
		}
	}
	return sessions
}

func terminate(targets map[int][]member) {
	for _, ms := range targets {
		for _, m := range ms {
			_ = syscall.Kill(m.pid, syscall.SIGTERM)
		}
	}
	deadline := time.Now().Add(reapGrace)
	for {
		left := false
		for sid, ms := range targets {
			targets[sid] = stillIn(sid, ms)
			left = left || len(targets[sid]) > 0
		}
		if !left {
			return
		}
		if time.Now().After(deadline) {
			for _, ms := range targets {
				for _, m := range ms {
					_ = syscall.Kill(m.pid, syscall.SIGKILL)
				}
			}
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func stillIn(sid int, ms []member) []member {
	var left []member
	for _, m := range ms {
		if s, err := syscall.Getsid(m.pid); err == nil && s == sid {
			left = append(left, m)
		}
	}
	return left
}

// runMembers keeps members started inside the run's lifetime: once a run's
// session empties, a later detached session can reuse its id.
func runMembers(d string, ms []member) []member {
	meta, err := os.Stat(join(d, "meta"))
	if err != nil {
		return nil
	}
	start := meta.ModTime().Add(-time.Second)
	var end time.Time
	for _, p := range []string{join(d, "status"), lineAt(metaLines(join(d, "meta")), 1)} {
		if fi, err := os.Stat(p); err == nil && fi.ModTime().After(end) {
			end = fi.ModTime()
		}
	}
	var kept []member
	for _, m := range ms {
		if !m.started.Before(start) && !m.started.After(end) {
			kept = append(kept, m)
		}
	}
	return kept
}

func reapRuns(olderThan time.Duration, dryRun, emit bool) {
	sessions := liveSessions()
	if len(sessions) == 0 {
		return
	}
	now := nowSec()
	targets := map[int][]member{}
	_ = filepath.WalkDir(runsBase(), func(path string, e fs.DirEntry, err error) error {
		if err != nil || e.IsDir() || e.Name() != "sid" {
			return nil
		}
		d := filepath.Dir(path)
		sid, ok := readPid(path)
		if !ok || now-runMtime(d) < olderThan.Seconds() {
			return nil
		}
		ms := runMembers(d, sessions[sid])
		if len(ms) == 0 {
			return nil
		}
		st := classify(d).state
		if st == "pending" || st == "running" {
			return nil
		}
		if emit {
			rec := reapRecord{Dir: d, State: st, Sid: sid}
			for _, m := range ms {
				rec.Pids = append(rec.Pids, m.pid)
			}
			b, _ := json.Marshal(rec)
			fmt.Println(string(b))
		}
		targets[sid] = ms
		return nil
	})
	if !dryRun && len(targets) > 0 {
		terminate(targets)
	}
}

func reapMode(args []string) {
	olderThan := reapAge
	dryRun := false
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--dry-run":
			dryRun = true
		case "--older-than":
			d, err := time.ParseDuration(argOrEmpty(args, i+1))
			if err != nil || d < 0 {
				die("codex-ask: --older-than takes a duration such as 1h or 30m", 2)
			}
			olderThan = d
			i++
		default:
			die("usage: codex-ask --reap [--older-than DURATION] [--dry-run]", 2)
		}
	}
	reapRuns(olderThan, dryRun, true)
	os.Exit(0)
}

func reapOwnSession(sid int) {
	if ms := liveSessions()[sid]; len(ms) > 0 {
		terminate(map[int][]member{sid: ms})
	}
}
