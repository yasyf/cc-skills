package supervisor

import (
	"context"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
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
	Env      lanes.Env
	Running  string
	Binary   string
	Read     time.Time
	Settings map[string]string
	Holding  func() bool
	Yield    func(bool)
	Serve    func(version string) error
}

func readSettings(state string) (map[string]string, error) {
	settings := map[string]string{}
	for _, name := range []string{configFile, runnerFile} {
		read, err := orca.Settings(filepath.Join(state, name))
		if err != nil {
			return nil, err
		}
		settings[name] = read
	}
	return settings, nil
}

func (u *Upgrade) restartOwed() bool {
	for _, name := range []string{configFile, runnerFile} {
		info, err := os.Stat(filepath.Join(u.Env.State, name))
		if err == nil && info.ModTime().After(u.Read) {
			return true
		}
	}
	if !Behind(u.Running, Installed(u.Env.Claude)) {
		return false
	}
	pinned := u.Env.PinnedBinary()
	return pinned == "" || pinned != u.Binary
}

func (u *Upgrade) Stale(ctx context.Context) (bool, error) {
	if u.Holding() {
		u.Yield(u.restartOwed())
		return false, nil
	}
	u.Yield(true)
	stale, err := u.restartDue(ctx)
	if !stale {
		u.Yield(false)
	}
	return stale, err
}

func (u *Upgrade) restartDue(ctx context.Context) (bool, error) {
	if changed, err := u.configChanged(ctx); changed || err != nil {
		return changed, err
	}
	installed := Installed(u.Env.Claude)
	if !Behind(u.Running, installed) {
		return false, nil
	}
	if pinned := u.Env.PinnedBinary(); pinned == "" || pinned != u.Binary {
		return true, nil
	}
	if err := u.Serve(installed); err != nil {
		return false, err
	}
	u.Running = installed
	return false, nil
}

func (u *Upgrade) configChanged(ctx context.Context) (bool, error) {
	checked := u.Read
	for _, name := range []string{configFile, runnerFile} {
		path := filepath.Join(u.Env.State, name)
		info, err := os.Stat(path)
		if err != nil {
			return false, err
		}
		if !info.ModTime().After(u.Read) {
			continue
		}
		settings, err := orca.Settings(path)
		if err != nil {
			return false, err
		}
		if settings == u.Settings[name] {
			if info.ModTime().After(checked) {
				checked = info.ModTime()
			}
			continue
		}
		_, err = u.Env.Bus.Post(ctx, client.Draft{Drive: u.Env.Config.Drive, Lane: lanes.Self, Kind: "report", To: []string{lanes.Root}, ID: fmt.Sprintf("config-changed:%s:%d", name, info.ModTime().Unix()), Text: "CONFIG-CHANGED " + name + " changed after the supervisor read it, so the supervisor restarts to read it fresh"})
		if err != nil {
			return false, fmt.Errorf("%w: cci post: %w", lanes.ErrUnread, err)
		}
		return true, nil
	}
	u.Read = checked
	return false, nil
}

func Reexec(claude string, exec func(string, []string, []string) error) error {
	path := filepath.Join(claude, "long-running", "current", "bin", "lr")
	return exec(path, append([]string{path}, os.Args[1:]...), os.Environ())
}
