package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
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
