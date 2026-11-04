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
	Binary  string
	Read    time.Time
	Holding func() bool
	Serve   func(version string) error
}

func (u *Upgrade) Stale(ctx context.Context) (bool, error) {
	if u.Holding() {
		return false, nil
	}
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
