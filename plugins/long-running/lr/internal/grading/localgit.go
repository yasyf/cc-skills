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
)

var (
	uncopiedGitDirs = []string{objectsDir, "cc-notes", "logs"}
	movedHomeDirs   = []string{".cache/cc-context", ".config/ccx", ".local/share/gh", ".local/bin", ".codex"}
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
	if movedOffHome(checkout, home) {
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

func movedOffHome(checkout, home string) bool {
	if info, err := os.Lstat(checkout); err != nil || info.Mode()&fs.ModeSymlink == 0 {
		return false
	}
	return !slices.ContainsFunc(movedHomeDirs, func(dir string) bool {
		info, err := os.Lstat(filepath.Join(home, dir))
		return err == nil && info.IsDir()
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
	for _, dir := range movedHomeDirs {
		if err := moveHomeDir(filepath.Join(home, dir), filepath.Join(root, homeDirsRoot, dir)); err != nil {
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
	for range 2 {
		if err := copyTree(path, staged, stays...); err != nil {
			return err
		}
	}
	if slices.Contains(stays, sessionsDir) {
		if err := os.Symlink(filepath.Join(path+homeCopySuffix, sessionsDir), filepath.Join(staged, sessionsDir)); err != nil {
			return err
		}
	}
	return swapForLink(path, staged, target)
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
		} else if _, statErr := os.Lstat(target); statErr != nil {
			err = copyGitFile(source, target, entry)
		}
		if err != nil {
			return err
		}
	}
	return nil
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
		if !filepath.IsAbs(link) {
			link = filepath.Join(filepath.Dir(from), link)
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
