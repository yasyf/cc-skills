package grading

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
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

func TestAMoveOffHomeLeavesHomeWholeAndKeepsItsToolCopiesCurrent(t *testing.T) {
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
	bin, codex := filepath.Join(home, localBinDir), filepath.Join(home, codexDir)
	write := func(path, body string) {
		t.Helper()
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(filepath.Join(bin, "ccx"), "ccx 1")
	write(filepath.Join(home, ".local", "lib", "tool.js"), "tool")
	write(filepath.Join(home, ".cache", "cc-context", "ledger.json"), "{}")
	write(filepath.Join(codex, "config.toml"), "config")
	write(filepath.Join(codex, "sessions", "2026", "a.log"), "session")
	write(filepath.Join(codex, "state_1.sqlite"), "live")
	write(filepath.Join(home, codexReleases, "releases", "1", "bin", "codex"), "codex")
	if err := os.Symlink("../lib/tool.js", filepath.Join(bin, "tool")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("releases/1", filepath.Join(home, codexReleases, "current")); err != nil {
		t.Fatal(err)
	}
	if movedOffHome(checkout, home, root) {
		t.Fatal("an unmoved Sprite reads as moved")
	}

	if err := moveOffHome(checkout, home, root); err != nil {
		t.Fatal(err)
	}

	if !movedOffHome(checkout, home, root) {
		t.Fatal("a moved Sprite still reads as unmoved, so every pass would start another move")
	}
	if moved, err := os.Readlink(checkout); err != nil || !strings.HasPrefix(moved, root) {
		t.Fatalf("the checkout links to %q: %v", moved, err)
	}
	if got := git(t, checkout, "status", "--porcelain"); got != "" {
		t.Fatalf("the moved worktree reads as dirty: %s", got)
	}
	for _, path := range []string{bin, codex} {
		if info, err := os.Lstat(path); err != nil || !info.IsDir() {
			t.Fatalf("%s on home is no longer a directory: %v, %v", path, info, err)
		}
	}
	if info, err := os.Lstat(filepath.Join(home, ".cache", "cc-context")); err != nil || info.Mode()&fs.ModeSymlink == 0 {
		t.Fatalf("ccx's state stayed on home: %v, %v", info, err)
	}
	copies := filepath.Join(root, homeDirsRoot)
	for path, want := range map[string]string{
		filepath.Join(checkout, "tracked.txt"):                     "committed\n",
		filepath.Join(copies, localBinDir, "ccx"):                  "ccx 1",
		filepath.Join(copies, localBinDir, "tool"):                 "tool",
		filepath.Join(copies, codexDir, "config.toml"):             "config",
		filepath.Join(copies, codexDir, "sessions/2026/a.log"):     "session",
		filepath.Join(copies, codexBinDir, "codex"):                "codex",
		filepath.Join(home, ".cache", "cc-context", "ledger.json"): "{}",
	} {
		if got, err := os.ReadFile(path); err != nil || string(got) != want {
			t.Fatalf("%s reads %q: %v", path, got, err)
		}
	}
	if resolved, err := filepath.EvalSymlinks(filepath.Join(copies, codexBinDir)); err != nil || !strings.HasPrefix(resolved, root) {
		t.Fatalf("the codex copy resolves to %q, off the local disk: %v", resolved, err)
	}
	if session, err := os.Readlink(filepath.Join(copies, codexDir, sessionsDir)); err != nil || strings.HasPrefix(session, root) {
		t.Fatalf("codex sessions moved to the local disk: %q, %v", session, err)
	}
	if _, err := os.Lstat(filepath.Join(copies, codexDir, "state_1.sqlite")); err == nil {
		t.Fatal("a live codex database was copied")
	}

	later := time.Now().Add(time.Hour)
	write(filepath.Join(bin, "ccx"), "ccx 2")
	if err := os.Chtimes(filepath.Join(bin, "ccx"), later, later); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(bin, later, later); err != nil {
		t.Fatal(err)
	}
	if movedOffHome(checkout, home, root) {
		t.Fatal("an upgraded tool reads as current, so passes would run the old copy")
	}
	if err := moveOffHome(checkout, home, root); err != nil {
		t.Fatalf("a refresh: %v", err)
	}
	if got, err := os.ReadFile(filepath.Join(copies, localBinDir, "ccx")); err != nil || string(got) != "ccx 2" {
		t.Fatalf("the refreshed tool reads %q: %v", got, err)
	}
}

func TestCodexMovesToItsLocalCopyOnlyOnceTheCopyIsWhole(t *testing.T) {
	root := t.TempDir()
	t.Setenv("CODEX_HOME", "")
	t.Setenv("TMPDIR", "/original")
	t.Setenv("PATH", os.Getenv("PATH"))
	t.Setenv(toolsPathEnv, "")
	if err := useLocalTools(t.TempDir(), root); err != nil {
		t.Fatal(err)
	}
	if os.Getenv("CODEX_HOME") != "" || os.Getenv("TMPDIR") != "/original" {
		t.Fatalf("no copy, but CODEX_HOME=%q TMPDIR=%q", os.Getenv("CODEX_HOME"), os.Getenv("TMPDIR"))
	}
	codex := filepath.Join(root, homeDirsRoot, codexDir)
	if err := os.MkdirAll(codex, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(codex, "config.toml"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := useLocalTools(t.TempDir(), root); err != nil {
		t.Fatal(err)
	}
	if os.Getenv("CODEX_HOME") != codex {
		t.Fatalf("CODEX_HOME=%q", os.Getenv("CODEX_HOME"))
	}
	if temp := os.Getenv("TMPDIR"); strings.HasPrefix(codex, temp+string(os.PathSeparator)) || !exists(temp) {
		t.Fatalf("TMPDIR=%q holds codex's home or does not exist, so codex refuses its helpers", temp)
	}
}

func TestLocalToolsComeRightBeforeHomesBinSoTheTokenWrappersStayFirst(t *testing.T) {
	got := localToolsPath("/plugin/sprite-fresh-token:/home/s/.local/bin:/usr/bin", "/home/s", "/tmp/lr-git")
	want := "/plugin/sprite-fresh-token:/tmp/lr-git/home/.codex/packages/standalone/current/bin:/tmp/lr-git/home/.local/bin:/home/s/.local/bin:/usr/bin"
	if got != want {
		t.Fatalf("PATH = %s", got)
	}
	if got := localToolsPath("/usr/bin", "/home/s", "/tmp/lr-git"); got != "/usr/bin" {
		t.Fatalf("a PATH without home's bin became %s", got)
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
