package supervisor

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	gateRefused = 3
	gateTimeout = 30 * time.Minute
	whyLimit    = 300
)

var versionPart = regexp.MustCompile(`\d+`)

func versionKey(version string) []int {
	before, _, _ := strings.Cut(version, "-")
	var key []int
	for _, part := range versionPart.FindAllString(before, -1) {
		number, err := strconv.Atoi(part)
		if err != nil {
			continue
		}
		key = append(key, number)
	}
	return key
}

func Behind(running, installed string) bool {
	return installed != "" && (running == "" || slices.Compare(versionKey(running), versionKey(installed)) < 0)
}

func Installed(claude string) string {
	resolved, err := filepath.EvalSymlinks(filepath.Join(claude, "long-running", "current"))
	if err != nil {
		return ""
	}
	return filepath.Base(resolved)
}

type Upgrade struct {
	Env     lanes.Env
	Running string
	Read    time.Time
	passed  map[string]bool
}

func (u *Upgrade) Stale(ctx context.Context) (bool, error) {
	if changed, err := u.configChanged(ctx); changed || err != nil {
		return changed, err
	}
	installed := Installed(u.Env.Claude)
	if !Behind(u.Running, installed) {
		return false, nil
	}
	if ok, known := u.passed[installed]; known {
		return ok, nil
	}
	if u.passed == nil {
		u.passed = map[string]bool{}
	}
	drive, repo := u.Env.Config.Drive, u.Env.Config.Repo
	gate := filepath.Join(u.Env.Claude, "long-running", "current", "bin", "replay.py")
	result, err := u.Env.Runner.Run(ctx, shell.Command{Argv: []string{gate, "gate", "--drive", drive, "--repo", repo, "--old", u.Running, "--new", installed}, Timeout: gateTimeout})
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		result.Code, result.Stderr = 1, err.Error()
	}
	u.passed[installed] = result.Code != gateRefused
	if result.Code == 0 || result.Code == gateRefused {
		return u.passed[installed], nil
	}
	why := fmt.Sprintf("exit %d", result.Code)
	said := strings.TrimSpace(result.Stderr)
	if said == "" {
		said = strings.TrimSpace(result.Stdout)
	}
	if said != "" {
		lines := strings.Split(said, "\n")
		why = lines[len(lines)-1]
		why = why[:min(whyLimit, len(why))]
	}
	_, err = u.Env.Bus.Post(ctx, client.Draft{Drive: drive, Lane: lanes.Self, Kind: "report", To: []string{lanes.Root}, ID: "replay-failed:" + installed, Text: fmt.Sprintf("the supervisor moves from %s to %s unreplayed: its replay gate exited %d: %s", u.Running, installed, result.Code, why)})
	if err != nil {
		return false, fmt.Errorf("%w: cci post: %w", lanes.ErrUnread, err)
	}
	return true, nil
}

func (u *Upgrade) configChanged(ctx context.Context) (bool, error) {
	for _, name := range []string{configFile, runnerFile} {
		info, err := os.Stat(filepath.Join(u.Env.State, name))
		if err != nil {
			return false, err
		}
		if !info.ModTime().After(u.Read) {
			continue
		}
		_, err = u.Env.Bus.Post(ctx, client.Draft{Drive: u.Env.Config.Drive, Lane: lanes.Self, Kind: "report", To: []string{lanes.Root}, ID: fmt.Sprintf("config-changed:%s:%d", name, info.ModTime().Unix()), Text: "CONFIG-CHANGED " + name + " changed after the supervisor read it, so the supervisor restarts to read it fresh"})
		if err != nil {
			return false, fmt.Errorf("%w: cci post: %w", lanes.ErrUnread, err)
		}
		return true, nil
	}
	return false, nil
}

func Reexec(claude string, exec func(string, []string, []string) error) error {
	path := filepath.Join(claude, "long-running", "current", "bin", "lr")
	return exec(path, append([]string{path}, os.Args[1:]...), os.Environ())
}
