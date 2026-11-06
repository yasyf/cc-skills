package roll

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

type Moved struct{ From, To string }

func (m Moved) Error() string {
	return fmt.Sprintf("the lr install link moved from %s to %s", m.From, m.To)
}

func Watch(ctx context.Context) (context.Context, error) {
	pack := os.Getenv("BINRUN_PLUGIN_ROOT")
	started := filepath.Base(pack)
	if pack == "" || os.Getenv("LONG_RUNNING_PIN") == started {
		return ctx, nil
	}
	claude, err := drive.ConfigDir()
	if err != nil {
		return nil, err
	}
	if _, err := os.Lstat(link(claude)); errors.Is(err, fs.ErrNotExist) {
		return ctx, nil
	}
	return follow(ctx, claude, started), nil
}

func Exit(err error, log io.Writer) error {
	var moved Moved
	if !errors.As(err, &moved) {
		return err
	}
	_, _ = fmt.Fprintf(log, "%v, so this desk exits and launchd starts it on %s\n", moved, moved.To)
	return nil
}

func link(claude string) string { return filepath.Join(claude, "long-running", "current") }

func installed(claude string) string {
	resolved, err := filepath.EvalSymlinks(link(claude))
	if err != nil {
		return ""
	}
	return filepath.Base(resolved)
}

func follow(ctx context.Context, claude, started string) context.Context {
	taking, end := context.WithCancelCause(ctx)
	go func() {
		end(watch(taking, filepath.Dir(link(claude)), func() {
			if now := installed(claude); now != started {
				end(Moved{From: started, To: now})
			}
		}))
	}()
	return taking
}
