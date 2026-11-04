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

func TestAClonesAttributesComeFromItsHeadNotItsWorktree(t *testing.T) {
	checkout, root := t.TempDir(), t.TempDir()
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

	if err := onLocalDisk(checkout, root); err != nil {
		t.Fatal(err)
	}
	wantAttributes("after the move")

	git(t, checkout, "config", "--unset", attrTree)
	if err := onLocalDisk(checkout, root); err != nil {
		t.Fatal(err)
	}
	wantAttributes("on a clone an earlier pass moved")
}

func TestAMoveOffHomeLeavesEveryPathWorkingBehindALinkOnTheLocalDisk(t *testing.T) {
	base := t.TempDir()
	checkout, root, home := filepath.Join(base, "checkout"), filepath.Join(base, "root"), filepath.Join(base, "home")
	if err := os.MkdirAll(checkout, 0o750); err != nil {
		t.Fatal(err)
	}
	git(t, checkout, "init", "-q")
	if err := os.WriteFile(filepath.Join(checkout, "tracked.txt"), []byte("committed\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	git(t, checkout, "add", "tracked.txt")
	git(t, checkout, "commit", "-q", "-m", "tree")
	if err := onLocalDisk(checkout, root); err != nil {
		t.Fatal(err)
	}
	bin, codex := filepath.Join(home, ".local", "bin"), filepath.Join(home, ".codex")
	for path, body := range map[string]string{
		filepath.Join(bin, "ccx"):                         "ccx",
		filepath.Join(codex, "config.toml"):               "config",
		filepath.Join(codex, "sessions", "2026", "a.log"): "session",
		filepath.Join(codex, "state_1.sqlite"):            "live",
	} {
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o700); err != nil { //nolint:gosec
			t.Fatal(err)
		}
	}
	if err := os.Symlink("../lib/tool.js", filepath.Join(bin, "tool")); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(home, ".local", "lib"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, ".local", "lib", "tool.js"), []byte("tool"), 0o600); err != nil {
		t.Fatal(err)
	}
	if movedOffHome(checkout, home) {
		t.Fatal("an unmoved Sprite reads as moved")
	}

	if err := moveOffHome(checkout, home, root); err != nil {
		t.Fatal(err)
	}

	if !movedOffHome(checkout, home) {
		t.Fatal("a moved Sprite still reads as unmoved, so every pass would start another move")
	}
	for _, path := range []string{checkout, bin, codex} {
		moved, err := os.Readlink(path)
		if err != nil || !strings.HasPrefix(moved, root) {
			t.Fatalf("%s links to %q: %v", path, moved, err)
		}
		if _, err := os.Stat(path + homeCopySuffix); err != nil {
			t.Fatalf("the home copy of %s is gone: %v", path, err)
		}
	}
	if got := git(t, checkout, "status", "--porcelain"); got != "" {
		t.Fatalf("the moved worktree reads as dirty: %s", got)
	}
	for path, want := range map[string]string{
		filepath.Join(checkout, "tracked.txt"):            "committed\n",
		filepath.Join(bin, "ccx"):                         "ccx",
		filepath.Join(bin, "tool"):                        "tool",
		filepath.Join(codex, "config.toml"):               "config",
		filepath.Join(codex, "sessions", "2026", "a.log"): "session",
	} {
		if got, err := os.ReadFile(path); err != nil || string(got) != want {
			t.Fatalf("%s reads %q: %v", path, got, err)
		}
	}
	session, err := os.Readlink(filepath.Join(codex, "sessions"))
	if err != nil || strings.HasPrefix(session, root) {
		t.Fatalf("codex sessions moved to the local disk: %q, %v", session, err)
	}
	if _, err := os.Lstat(filepath.Join(codex, "state_1.sqlite")); err == nil {
		t.Fatal("a live codex database was copied")
	}

	if err := moveOffHome(checkout, home, root); err != nil {
		t.Fatalf("a second move: %v", err)
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
