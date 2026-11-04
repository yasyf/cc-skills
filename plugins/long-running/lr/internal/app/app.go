package app

import (
	"io"
	"log/slog"
	"os"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/actions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orcarpc"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type App struct {
	ConfigPath string
	Runner     shell.Runner
	CCX        ccx.Client
	Ledger     ledger.Store
	Actions    actions.Store
	Stderr     io.Writer
	Log        *slog.Logger
	Now        func() time.Time
}

func New(stderr io.Writer) *App {
	runner := orcarpc.NewRunner(shell.Exec{})
	return &App{
		Runner:  runner,
		CCX:     ccx.CLI{Runner: runner},
		Ledger:  ledger.CLI{Runner: runner},
		Actions: actions.Files{},
		Stderr:  stderr,
		Log:     slog.New(slog.NewJSONHandler(stderr, nil)).With("drive", os.Getenv("LR_DRIVE"), "lane", os.Getenv("LR_LANE")),
		Now:     time.Now,
	}
}

func (a *App) Config() (config.Config, error) {
	path, err := a.ConfigFile()
	if err != nil {
		return config.Config{}, err
	}
	return config.Load(path, a.Stderr)
}

func (a *App) ConfigFile() (string, error) {
	if a.ConfigPath == "" {
		entry, err := drive.Session()
		if err != nil {
			return "", err
		}
		a.ConfigPath = entry.Config()
	}
	return a.ConfigPath, nil
}
