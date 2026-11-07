package install

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	installDesk = "install-desk"
	startLayout = "Mon Jan _2 15:04:05 2006"
	noPack      = "no lr pack"
)

var (
	launchdPID = regexp.MustCompile(`(?m)^\tpid = (\d+)$`)
	packRoot   = regexp.MustCompile(`\bBINRUN_PLUGIN_ROOT=(\S+)`)
)

type desk struct {
	Label   string
	PID     int
	Started time.Time
	Version string
}

func staleDesks(ctx context.Context, runner shell.Runner, home, driveName, installed string, moved time.Time) ([]desk, error) {
	plists, err := filepath.Glob(filepath.Join(home, "Library", "LaunchAgents", "com.long-running."+driveName+".*.plist"))
	if err != nil {
		return nil, err
	}
	var stale []desk
	for _, plist := range plists {
		label := strings.TrimSuffix(filepath.Base(plist), ".plist")
		printed, err := ran(ctx, runner, "launchctl", "print", fmt.Sprintf("gui/%d/%s", os.Getuid(), label))
		if err != nil {
			return nil, err
		}
		pid := launchdPID.FindStringSubmatch(printed)
		if pid == nil {
			continue
		}
		listed, err := ran(ctx, runner, "ps", "-E", "-ww", "-o", "lstart=,command=", "-p", pid[1])
		if err != nil {
			return nil, err
		}
		if len(listed) < len(startLayout) {
			continue
		}
		started, err := time.ParseInLocation(startLayout, listed[:len(startLayout)], time.Local)
		if err != nil {
			return nil, fmt.Errorf("ps named %s's start %q: %w", label, listed[:len(startLayout)], err)
		}
		version := noPack
		if root := packRoot.FindStringSubmatch(listed); root != nil {
			version = filepath.Base(root[1])
		}
		if !started.Before(moved) || version == installed {
			continue
		}
		number, _ := strconv.Atoi(pid[1])
		stale = append(stale, desk{Label: label, PID: number, Started: started, Version: version})
	}
	return stale, nil
}

func ran(ctx context.Context, runner shell.Runner, argv ...string) (string, error) {
	result, err := runner.Run(ctx, shell.Command{Argv: argv})
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return "", nil
	}
	return result.Stdout, err
}

type poster interface {
	Post(ctx context.Context, to []string, body bus.Body) (bus.Record, error)
}

func tellInstallDesk(ctx context.Context, p poster, text string) error {
	_, err := p.Post(ctx, []string{installDesk}, bus.Note{Text: text})
	return err
}

func staleText(installed string, moved time.Time, stale []desk) string {
	at := moved.Local().Format("3:04 PM Jan 2")
	if len(stale) == 0 {
		return fmt.Sprintf("lr install %s at %s: every drive launchd desk runs %s or started after the install", installed, at, installed)
	}
	lines := make([]string, 0, len(stale))
	for _, d := range stale {
		lines = append(lines, fmt.Sprintf("%s pid %d since %s on %s", d.Label, d.PID, d.Started.Local().Format("3:04 PM Jan 2"), d.Version))
	}
	return fmt.Sprintf("lr install %s at %s: %d drive launchd desks still run a process that started before it, each restarting only on his word for that service: %s", installed, at, len(stale), strings.Join(lines, "; "))
}
