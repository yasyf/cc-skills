package main

import (
	"encoding/json"
	"os"
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

	t.Run("failed codex run still registers", func(t *testing.T) {
		sdir := lane(t, `{"session":"s-test"}`, "banner\n"+started)
		// A nonzero status must not gate registration.
		if err := atomicWrite(join(sdir, "status"), "1\n"); err != nil {
			t.Fatal(err)
		}
		shimPath(t, "capt-hook", "exit 0\n")
		registerTranscript(sdir)
		if got := outcome(t, sdir); got != "ok "+tid {
			t.Fatalf("outcome = %q, want %q", got, "ok "+tid)
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
	if err := os.WriteFile(filepath.Join(dir, name), []byte("#!/bin/sh\n"+body), 0o755); err != nil { //nolint:gosec // test shim must be executable
		t.Fatal(err)
	}
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
