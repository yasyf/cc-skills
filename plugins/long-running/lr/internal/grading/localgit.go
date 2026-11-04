package grading

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"
)

const (
	spriteLocalDisk = "/tmp/lr-git"
	gitDir          = ".git"
	objectsDir      = "objects"
	fetchLock       = ".fetch.lock"
	replacedSuffix  = ".replaced"
	stagedSuffix    = ".part"
	clonePathChars  = 12
	attrTree        = "attr.tree"
	headTree        = "HEAD"
	homeCopySuffix  = ".home"
	worktreeSuffix  = "-worktree"
	homeDirsRoot    = "home"
	moveLock        = ".move.lock"
	moveLog         = "move.log"
	sessionsDir     = "sessions"
	sqliteMark      = ".sqlite"
	localBinDir     = ".local/bin"
	codexDir        = ".codex"
	codexReleases   = ".codex/packages/standalone"
	codexBinDir     = ".codex/packages/standalone/current/bin"
	toolsPathEnv    = "SPRITE_TOOLS_PATH"
	localTempDir    = "tmp"
)

var (
	uncopiedGitDirs = []string{objectsDir, "cc-notes", "logs"}
	linkedHomeDirs  = []string{".cache/cc-context", ".config/ccx", ".local/share/gh"}
	copiedToolDirs  = []string{localBinDir, codexDir, codexReleases}
)

func onLocalDisk(checkout, root string) error {
	git := filepath.Join(checkout, gitDir)
	info, err := os.Lstat(git)
	if err != nil || info.Mode().IsRegular() {
		return err
	}
	if info.IsDir() {
		if err := moveGit(checkout, git, root); err != nil {
			return err
		}
		if err := os.RemoveAll(git + replacedSuffix); err != nil {
			return err
		}
	}
	return attributesFromHead(checkout)
}

func attributesFromHead(checkout string) error {
	set, err := exec.Command("git", "-C", checkout, "config", "--get", attrTree).Output()
	if err == nil && strings.TrimSpace(string(set)) == headTree {
		return nil
	}
	lock, err := lockFetch(checkout)
	if err != nil {
		return err
	}
	defer lock.Close()
	if out, err := exec.Command("git", "-C", checkout, "config", attrTree, headTree).CombinedOutput(); err != nil {
		return fmt.Errorf("git config %s: %w: %s", attrTree, err, out)
	}
	return nil
}

func lockFetch(checkout string) (*os.File, error) {
	lock, err := os.OpenFile(checkout+fetchLock, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		lock.Close()
		return nil, err
	}
	return lock, nil
}

func moveGit(checkout, git, root string) error {
	lock, err := lockFetch(checkout)
	if err != nil {
		return err
	}
	defer lock.Close()
	if info, err := os.Lstat(git); err != nil || !info.IsDir() {
		return err
	}
	target := clonePath(checkout, root)
	staged := target + stagedSuffix
	for _, stale := range []string{staged, target} {
		if err := os.RemoveAll(stale); err != nil {
			return err
		}
	}
	// A pass may fetch or repack during the copy, so every ref is copied before any object it can name, and the
	// objects twice: the second walk takes the packs a repack made from packs the first walk found deleted.
	if err := copyTree(git, staged, uncopiedGitDirs...); err != nil {
		return err
	}
	for range 2 {
		if err := copyTree(filepath.Join(git, objectsDir), filepath.Join(staged, objectsDir)); err != nil {
			return err
		}
	}
	syscall.Sync()
	if err := os.Rename(staged, target); err != nil {
		return err
	}
	if err := os.Rename(git, git+replacedSuffix); err != nil {
		return err
	}
	return os.Symlink(target, git)
}

func clonePath(checkout, root string) string {
	sum := sha256.Sum256([]byte(checkout))
	return filepath.Join(root, hex.EncodeToString(sum[:])[:clonePathChars])
}

func startMoveOffHome(checkout, root string) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	if err := useLocalTools(home, root); err != nil {
		return err
	}
	if movedOffHome(checkout, home, root) {
		return nil
	}
	self, err := os.Executable()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(root, 0o750); err != nil {
		return err
	}
	log, err := os.OpenFile(filepath.Join(root, moveLog), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer log.Close()
	cmd := exec.Command(self, "grade", moveOffHomeVerb, checkout)
	cmd.Stdout, cmd.Stderr = log, log
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}

