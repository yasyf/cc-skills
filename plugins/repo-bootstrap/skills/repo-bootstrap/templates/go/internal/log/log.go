// Package log configures the process-wide structured logger.
package log

import (
	"log/slog"
	"os"
)

// Setup installs the default slog logger. It writes human-readable text to
// stderr; set LOG_FORMAT=json for structured output and LOG_LEVEL to one of
// debug|info|warn|error (default info).
func Setup() {
	opts := &slog.HandlerOptions{Level: parseLevel(os.Getenv("LOG_LEVEL"))}
	var handler slog.Handler = slog.NewTextHandler(os.Stderr, opts)
	if os.Getenv("LOG_FORMAT") == "json" {
		handler = slog.NewJSONHandler(os.Stderr, opts)
	}
	slog.SetDefault(slog.New(handler))
}

func parseLevel(s string) slog.Level {
	switch s {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
