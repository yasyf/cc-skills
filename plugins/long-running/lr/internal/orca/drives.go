package orca

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	driveEnv       = "CLAUDE_LONG_RUNNING_DRIVE"
	landingRepoEnv = "CLAUDE_LONG_RUNNING_LANDING_REPO"
	sessionEnv     = "CLAUDE_CODE_SESSION_ID"
)

func drivesDir(env func(string) string) string {
	dir := env("CLAUDE_CONFIG_DIR")
	if dir == "" {
		dir = filepath.Join(env("HOME"), ".claude")
	}
	return filepath.Join(dir, "long-running", "drives")
}

func readDrive(path string) *drive.Entry {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var entry drive.Entry
	if json.Unmarshal(raw, &entry) != nil {
		return nil
	}
	return &entry
}

func allDrives(env func(string) string) []drive.Entry {
	paths, err := filepath.Glob(filepath.Join(drivesDir(env), "*.json"))
	if err != nil {
		return nil
	}
	entries := make([]drive.Entry, 0, len(paths))
	for _, path := range paths {
		if entry := readDrive(path); entry != nil {
			entries = append(entries, *entry)
		}
	}
	return entries
}

func namedDrive(env func(string) string, name string) *drive.Entry {
	if name == "" || strings.ContainsAny(name, "/\x00") {
		return nil
	}
	entry := readDrive(filepath.Join(drivesDir(env), name+".json"))
	if entry == nil || entry.Drive != name {
		return nil
	}
	return entry
}

func currentDriveEntry(env func(string) string) *drive.Entry {
	if name := env(driveEnv); name != "" {
		return namedDrive(env, name)
	}
	session := env(sessionEnv)
	if session == "" {
		return nil
	}
	for _, entry := range allDrives(env) {
		if slices.Contains(entry.Sessions, session) {
			return &entry
		}
	}
	return nil
}

func ownDriveEntry(ctx context.Context, env func(string) string, runner shell.Runner) *drive.Entry {
	if entry := currentDriveEntry(env); entry != nil || env(driveEnv) != "" {
		return entry
	}
	me, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "me"}})
	if err != nil {
		return nil
	}
	name := drive.BoundInCCI(me.Stdout)
	for _, entry := range allDrives(env) {
		if name != "" && entry.Bus() == name {
			return &entry
		}
	}
	return nil
}

func driveOfRun(env func(string) string, run string) *drive.Entry {
	if run == "" {
		return nil
	}
	for _, entry := range allDrives(env) {
		if entry.OrcaRun == run {
			return &entry
		}
	}
	return nil
}

func driveName(env func(string) string, run string) string {
	if name := env(driveEnv); name != "" {
		return name
	}
	if entry := currentDriveEntry(env); entry != nil {
		return entry.Drive
	}
	if entry := driveOfRun(env, run); entry != nil {
		return entry.Drive
	}
	return ""
}
