package app

import (
	"io"
	"log/slog"
	"os"
	"time"
)

type App struct {
	ConfigPath string
	Log        *slog.Logger
	Now        func() time.Time
}

func New(stderr io.Writer) *App {
	return &App{
		Log: slog.New(slog.NewJSONHandler(stderr, nil)).With("drive", os.Getenv("LR_DRIVE"), "lane", os.Getenv("LR_LANE")),
		Now: time.Now,
	}
}
