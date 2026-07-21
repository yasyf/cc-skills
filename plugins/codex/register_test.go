package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestThreadIDFromLog(t *testing.T) {
	banner := "stdin: reading question from lane\n"
	started := `{"type":"thread.started","thread_id":"019f-abc"}` + "\n"
	cases := []struct {
		id   string
		body string
		want string
	}{
		{"banner-then-started", banner + started + `{"type":"item.completed"}` + "\n", "019f-abc"},
		{"malformed-line-skipped", "not json at all\n" + started, "019f-abc"},
		{"no-thread-started", banner + `{"type":"turn.completed"}` + "\n", ""},
		{"wrong-type-same-shape", `{"type":"thread.other","thread_id":"019f-xyz"}` + "\n" + started, "019f-abc"},
		{"empty-thread-id", `{"type":"thread.started","thread_id":""}` + "\n" + started, "019f-abc"},
		{"beyond-scan-window", strings.Repeat("filler line\n", threadScanLines) + started, ""},
		{"empty-log", "", ""},
	}
	for _, c := range cases {
		t.Run(c.id, func(t *testing.T) {
			log := filepath.Join(t.TempDir(), "codex.log")
			if err := os.WriteFile(log, []byte(c.body), 0o644); err != nil { //nolint:gosec // test fixture log file
				t.Fatal(err)
			}
			if got := threadIDFromLog(log); got != c.want {
				t.Errorf("threadIDFromLog = %q, want %q", got, c.want)
			}
		})
	}
}

func TestThreadIDFromLogMissingFile(t *testing.T) {
	if got := threadIDFromLog(filepath.Join(t.TempDir(), "absent.log")); got != "" {
		t.Errorf("missing log: got %q, want \"\"", got)
	}
	if got := threadIDFromLog(""); got != "" {
		t.Errorf("empty path: got %q, want \"\"", got)
	}
}

func TestSessionFromMeta(t *testing.T) {
	cases := []struct {
		id   string
		info string
		want string
	}{
		{"present", `{"ts":1.5,"cwd":"/x","model":"gpt-5.6-sol","session":"s-test"}`, "s-test"},
		{"absent", `{"ts":1.5,"cwd":"/x","model":"gpt-5.6-sol"}`, ""},
		{"malformed", `{not json`, ""},
	}
	for _, c := range cases {
		t.Run(c.id, func(t *testing.T) {
			sdir := t.TempDir()
			writeMeta(t, sdir, c.info)
			if got := sessionFromMeta(sdir); got != c.want {
				t.Errorf("sessionFromMeta = %q, want %q", got, c.want)
			}
		})
	}
}

func TestSessionFromMetaNoMeta(t *testing.T) {
	if got := sessionFromMeta(t.TempDir()); got != "" {
		t.Errorf("no meta file: got %q, want \"\"", got)
	}
}

func TestFirstStderrLine(t *testing.T) {
	cases := []struct {
		id, stderr, want string
	}{
		{"click-banner-before-error", "Usage: capt-hook [OPTIONS] COMMAND [ARGS]...\nTry 'capt-hook --help'.\n\nError: No such command 'transcripts'.\n", "Error: No such command 'transcripts'."},
		{"uvx-progress-before-error", "Resolved 5 packages\nInstalled 5 packages\nerror: bad flag\n", "error: bad flag"},
		{"no-error-line-falls-back", "boom\nmore noise\n", "boom"},
		{"leading-blank-lines", "\n\nError: x\n", "Error: x"},
		{"empty", "", ""},
	}
	for _, c := range cases {
		t.Run(c.id, func(t *testing.T) {
			if got := firstStderrLine([]byte(c.stderr)); got != c.want {
				t.Errorf("firstStderrLine = %q, want %q", got, c.want)
			}
		})
	}
}

