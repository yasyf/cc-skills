package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// threadScanLines caps the log scan: codex emits thread.started in its opening
// events, so the first handful of lines suffice (line 1 is a stdin banner).
const threadScanLines = 10

// registerTimeout bounds the capt-hook invocation; the worker is already orphaned
// to PID 1, so the wait is free of any caller — but never unbounded.
const registerTimeout = 30 * time.Second

// registerTranscript records this lane's codex rollout with captain-hook so a
// deep-transcript gate can see the lane's edits. Called from runWorker after the
// status write, regardless of the run's exit code — a failed lane's edits still
// hit the tree. Never errors; its outcome is written to <sdir>/register.
func registerTranscript(sdir string) {
	_ = atomicWrite(join(sdir, "register"), registerOutcome(sdir)+"\n")
}

func registerOutcome(sdir string) string {
	sid := sessionFromMeta(sdir)
	if sid == "" {
		return "skipped: no session"
	}
	tid := threadIDFromLog(logPathFromCmd(sdir))
	if tid == "" {
		return "skipped: no thread id"
	}
	bin, prefix := resolveCaptHook()
	if bin == "" {
		return "skipped: capt-hook not found"
	}
	return invokeRegister(bin, prefix, sid, tid, filepath.Base(sdir))
}

// sessionFromMeta pulls the Claude Code session id off meta's info line (line 3,
// written dispatch.go from $CLAUDE_CODE_SESSION_ID). Absent outside Claude Code.
func sessionFromMeta(sdir string) string {
	info := lineAt(metaLines(join(sdir, "meta")), 2)
	if info == "" {
		return ""
	}
	var m map[string]any
	if json.Unmarshal([]byte(info), &m) != nil {
		return ""
	}
	if s, ok := m["session"].(string); ok {
		return s
	}
	return ""
}

func logPathFromCmd(sdir string) string {
	b, err := os.ReadFile(join(sdir, "cmd")) //nolint:gosec // reads the lane's own dispatch cmd file by path, by design
	if err != nil {
		return ""
	}
	var cmd cmdSpec
	if json.Unmarshal(b, &cmd) != nil {
		return ""
	}
	return cmd.Log
}

// threadIDFromLog scans the log's opening lines for codex's thread.started event
// and returns its thread_id. Line 1 is a stdin banner and any malformed line is
// skipped — the scan reads structure, never a fixed line number.
func threadIDFromLog(logPath string) string {
	if logPath == "" {
		return ""
	}
	f, err := os.Open(logPath) //nolint:gosec // streams the lane's own codex log by path, by design
	if err != nil {
		return ""
	}
	defer func() { _ = f.Close() }()
	rd := bufio.NewReader(f)
	for i := 0; i < threadScanLines; i++ {
		line, rerr := rd.ReadString('\n')
		var ev struct {
			Type     string `json:"type"`
			ThreadID string `json:"thread_id"`
		}
		if json.Unmarshal([]byte(line), &ev) == nil && ev.Type == "thread.started" && ev.ThreadID != "" {
			return ev.ThreadID
		}
		if rerr != nil {
			return ""
		}
	}
	return ""
}

// resolveCaptHook picks the capt-hook invocation: an explicit $CAPT_HOOK_BIN, then
// a PATH capt-hook, then uvx as `uvx capt-hook`. Empty bin means none is reachable.
func resolveCaptHook() (bin string, prefix []string) {
	if override := os.Getenv("CAPT_HOOK_BIN"); override != "" {
		return override, nil
	}
	if p, err := exec.LookPath("capt-hook"); err == nil {
		return p, nil
	}
	if p, err := exec.LookPath("uvx"); err == nil {
		return p, []string{"capt-hook"}
	}
	return "", nil
}

// invokeRegister runs the capt-hook registration under a bounded context. An old
// capt-hook without the command is a recorded failure, not a compat branch.
func invokeRegister(bin string, prefix []string, sid, tid, label string) string {
	ctx, cancel := context.WithTimeout(context.Background(), registerTimeout)
	defer cancel()
	args := append(
		append([]string{}, prefix...),
		"transcripts", "register",
		"--session", sid,
		"--provider", "codex",
		"--thread-id", tid,
		"--label", label,
	)
	c := exec.CommandContext(ctx, bin, args...) //nolint:gosec // bin is the resolved capt-hook launcher; args are the fixed registration contract
	var stderr bytes.Buffer
	c.Stderr = &stderr
	if err := c.Run(); err != nil {
		msg := firstStderrLine(stderr.Bytes())
		if msg == "" {
			msg = err.Error()
		}
		return fmt.Sprintf("failed rc=%d: %s", registerExitCode(err), msg)
	}
	return "ok " + tid
}

func registerExitCode(err error) int {
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode()
	}
	return -1
}

func firstStderrLine(b []byte) string {
	for _, line := range strings.Split(string(b), "\n") {
		if t := strings.TrimSpace(line); t != "" {
			return t
		}
	}
	return ""
}
