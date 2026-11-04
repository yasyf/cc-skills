package awake

import (
	"context"
	"log/slog"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Every         = time.Minute
	WorkerRunning = "running"
	WorkerScript  = `for comm in /proc/[0-9]*/comm; do read -r name < "$comm" 2>/dev/null || continue; case $name in claude|codex) echo running; exit;; esac; done; echo none`
)

type Holder struct {
	Runner shell.Runner
	Task   string
	Every  time.Duration
	Log    *slog.Logger
	Wanted func(context.Context) (bool, error)
}

func Always(context.Context) (bool, error) {
	return true, nil
}

func WorkerAlive(runner shell.Runner) func(context.Context) (bool, error) {
	return func(ctx context.Context) (bool, error) {
		result, err := runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", WorkerScript}, Timeout: Every})
		if err != nil {
			return false, err
		}
		return strings.TrimSpace(result.Stdout) == WorkerRunning, nil
	}
}

func (h Holder) Run(ctx context.Context) {
	for {
		if err := h.refresh(ctx); err != nil {
			h.Log.Warn("the Sprite's awake task was not refreshed", "task", h.Task, "err", err)
		}
		select {
		case <-ctx.Done():
			if err := h.call(context.WithoutCancel(ctx), "DELETE"); err != nil {
				h.Log.Warn("the Sprite's awake task was not released", "task", h.Task, "err", err)
			}
			return
		case <-time.After(h.Every):
		}
	}
}

func (h Holder) refresh(ctx context.Context) error {
	wanted, err := h.Wanted(ctx)
	if err != nil || !wanted {
		return err
	}
	return h.call(ctx, "PUT", "-H", "Content-Type: application/json", "-d", `{"expire": "5m"}`)
}

func (h Holder) call(ctx context.Context, method string, body ...string) error {
	_, err := h.Runner.Run(ctx, shell.Command{Argv: append([]string{"/.sprite/bin/sprite-env", "curl", "-sSf", "-X", method, "/v1/tasks/" + h.Task}, body...), Timeout: h.Every})
	return err
}