// TestRegisterTranscript exercises the whole registerTranscript codepath via the
// <sdir>/register outcome file, with a fake capt-hook shim on $PATH.
func TestRegisterTranscript(t *testing.T) {
	const tid = "019f-abc"
	started := `{"type":"thread.started","thread_id":"` + tid + `"}` + "\n"

	t.Run("ok", func(t *testing.T) {
		sdir := lane(t, `{"session":"s-test"}`, "banner\n"+started)
		argsOut := filepath.Join(t.TempDir(), "args")
		t.Setenv("REGISTER_ARGS_OUT", argsOut)
		shimPath(t, "capt-hook", "printf '%s\\n' \"$@\" > \"$REGISTER_ARGS_OUT\"\nexit 0\n")

		registerTranscript(sdir)
		if got := outcome(t, sdir); got != "ok "+tid {
			t.Fatalf("outcome = %q, want %q", got, "ok "+tid)
		}
		if got := strings.Split(strings.TrimSpace(readFile(argsOut)), "\n"); !equalArgs(got,
			[]string{"transcripts", "register", "--session", "s-test", "--provider", "codex", "--thread-id", tid, "--label", filepath.Base(sdir)}) {
			t.Errorf("shim args = %v", got)
		}
	})

	t.Run("skipped no session", func(t *testing.T) {
		sdir := lane(t, `{"model":"gpt-5.6-sol"}`, "banner\n"+started)
		shimPath(t, "capt-hook", "exit 0\n")
		registerTranscript(sdir)
		if got := outcome(t, sdir); got != "skipped: no session" {
			t.Errorf("outcome = %q, want %q", got, "skipped: no session")
		}
	})

	t.Run("skipped no thread id", func(t *testing.T) {
		sdir := lane(t, `{"session":"s-test"}`, "banner\n"+`{"type":"turn.completed"}`+"\n")
		shimPath(t, "capt-hook", "exit 0\n")
		registerTranscript(sdir)
		if got := outcome(t, sdir); got != "skipped: no thread id" {
			t.Errorf("outcome = %q, want %q", got, "skipped: no thread id")
		}
	})

	t.Run("skipped capt-hook not found", func(t *testing.T) {
		sdir := lane(t, `{"session":"s-test"}`, "banner\n"+started)
		t.Setenv("CAPT_HOOK_BIN", "")
		t.Setenv("PATH", t.TempDir()) // empty dir: neither capt-hook nor uvx resolves
		registerTranscript(sdir)
		if got := outcome(t, sdir); got != "skipped: capt-hook not found" {
			t.Errorf("outcome = %q, want %q", got, "skipped: capt-hook not found")
		}
	})

	t.Run("failed shim nonzero", func(t *testing.T) {
		sdir := lane(t, `{"session":"s-test"}`, "banner\n"+started)
		shimPath(t, "capt-hook", "echo boom >&2\nexit 3\n")
		registerTranscript(sdir)
		if got := outcome(t, sdir); got != "failed rc=3: boom" {
			t.Errorf("outcome = %q, want %q", got, "failed rc=3: boom")
		}
	})

	t.Run("uvx fallback", func(t *testing.T) {
		sdir := lane(t, `{"session":"s-test"}`, "banner\n"+started)
		argsOut := filepath.Join(t.TempDir(), "args")
		t.Setenv("REGISTER_ARGS_OUT", argsOut)
		t.Setenv("CAPT_HOOK_BIN", "")
		// Only uvx on PATH: registration runs as `uvx capt-hook ...`.
		shimPath(t, "uvx", "printf '%s\\n' \"$@\" > \"$REGISTER_ARGS_OUT\"\nexit 0\n")
		registerTranscript(sdir)
		if got := outcome(t, sdir); got != "ok "+tid {
			t.Fatalf("outcome = %q, want %q", got, "ok "+tid)
		}
		if got := strings.Split(strings.TrimSpace(readFile(argsOut)), "\n"); len(got) == 0 || got[0] != "capt-hook" {
			t.Errorf("uvx args should lead with capt-hook, got %v", got)
		}
	})
}

