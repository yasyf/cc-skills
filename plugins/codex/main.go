// codex-ask — the one way this plugin runs codex: print REPLY_FILE:/LOG_FILE:/
// AWAIT: lines, launch the pinned `codex exec` detached so a Bash-tool timeout
// kill can't destroy finished work, then block on a status file. Every run lands
// under a fixed registry base so --ps/--collect/--await can reach a dead caller's
// runs. See AGENTS.md; recover a killed call with `codex-ask --await <dir>`.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

const (
	modelSol  = "gpt-5.6-sol"
	modelLuna = "gpt-5.6-luna"
	effort    = "xhigh"

	pruneAgeS = 7 * 24 * 3600
)

const (
	turnStartedMarker   = `"type":"turn.started"`
	turnCompletedMarker = `"type":"turn.completed"`
	turnFailedMarker    = `"type":"turn.failed"`
)

const usageStr = "usage: codex-ask [-m sol|luna] [-s ABS_DIR] [--image] [--schema FILE] " +
	"[--skip-git-repo-check] [QUESTION_FILE | - | QUESTION_TEXT]"

var terminal = []string{"completed", "failed", "no-run"}

// runPrefixes: --ps prunes only codex-ask's own minted dirs, never a caller lane.
var runPrefixes = []string{"codex-ask.", "codex-root."}

// selfPath is the resolved absolute path of this binary (Python's SELF).
var selfPath string

func main() {
	// The ambient OPENAI_API_KEY is billing-capped and blocks the hosted image_gen
	// tool, so every mode drops it and codex OAuth-auths.
	_ = os.Unsetenv("OPENAI_API_KEY")
	initSelf()

	args := os.Args[1:]
	switch {
	case len(args) == 2 && args[0] == "--worker":
		runWorker(args[1])
	case len(args) == 2 && args[0] == "--detach-middle":
		detachMiddle(args[1])
	case len(args) >= 1 && args[0] == "--await":
		awaitMode(argOrEmpty(args, 1))
	case len(args) >= 1 && args[0] == "--collect":
		collectMode(argOrEmpty(args, 1))
	case len(args) >= 1 && args[0] == "--mint-root":
		mintRootMode(args[1:])
	case len(args) == 1 && args[0] == "--ps":
		psMode()
	case len(args) >= 1 && isConsumerSubcommand(args[0]):
		// Additive cc-interact subcommands (daemon, agent-*, channel, direct) —
		// plain words that never shadow the leading --flag cases above or askMode.
		runConsumer(args)
	default:
		askMode(args)
	}
}

func initSelf() {
	exe, err := os.Executable()
	if err != nil {
		return
	}
	if resolved, e := filepath.EvalSymlinks(exe); e == nil {
		exe = resolved
	}
	selfPath = exe
}

func die(msg string, code int) {
	fmt.Fprintln(os.Stderr, msg)
	os.Exit(code)
}

func usage() { die(usageStr, 2) }

func argOrEmpty(args []string, i int) string {
	if i < len(args) {
		return args[i]
	}
	return ""
}

func join(parts ...string) string { return filepath.Join(parts...) }

func nowSec() float64 { return float64(time.Now().UnixNano()) / 1e9 }

func exists(path string) bool {
	_, err := os.Stat(path) //nolint:gosec // stats a caller-supplied dispatch path
	return err == nil
}

func isFile(path string) bool {
	fi, err := os.Stat(path) //nolint:gosec // stats a caller-supplied dispatch path
	return err == nil && fi.Mode().IsRegular()
}

// isDir reports whether path is a directory, FOLLOWING symlinks — Python's
// Path.is_dir(). os.DirEntry.IsDir() is lstat-based and would drop a
// symlink-to-a-lane-dir from --collect/--ps enumeration.
func isDir(path string) bool {
	fi, err := os.Stat(path) //nolint:gosec // stats a caller-supplied dispatch path
	return err == nil && fi.IsDir()
}

func fileSize(path string) int64 {
	fi, err := os.Stat(path) //nolint:gosec // stats a caller-supplied dispatch path
	if err != nil {
		return 0
	}
	return fi.Size()
}

func readFile(path string) string {
	b, err := os.ReadFile(path) //nolint:gosec // reads the tool's own dispatch file by path, by design
	if err != nil {
		return ""
	}
	return string(b)
}

func allDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}
