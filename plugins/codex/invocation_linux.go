package main

import (
	"os"
	"os/exec"
	"strings"
)

// os.Executable reads /proc/self/exe, which has already resolved the plugin's
// bin/codex-ask symlink; argv[0] still names the path the caller invoked.
func invocationPath() (string, error) {
	if strings.ContainsRune(os.Args[0], os.PathSeparator) {
		return os.Args[0], nil
	}
	return exec.LookPath(os.Args[0])
}
