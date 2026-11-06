//go:build !darwin

package roll

import (
	"context"

	"github.com/fsnotify/fsnotify"
)

func watch(ctx context.Context, dir string, changed func()) error {
	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		return err
	}
	defer func() { _ = watcher.Close() }()
	if err := watcher.Add(dir); err != nil {
		return err
	}
	changed()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-watcher.Events:
			changed()
		case err := <-watcher.Errors:
			return err
		}
	}
}
