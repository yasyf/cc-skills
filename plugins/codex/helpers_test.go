package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestJSONEscape(t *testing.T) {
	// Escape order (backslash first) then strip raw C0 controls: the JSONL contract
	// the pytest --collect goldens depend on, pinned directly.
	cases := map[string]string{
		`say "hi" a\b` + "\tc\x01\x02 end": `say \"hi\" a\\b\tc end`,
		"lane\nbreak":                      `lane\nbreak`,
		"plain":                            "plain",
		"":                                 "",
	}
	for in, want := range cases {
		if got := jsonEscape(in); got != want {
			t.Errorf("jsonEscape(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestShlexQuote(t *testing.T) {
	cases := map[string]string{
		"":                     "''",
		"/tmp/codex-ask.1/l":   "/tmp/codex-ask.1/l",
		"has space":            "'has space'",
		"it's":                 `'it'"'"'s'`,
		"safe@%+=:,./-_chars9": "safe@%+=:,./-_chars9",
	}
	for in, want := range cases {
		if got := shlexQuote(in); got != want {
			t.Errorf("shlexQuote(%q) = %q, want %q", in, got, want)
		}
	}
}

// The 126/127 split rides the real execve errno, not a pre-Stat guess: ENOENT
// (missing, or a missing shebang interpreter) is 127; EACCES / ELOOP is 126.
func TestRunCodexErrnoSplit(t *testing.T) {
	devnull, err := os.Open(os.DevNull)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = devnull.Close() }()
	sink, err := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = sink.Close() }()

	run := func(dir string) int {
		t.Setenv("PATH", dir)
		return runCodex([]string{"codex"}, devnull, sink)
	}

	t.Run("missing->127", func(t *testing.T) {
		if rc := run(t.TempDir()); rc != 127 {
			t.Errorf("missing binary: got %d, want 127", rc)
		}
	})
	t.Run("nonexec->126", func(t *testing.T) {
		dir := t.TempDir()
		if err := os.WriteFile(dir+"/codex", []byte("not a program\n"), 0o644); err != nil { //nolint:gosec // test fixture: a non-executable fake binary
			t.Fatal(err)
		}
		if rc := run(dir); rc != 126 {
			t.Errorf("non-executable: got %d, want 126", rc)
		}
	})
	t.Run("bad-interpreter->127", func(t *testing.T) {
		dir := t.TempDir()
		// executable, but its shebang interpreter does not exist -> execve ENOENT
		if err := os.WriteFile(dir+"/codex", []byte("#!"+dir+"/no-such-interp\n"), 0o755); err != nil { //nolint:gosec // test fixture must be owner-executable
			t.Fatal(err)
		}
		if rc := run(dir); rc != 127 {
			t.Errorf("bad interpreter: got %d, want 127", rc)
		}
	})
	t.Run("symlink-loop->126", func(t *testing.T) {
		dir := t.TempDir()
		loop := dir + "/codex"
		if err := os.Symlink(loop, loop); err != nil { // codex -> codex -> ELOOP on exec
			t.Fatal(err)
		}
		if rc := run(dir); rc != 126 {
			t.Errorf("symlink loop: got %d, want 126", rc)
		}
	})
	t.Run("nonexec-shadows-exec->skips-to-later", func(t *testing.T) {
		// execvp skips a non-executable codex earlier in PATH and runs a later executable one.
		d1, d2 := t.TempDir(), t.TempDir()
		if err := os.WriteFile(d1+"/codex", []byte("not a program\n"), 0o644); err != nil { //nolint:gosec // test fixture: a non-executable fake binary
			t.Fatal(err)
		}
		if err := os.WriteFile(d2+"/codex", []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil { //nolint:gosec // test fixture must be owner-executable
			t.Fatal(err)
		}
		t.Setenv("PATH", d1+string(os.PathListSeparator)+d2)
		if rc := runCodex([]string{"codex"}, devnull, sink); rc != 0 {
			t.Errorf("non-exec shadowing executable: got %d, want 0", rc)
		}
	})
}

func TestInsideGitRepo(t *testing.T) {
	root := t.TempDir()
	sub := filepath.Join(root, "a", "b")
	if err := os.MkdirAll(sub, 0o750); err != nil {
		t.Fatal(err)
	}

	t.Run("no-repo", func(t *testing.T) {
		t.Chdir(sub)
		if insideGitRepo() {
			t.Error("bare temp dir reported as inside a git repo")
		}
	})
	t.Run("dotgit-dir-in-ancestor", func(t *testing.T) {
		if err := os.Mkdir(filepath.Join(root, ".git"), 0o750); err != nil {
			t.Fatal(err)
		}
		t.Chdir(sub)
		if !insideGitRepo() {
			t.Error(".git dir two levels up not detected")
		}
	})
	t.Run("dotgit-file-worktree", func(t *testing.T) {
		wt := t.TempDir()
		if err := os.WriteFile(filepath.Join(wt, ".git"), []byte("gitdir: elsewhere\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		t.Chdir(wt)
		if !insideGitRepo() {
			t.Error(".git worktree file not detected")
		}
	})
}
