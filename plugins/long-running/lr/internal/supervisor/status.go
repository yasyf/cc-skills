package supervisor

import (
	"context"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const statusRead = 10 * time.Second

func Status(cmd *cobra.Command, a *app.App) error {
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return err
	}
	env, err := lanes.NewEnv(a.Runner, cfg, state, a.Now)
	if err != nil {
		return err
	}
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	lease, err := s.Lease(cfg.Drive)
	if err != nil {
		return err
	}
	ctx := cmd.Context()
	running := ""
	alive := processAlive(lease.PID)
	if alive {
		if running, err = runningBinary(ctx, a.Runner, runtime.GOOS, lease.PID); err != nil {
			return err
		}
	}
	writeStatus(cmd.OutOrStdout(), statusFacts{
		Lease: lease, Alive: alive, Running: running, Pinned: env.PinnedBinary(), Installed: Installed(env.Claude), Age: a.Now().Sub(lease.Renewed).Round(time.Second),
	})
	return nil
}

type statusFacts struct {
	Lease                      store.Lease
	Alive                      bool
	Running, Pinned, Installed string
	Age                        time.Duration
}

func writeStatus(w io.Writer, f statusFacts) {
	fmt.Fprintf(w, "supervisor pid %d generation %d, lease renewed %s ago\n", f.Lease.PID, f.Lease.Generation, f.Age)
	fmt.Fprintf(w, "running lr %s, pinned lr %s\n", orUnread(f.Running), orUnread(f.Pinned))
	fmt.Fprintf(w, "served pack %s, installed pack %s\n", orUnread(f.Lease.Version), orUnread(f.Installed))
	fmt.Fprintln(w, notReexecuted(f))
}

func orUnread(version string) string {
	if version == "" {
		return "unread"
	}
	return version
}

func notReexecuted(f statusFacts) string {
	switch {
	case !f.Alive:
		return fmt.Sprintf("not running: pid %d is gone, so launchd or its Sprite restarts it on the installed pack", f.Lease.PID)
	case f.Installed == "":
		return "will not re-execute: long-running/current resolves to nothing, so it never sees a newer pack"
	case !Behind(f.Lease.Version, f.Installed) && f.Pinned != f.Running:
		return fmt.Sprintf("will not re-execute: it checks the pin only when the installed pack passes the one it serves, and %s has not passed %s", f.Installed, f.Lease.Version)
	case f.Pinned == f.Running:
		return "nothing to re-execute: it runs the pinned binary and serves a newer pack in place"
	}
	return "re-execute owed: the pin moved, so it re-executes at its next tick unless a gc pass holds it; an old renewal means the tick itself is stuck"
}

func processAlive(pid int) bool {
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

func runningBinary(ctx context.Context, runner shell.Runner, goos string, pid int) (string, error) {
	path := fmt.Sprintf("/proc/%d/exe", pid)
	if goos != "linux" {
		listed, err := runner.Run(ctx, shell.Command{Argv: []string{"lsof", "-a", "-p", strconv.Itoa(pid), "-d", "txt", "-Fn"}, Timeout: statusRead})
		if err != nil {
			return "", err
		}
		_, after, found := strings.Cut(listed.Stdout, "\nn")
		if !found {
			return "", fmt.Errorf("lsof names no binary for pid %d", pid)
		}
		path, _, _ = strings.Cut(after, "\n")
	}
	version, err := runner.Run(ctx, shell.Command{Argv: []string{path, "--version"}, Timeout: statusRead})
	if err != nil {
		return "", err
	}
	return strings.TrimPrefix(strings.TrimSpace(version.Stdout), "lr version "), nil
}