func movedOffHome(checkout, home, root string) bool {
	if info, err := os.Lstat(checkout); err != nil || info.Mode()&fs.ModeSymlink == 0 {
		return false
	}
	return !slices.ContainsFunc(copiedToolDirs, func(dir string) bool {
		tool, err := os.Stat(filepath.Join(home, dir))
		if err != nil {
			return false
		}
		copied, err := os.Stat(filepath.Join(root, homeDirsRoot, dir))
		return err != nil || tool.ModTime().After(copied.ModTime())
	})
}

func moveOffHome(checkout, home, root string) error {
	if err := os.MkdirAll(root, 0o750); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(root, moveLock), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil
		}
		return err
	}
	if err := moveWorktree(checkout, root); err != nil {
		return err
	}
	for _, dir := range linkedHomeDirs {
		if err := moveHomeDir(filepath.Join(home, dir), filepath.Join(root, homeDirsRoot, dir)); err != nil {
			return err
		}
	}
	for _, dir := range copiedToolDirs {
		if err := refreshToolCopy(filepath.Join(home, dir), filepath.Join(root, homeDirsRoot, dir)); err != nil {
			return err
		}
	}
	return nil
}

func moveWorktree(checkout, root string) error {
	info, err := os.Lstat(checkout)
	if err != nil || info.Mode()&fs.ModeSymlink != 0 {
		return err
	}
	if exec.Command("git", "-C", checkout, "rev-parse", "--verify", "--quiet", headTree).Run() != nil {
		return nil
	}
	clone, err := os.Readlink(filepath.Join(checkout, gitDir))
	if err != nil {
		return err
	}
	target := clonePath(checkout, root) + worktreeSuffix
	staged := target + stagedSuffix
	index := target + ".index"
	for _, stale := range []string{staged, target, index} {
		if err := os.RemoveAll(stale); err != nil {
			return err
		}
	}
	if err := os.MkdirAll(staged, 0o750); err != nil {
		return err
	}
	build := exec.Command("git", "-C", checkout, "read-tree", "-u", "--reset", headTree)
	build.Env = append(os.Environ(), "GIT_INDEX_FILE="+index, "GIT_WORK_TREE="+staged, "GIT_LFS_SKIP_SMUDGE=1")
	if out, err := build.CombinedOutput(); err != nil {
		return fmt.Errorf("git read-tree into %s: %w: %s", staged, err, out)
	}
	if err := os.Symlink(clone, filepath.Join(staged, gitDir)); err != nil {
		return err
	}
	if err := os.Rename(index, filepath.Join(clone, "index")); err != nil {
		return err
	}
	return swapForLink(checkout, staged, target)
}

func moveHomeDir(path, target string) error {
	info, err := os.Lstat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil || !info.IsDir() {
		return err
	}
	staged := target + stagedSuffix
	for _, stale := range []string{staged, target} {
		if err := os.RemoveAll(stale); err != nil {
			return err
		}
	}
	if err := copyTree(path, staged); err != nil {
		return err
	}
	err = swapForLink(path, staged, target)
	if errors.Is(err, syscall.EXDEV) {
		return os.RemoveAll(target)
	}
	return err
}

func refreshToolCopy(path, target string) error {
	if _, err := os.Stat(path); errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	entries, err := os.ReadDir(path)
	if err != nil {
		return err
	}
	var stays []string
	for _, entry := range entries {
		if entry.Name() == sessionsDir || strings.Contains(entry.Name(), sqliteMark) {
			stays = append(stays, entry.Name())
		}
	}
	into := target
	if !exists(target) {
		into = target + stagedSuffix
	}
	if err := copyTree(path, into, stays...); err != nil {
		return err
	}
	if slices.Contains(stays, sessionsDir) {
		link := filepath.Join(into, sessionsDir)
		if _, err := os.Lstat(link); err != nil {
			if err := os.Symlink(filepath.Join(path, sessionsDir), link); err != nil {
				return err
			}
		}
	}
	for _, entry := range entries {
		if err := relinkInsideCopy(path, into, entry); err != nil {
			return err
		}
	}
	if into != target {
		if err := os.Rename(into, target); err != nil {
			return err
		}
	}
	now := time.Now()
	return os.Chtimes(target, now, now)
}