// TestRunWorkerRegistersAfterStatus drives a freshly built binary's real --worker
// entry with a fake codex + fake capt-hook, asserting runWorker writes status AND
// register on both a zero and a nonzero run — the only coverage of the lifecycle
// placement (register after status, unconditional on rc), which os.Exit forecloses in-process.
func TestRunWorkerRegistersAfterStatus(t *testing.T) {
	bin := buildCodexAsk(t)
	captDir := t.TempDir()
	// Shim asserts status exists (via STATUS_FILE) so a register-before-status reorder
	// yields rc=9, not byte-identical green — pinning the ordering, not just presence.
	writeExec(t, filepath.Join(captDir, "capt-hook"), "test -s \"$STATUS_FILE\" || { echo status-missing >&2; exit 9; }\nexit 0\n")
	codexDir := t.TempDir()
	writeExec(t, filepath.Join(codexDir, "fakecodex"),
		`printf '%s\n' '{"type":"thread.started","thread_id":"019f-worker"}'`+"\nexit ${FAKE_CODEX_RC:-0}\n")

	for _, c := range []struct {
		id, rc, wantStatus string
	}{
		{"rc-zero-registers", "0", "0"},
		{"rc-nonzero-still-registers", "3", "3"},
	} {
		t.Run(c.id, func(t *testing.T) {
			sdir := t.TempDir()
			question := join(sdir, "question")
			if err := os.WriteFile(question, []byte("hi\n"), 0o644); err != nil { //nolint:gosec // test fixture question file
				t.Fatal(err)
			}
			reply := join(sdir, "reply")
			cb, err := json.Marshal(cmdSpec{Argv: []string{"fakecodex"}, Question: question, Reply: reply, ReplyTmp: reply + ".tmp", Log: join(sdir, "codex.log")})
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(join(sdir, "cmd"), cb, 0o644); err != nil { //nolint:gosec // test fixture cmd file
				t.Fatal(err)
			}
			writeMeta(t, sdir, `{"session":"s-test"}`)

			worker := exec.Command(bin, "--worker", sdir)
			worker.Env = append(os.Environ(), "PATH="+codexDir+string(os.PathListSeparator)+captDir, "CAPT_HOOK_BIN=", "FAKE_CODEX_RC="+c.rc, "STATUS_FILE="+join(sdir, "status"))
			if out, err := worker.CombinedOutput(); err != nil {
				t.Fatalf("--worker failed: %v\n%s", err, out)
			}
			if got := strings.TrimSpace(readFile(join(sdir, "status"))); got != c.wantStatus {
				t.Errorf("status = %q, want %q", got, c.wantStatus)
			}
			if got := outcome(t, sdir); got != "ok 019f-worker" {
				t.Errorf("register outcome = %q, want %q", got, "ok 019f-worker")
			}
		})
	}
}

// buildCodexAsk compiles this package to a temp path so its real --worker entry
// point (which ends in os.Exit) can be driven as a subprocess.
func buildCodexAsk(t *testing.T) string {
	t.Helper()
	bin := filepath.Join(t.TempDir(), "codex-ask")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, out)
	}
	return bin
}

// writeExec drops an executable /bin/sh shim carrying body at path.
func writeExec(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body), 0o755); err != nil { //nolint:gosec // test shim must be executable
		t.Fatal(err)
	}
}

// writeMeta lays down a meta file with the given info JSON on line 3, mirroring
// dispatch.go's reply\nlog\ninfo\n layout.
func writeMeta(t *testing.T, sdir, info string) {
	t.Helper()
	reply := join(sdir, "reply")
	log := join(sdir, "codex.log")
	if err := atomicWrite(join(sdir, "meta"), reply+"\n"+log+"\n"+info+"\n"); err != nil {
		t.Fatal(err)
	}
}

// lane builds a lane dir with a meta (carrying info) and a cmd file whose Log
// points at a fixture log holding logBody, then returns the lane path.
func lane(t *testing.T, info, logBody string) string {
	t.Helper()
	sdir := t.TempDir()
	log := join(sdir, "codex.log")
	if err := os.WriteFile(log, []byte(logBody), 0o644); err != nil { //nolint:gosec // test fixture log file
		t.Fatal(err)
	}
	writeMeta(t, sdir, info)
	cb, err := json.Marshal(cmdSpec{Log: log})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(join(sdir, "cmd"), cb, 0o644); err != nil { //nolint:gosec // test fixture cmd file
		t.Fatal(err)
	}
	return sdir
}

// shimPath drops an executable shell shim named `name` into a fresh dir, points
// $PATH at it alone, and clears $CAPT_HOOK_BIN so resolveCaptHook walks PATH.
func shimPath(t *testing.T, name, body string) {
	t.Helper()
	dir := t.TempDir()
	writeExec(t, filepath.Join(dir, name), body)
	t.Setenv("CAPT_HOOK_BIN", "")
	t.Setenv("PATH", dir)
}

func outcome(t *testing.T, sdir string) string {
	t.Helper()
	return strings.TrimSpace(readFile(join(sdir, "register")))
}

func equalArgs(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
