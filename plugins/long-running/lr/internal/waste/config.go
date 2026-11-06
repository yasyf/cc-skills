package waste

import (
	"cmp"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

type Floors struct {
	Dead        int64 `json:"dead"`
	FanOut      int64 `json:"fan_out"`
	Injected    int64 `json:"injected"`
	Skips       int64 `json:"skips"`
	Chores      int64 `json:"chores"`
	Reads       int64 `json:"reads"`
	Cost        int64 `json:"cost"`
	Duplicates  int64 `json:"duplicates"`
	Races       int64 `json:"races"`
	Uninstalled int64 `json:"uninstalled"`
	OwedReplies int64 `json:"owed_replies"`
	Compactions int64 `json:"compactions"`
}

type Config struct {
	Lane               string   `json:"lane"`
	Ignore             []string `json:"ignore"`
	Growth             float64  `json:"growth"`
	FanOutRecipients   int      `json:"fan_out_recipients"`
	Transcripts        int      `json:"transcripts"`
	HookLogs           string   `json:"hook_logs"`
	CodexSessions      string   `json:"codex_sessions"`
	MergeLookbackHours int      `json:"merge_lookback_hours"`
	Floors             Floors   `json:"floors"`
}

func defaults() Config {
	return Config{
		Lane:               "meta",
		Growth:             2,
		FanOutRecipients:   8,
		Transcripts:        200,
		MergeLookbackHours: 24,
		Floors: Floors{
			Dead:        20,
			FanOut:      40,
			Injected:    200_000,
			Skips:       100,
			Chores:      20,
			Reads:       5,
			Cost:        20_000_000,
			Duplicates:  2,
			Races:       1,
			Uninstalled: 30,
			OwedReplies: 2,
			Compactions: 6,
		},
	}
}

func ReadConfig(state string) (Config, error) {
	runner := struct {
		Waste Config `json:"waste"`
	}{Waste: defaults()}
	data, err := drive.ReadMacOnly(filepath.Join(state, "runner.json"))
	switch {
	case errors.Is(err, fs.ErrNotExist):
	case err != nil:
		return Config{}, err
	default:
		if err := json.Unmarshal(data, &runner); err != nil {
			return Config{}, fmt.Errorf("runner.json: %w", err)
		}
	}
	logs, err := hookLogs(runner.Waste.HookLogs)
	if err != nil {
		return Config{}, err
	}
	runner.Waste.HookLogs = logs
	sessions, err := codexSessions(runner.Waste.CodexSessions)
	if err != nil {
		return Config{}, err
	}
	runner.Waste.CodexSessions = sessions
	return runner.Waste, nil
}

func hookLogs(configured string) (string, error) {
	if dir := cmp.Or(configured, os.Getenv("CAPTAIN_HOOK_LOG_DIR")); dir != "" {
		return dir, nil
	}
	cache := os.Getenv("XDG_CACHE_HOME")
	if cache == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		cache = filepath.Join(home, ".cache")
	}
	return filepath.Join(cache, "captain-hook", "logs"), nil
}

func codexSessions(configured string) (string, error) {
	if configured != "" {
		return configured, nil
	}
	if home := os.Getenv("CODEX_HOME"); home != "" {
		return filepath.Join(home, "sessions"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".codex", "sessions"), nil
}
