package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// os.Executable reads /proc/self/exe, which has already resolved the plugin's
// bin/codex-ask symlink; argv[0] still names the path the caller invoked.
func invocationPath() (string, error) {
	path := os.Args[0]
	if !strings.ContainsRune(path, os.PathSeparator) {
		found, err := exec.LookPath(path)
		if err != nil && !errors.Is(err, exec.ErrDot) {
			return "", err
		}
		path = found
	}
	return filepath.Abs(path)
}
