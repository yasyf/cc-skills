package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/yasyf/daemonkit/daemonrole"
	"github.com/yasyf/daemonkit/version"
)

func appDaemonRole() daemonrole.Classifier {
	role := daemonrole.Classifier{
		RoleID: "com.yasyf.codex-ask", RolePath: filepath.Join(appPaths().StateDir(), "bin", binaryName),
	}
	if err := provisionDaemonRole(role.RolePath); err != nil {
		panic(err)
	}
	return role
}

func provisionDaemonRole(rolePath string) error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	executable, err = filepath.EvalSymlinks(executable)
	if err != nil {
		return err
	}
	if target, targetErr := filepath.EvalSymlinks(rolePath); targetErr == nil {
		currentVersion, versionErr := executableVersion(rolePath)
		if target == executable || (versionErr == nil && !version.Newer(appVersion, currentVersion)) {
			return nil
		}
	}
	if err := os.MkdirAll(filepath.Dir(rolePath), 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(rolePath), ".codex-ask-role-*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Remove(tmpPath); err != nil {
		return err
	}
	if err := os.Symlink(executable, tmpPath); err != nil {
		return err
	}
	defer func() { _ = os.Remove(tmpPath) }()
	return os.Rename(tmpPath, rolePath)
}

func executableVersion(path string) (string, error) {
	cmd := exec.Command(path, "--version") //nolint:gosec // the role path is the exact local service identity
	var out bytes.Buffer
	cmd.Stdout = &out
	if err := cmd.Start(); err != nil {
		return "", err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	timer := time.NewTimer(2 * time.Second)
	defer timer.Stop()
	select {
	case err := <-done:
		if err != nil {
			return "", err
		}
	case <-timer.C:
		_ = cmd.Process.Kill()
		<-done
		return "", errors.New("codex-ask daemon role version timed out")
	}
	return strings.TrimSpace(out.String()), nil
}
