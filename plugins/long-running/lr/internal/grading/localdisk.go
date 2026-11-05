package grading

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

const codexSessionLife = 24 * time.Hour

func pruneLocalDisk(ctx context.Context, home, plugin string, now time.Time) error {
	out, err := exec.CommandContext(ctx, filepath.Join(plugin, "scripts", "binrun.sh"), "--", "gc").CombinedOutput()
	if err != nil {
		return fmt.Errorf("binrun gc: %w: %s", err, out)
	}
	sessions, err := os.OpenRoot(filepath.Join(home, ".codex", "sessions"))
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer sessions.Close()
	return fs.WalkDir(sessions.FS(), ".", func(path string, entry fs.DirEntry, walked error) error {
		if errors.Is(walked, fs.ErrNotExist) {
			return nil
		}
		if walked != nil {
			return walked
		}
		if entry.IsDir() {
			return nil
		}
		info, err := entry.Info()
		if errors.Is(err, fs.ErrNotExist) {
			return nil
		}
		if err != nil {
			return err
		}
		if now.Sub(info.ModTime()) <= codexSessionLife {
			return nil
		}
		if err := sessions.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
			return err
		}
		return nil
	})
}
