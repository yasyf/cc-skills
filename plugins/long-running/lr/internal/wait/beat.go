package wait

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	BeatTopic = "wait:"
	beatEvery = 30 * time.Minute
)

func (w *waiter) beat(ctx context.Context, until time.Time) {
	for _, s := range w.sources {
		if c, ok := s.(cciSource); ok && c.to == c.lane {
			if err := w.beatFor(ctx, c, until); err != nil {
				fmt.Fprintf(w.Stderr, "lr wait: %s's waiting state did not post, so a sweep may read it as gone: %v\n", c.lane, err)
			}
		}
	}
}

func (w *waiter) beatFor(ctx context.Context, s cciSource, until time.Time) error {
	dir, err := armedDir()
	if err != nil {
		return err
	}
	stamp := filepath.Join(filepath.Dir(dir), "beats", s.drive+"-"+s.lane)
	last, err := os.ReadFile(stamp)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	if at, err := time.Parse(time.RFC3339, string(last)); err == nil && w.Now().Sub(at) < beatEvery {
		return nil
	}
	text := fmt.Sprintf("WAITING until %s on records to %s", until.In(pacific).Format("3:04 PM"), s.lane)
	argv := []string{"cci", "post", "--drive", s.drive, "--lane", s.lane, "--kind", "state", "--topic", BeatTopic + s.lane, "--text", text}
	if _, err := w.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout}); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(stamp), 0o700); err != nil {
		return err
	}
	return os.WriteFile(stamp, []byte(w.Now().Format(time.RFC3339)), 0o600)
}
