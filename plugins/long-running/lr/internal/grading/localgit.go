package grading

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
)

const (
	gitDir    = ".git"
	fetchLock = ".fetch.lock"
	attrTree  = "attr.tree"
	headTree  = "HEAD"
)

func readyClone(checkout string) error {
	info, err := os.Lstat(filepath.Join(checkout, gitDir))
	if err != nil || info.Mode().IsRegular() {
		return err
	}
	if err := wholeHistory(checkout); err != nil {
		return err
	}
	return attributesFromHead(checkout)
}

func wholeHistory(checkout string) error {
	shallow, err := isShallow(checkout)
	if err != nil || !shallow {
		return err
	}
	lock, err := lockFetch(checkout)
	if err != nil {
		return err
	}
	defer lock.Close()
	shallow, err = isShallow(checkout)
	if err != nil || !shallow {
		return err
	}
	if out, err := exec.Command("git", "-C", checkout, "fetch", "-q", "--unshallow", "origin").CombinedOutput(); err != nil {
		return fmt.Errorf("git fetch --unshallow: %w: %s", err, out)
	}
	return nil
}

func isShallow(checkout string) (bool, error) {
	out, err := exec.Command("git", "-C", checkout, "rev-parse", "--is-shallow-repository").CombinedOutput()
	if err != nil {
		return false, fmt.Errorf("git rev-parse --is-shallow-repository: %w: %s", err, out)
	}
	return strings.TrimSpace(string(out)) == "true", nil
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
