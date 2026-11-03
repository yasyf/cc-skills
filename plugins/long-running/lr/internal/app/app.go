package app

import (
	"io"
	"log/slog"
	"os"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type App struct {
	ConfigPath string
	Runner     shell.Runner
	CCX        ccx.Client
	Ledger     ledger.Store
	Log        *slog.Logger
	Now        func() time.Time
}

func New(stderr io.Writer) *App {
	runner := shell.Exec{}
	return &App{
		Runner: runner,
		CCX:    ccx.CLI{Runner: runner},
		Ledger: ledger.CLI{Runner: runner},
		Log:    slog.New(slog.NewJSONHandler(stderr, nil)).With("drive", os.Getenv("LR_DRIVE"), "lane", os.Getenv("LR_LANE")),
		Now:    time.Now,
	}
}
