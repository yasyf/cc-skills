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

func TestAClonesGitMovesToTheLocalDiskOnceAndStillReadsWithoutItsLeftovers(t *testing.T) {
	checkout, root := t.TempDir(), t.TempDir()
	git(t, checkout, "init", "-q")
	git(t, checkout, "commit", "-q", "--allow-empty", "-m", "kept")
	head := git(t, checkout, "rev-parse", "HEAD")
	dotGit := filepath.Join(checkout, gitDir)
	leftovers := []string{"refs/heads/stale.lock", "objects/pack/tmp_pack_abc", "objects/pack/multi-pack-index", "cc-notes/folds-v6/slot-1", "logs/refs/heads/old"}
	for _, leftover := range leftovers {
		if err := os.MkdirAll(filepath.Dir(filepath.Join(dotGit, leftover)), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dotGit, leftover), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}

	if err := onLocalDisk(checkout, root); err != nil {
		t.Fatal(err)
	}

	moved, err := os.Readlink(dotGit)
	if err != nil {
		t.Fatalf(".git is not a link: %v", err)
	}
	if !strings.HasPrefix(moved, root) {
		t.Fatalf(".git links to %s, outside %s", moved, root)
	}
	if got := git(t, checkout, "cat-file", "-t", head); got != "commit" {
		t.Fatalf("the moved clone reads HEAD as %q", got)
	}
	if exists(dotGit + replacedSuffix) {
		t.Fatal("the replaced .git stayed on the slow disk")
	}
	for _, leftover := range leftovers {
		if exists(filepath.Join(moved, leftover)) {
			t.Fatalf("%s was copied", leftover)
		}
	}

	git(t, checkout, "commit", "-q", "--allow-empty", "-m", "after")
	if err := onLocalDisk(checkout, root); err != nil {
		t.Fatal(err)
	}
	if again, err := os.Readlink(dotGit); err != nil || again != moved {
		t.Fatalf("a second move relinked .git to %s: %v", again, err)
	}
	if got := git(t, checkout, "log", "-1", "--format=%s"); got != "after" {
		t.Fatalf("a commit in the moved clone reads as %q", got)
	}
}

func TestACopyOfAStoreThatGainedAPackTakesItOnTheSecondWalk(t *testing.T) {
	from, to := t.TempDir(), t.TempDir()
	if err := os.WriteFile(filepath.Join(from, "pack-a"), []byte("a"), 0o400); err != nil {
		t.Fatal(err)
	}
	if err := copyTree(from, to); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(from, "pack-b"), []byte("b"), 0o400); err != nil {
		t.Fatal(err)
	}
	if err := copyTree(from, to); err != nil {
		t.Fatal(err)
	}
	for _, pack := range []string{"pack-a", "pack-b"} {
		info, err := os.Stat(filepath.Join(to, pack))
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0o400 {
			t.Fatalf("%s copied as %v", pack, info.Mode())
		}
	}
}

func TestACloneAlreadyMovedOrALinkedWorktreeIsLeftAlone(t *testing.T) {
	checkout, root := t.TempDir(), t.TempDir()
	if err := os.WriteFile(filepath.Join(checkout, gitDir), []byte("gitdir: /elsewhere\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := onLocalDisk(checkout, root); err != nil {
		t.Fatal(err)
	}
	if entries, err := os.ReadDir(root); err != nil || len(entries) != 0 {
		t.Fatalf("root holds %v: %v", entries, err)
	}
}
