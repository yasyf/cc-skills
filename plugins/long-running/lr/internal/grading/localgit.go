package grading

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"io/fs"
	"os"
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
)

var uncopiedGitDirs = []string{objectsDir, "cc-notes", "logs"}

func onLocalDisk(checkout, root string) error {
	git := filepath.Join(checkout, gitDir)
	if info, err := os.Lstat(git); err != nil || !info.IsDir() {
		return err
	}
	if err := moveGit(checkout, git, root); err != nil {
		return err
	}
	return os.RemoveAll(git + replacedSuffix)
}

func moveGit(checkout, git, root string) error {
	lock, err := os.OpenFile(checkout+fetchLock, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	if info, err := os.Lstat(git); err != nil || !info.IsDir() {
		return err
	}
	sum := sha256.Sum256([]byte(checkout))
	target := filepath.Join(root, hex.EncodeToString(sum[:])[:clonePathChars])
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
		} else if !exists(target) {
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
		return os.Symlink(link, to)
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
