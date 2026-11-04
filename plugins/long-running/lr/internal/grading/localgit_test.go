package grading

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func git(t *testing.T, checkout string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", checkout, "-c", "user.name=lr", "-c", "user.email=lr@example.com"}, args...)...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func TestAShallowCloneTakesItsWholeHistorySoARangeFromTheTrunkStopsAtTheirForkOnce(t *testing.T) {
	origin, checkout := t.TempDir(), t.TempDir()
	git(t, origin, "init", "-q", "-b", "dev")
	git(t, origin, "commit", "-q", "--allow-empty", "-m", "fork")
	fork := git(t, origin, "rev-parse", "HEAD")
	git(t, origin, "commit", "-q", "--allow-empty", "-m", "trunk")
	trunk := git(t, origin, "rev-parse", "HEAD")
	git(t, origin, "checkout", "-q", "-b", "pr", fork)
	git(t, origin, "commit", "-q", "--allow-empty", "-m", "own")
	head := git(t, origin, "rev-parse", "HEAD")
	git(t, origin, "checkout", "-q", "dev")
	git(t, checkout, "clone", "-q", "--depth", "1", "--branch", "dev", "file://"+origin, ".")
	git(t, checkout, "fetch", "-q", "origin", "pr")
	if got := git(t, checkout, "rev-list", "--count", trunk+".."+head); got != "2" {
		t.Fatalf("the shallow clone reads %s commits past the trunk, want the fork counted too", got)
	}

	for range 2 {
		if err := readyClone(checkout); err != nil {
			t.Fatal(err)
		}
	}

	if got := git(t, checkout, "rev-parse", "--is-shallow-repository"); got != "false" {
		t.Fatalf("is shallow: %s", got)
	}
	if got := git(t, checkout, "rev-list", trunk+".."+head); got != head {
		t.Fatalf("past the trunk: %s, want only %s", got, head)
	}
}

func TestAClonesAttributesComeFromItsHeadNotItsWorktree(t *testing.T) {
	checkout := t.TempDir()
	git(t, checkout, "init", "-q")
	attributes := filepath.Join(checkout, ".gitattributes")
	if err := os.WriteFile(attributes, []byte("committed.txt -diff\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	git(t, checkout, "add", ".gitattributes")
	git(t, checkout, "commit", "-q", "-m", "attributes")
	if err := os.WriteFile(attributes, []byte("worktree.txt -diff\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	wantAttributes := func(moment string) {
		t.Helper()
		if got := git(t, checkout, "check-attr", "diff", "--", "committed.txt"); !strings.HasSuffix(got, "diff: unset") {
			t.Fatalf("%s: the head's attribute reads %q", moment, got)
		}
		if got := git(t, checkout, "check-attr", "diff", "--", "worktree.txt"); !strings.HasSuffix(got, "diff: unspecified") {
			t.Fatalf("%s: the worktree's attribute reads %q", moment, got)
		}
	}

	if err := readyClone(checkout); err != nil {
		t.Fatal(err)
	}
	wantAttributes("after the first pass")

	git(t, checkout, "config", "--unset", attrTree)
	if err := readyClone(checkout); err != nil {
		t.Fatal(err)
	}
	wantAttributes("after a later pass")
}

func TestALinkedWorktreeIsLeftAlone(t *testing.T) {
	checkout := t.TempDir()
	if err := os.WriteFile(filepath.Join(checkout, gitDir), []byte("gitdir: /elsewhere\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := readyClone(checkout); err != nil {
		t.Fatal(err)
	}
}