func relinkInsideCopy(path, into string, entry fs.DirEntry) error {
	if entry.Type()&fs.ModeSymlink == 0 {
		return nil
	}
	link, err := os.Readlink(filepath.Join(path, entry.Name()))
	if err != nil {
		return err
	}
	inside, found := strings.CutPrefix(link, path+string(os.PathSeparator))
	if !found {
		return nil
	}
	copied := filepath.Join(into, entry.Name())
	if err := os.Remove(copied); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return os.Symlink(inside, copied)
}

func useLocalTools(home, root string) error {
	copies := filepath.Join(root, homeDirsRoot)
	codex := filepath.Join(copies, codexDir)
	if exists(filepath.Join(codex, "config.toml")) {
		// Codex refuses to write its helper binaries under the temp dir, and its home is under /tmp.
		temp := filepath.Join(root, localTempDir)
		if err := os.MkdirAll(temp, 0o750); err != nil {
			return err
		}
		if err := os.Setenv("TMPDIR", temp); err != nil {
			return err
		}
		if err := os.Setenv("CODEX_HOME", codex); err != nil {
			return err
		}
	}
	for _, name := range []string{"PATH", toolsPathEnv} {
		if err := os.Setenv(name, localToolsPath(os.Getenv(name), home, root)); err != nil {
			return err
		}
	}
	return nil
}

func localToolsPath(path, home, root string) string {
	copies := filepath.Join(root, homeDirsRoot)
	bin := filepath.Join(home, localBinDir)
	entries := filepath.SplitList(path)
	for index, entry := range entries {
		if entry == bin {
			local := []string{filepath.Join(copies, codexBinDir), filepath.Join(copies, localBinDir)}
			return strings.Join(slices.Insert(entries, index, local...), string(os.PathListSeparator))
		}
	}
	return path
}

func swapForLink(path, staged, target string) error {
	if err := os.Rename(staged, target); err != nil {
		return err
	}
	if err := os.Rename(path, path+homeCopySuffix); err != nil {
		return err
	}
	return os.Symlink(target, path)
}

func copyTree(from, to string, skip ...string) error {
	entries, err := os.ReadDir(from)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if err := os.MkdirAll(to, 0o750); err != nil {
		return err
	}
	for _, entry := range entries {
		name := entry.Name()
		if slices.Contains(skip, name) || transientGitFile(name) {
			continue
		}
		source, target := filepath.Join(from, name), filepath.Join(to, name)
		if entry.IsDir() {
			err = copyTree(source, target)
		} else if outOfDate(entry, target) {
			err = copyGitFile(source, target, entry)
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func outOfDate(entry fs.DirEntry, target string) bool {
	copied, err := os.Lstat(target)
	if err != nil {
		return true
	}
	info, err := entry.Info()
	return err == nil && info.ModTime().After(copied.ModTime())
}

func transientGitFile(name string) bool {
	return strings.HasSuffix(name, ".lock") || strings.HasPrefix(name, "tmp_") || strings.HasPrefix(name, ".tmp-") || strings.HasPrefix(name, "multi-pack-index")
}

func copyGitFile(from, to string, entry fs.DirEntry) error {
	info, err := entry.Info()
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if info.Mode()&fs.ModeSymlink != 0 {
		link, err := os.Readlink(from)
		if errors.Is(err, fs.ErrNotExist) {
			return nil
		}
		if err != nil {
			return err
		}
		if strings.HasPrefix(link, "../") {
			link = filepath.Join(filepath.Dir(from), link)
		}
		if err := os.Remove(to); err != nil && !errors.Is(err, fs.ErrNotExist) {
			return err
		}
		return os.Symlink(link, to)
	}
	if !info.Mode().IsRegular() {
		return nil
	}
	source, err := os.Open(from)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer source.Close()
	staged := to + stagedSuffix
	out, err := os.OpenFile(staged, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, info.Mode().Perm()|0o200)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, source); err != nil {
		out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	if err := os.Chmod(staged, info.Mode().Perm()); err != nil {
		return err
	}
	return os.Rename(staged, to)
}
