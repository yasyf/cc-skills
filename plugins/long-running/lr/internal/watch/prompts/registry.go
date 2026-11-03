package prompts

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
)

const watchDirName = "prompt-watch"

type Desk struct {
	Terminal    string `json:"terminal"`
	Environment string `json:"environment"`
	Supervisor  bool   `json:"supervisor"`
}

type Entry struct {
	Drive        string          `json:"drive"`
	CCIDrive     string          `json:"cci_drive"`
	Program      string          `json:"program"`
	StateDir     string          `json:"state_dir"`
	OrcaRun      string          `json:"orca_run"`
	RootTerminal string          `json:"root_terminal"`
	SlackCards   bool            `json:"slack_cards"`
	Sessions     []string        `json:"sessions"`
	Desks        map[string]Desk `json:"desks"`
}

func (e Entry) Bus() string {
	if e.CCIDrive != "" {
		return e.CCIDrive
	}
	if e.Program != "" {
		return e.Program
	}
	return filepath.Base(e.StateDir)
}

func (e Entry) dir() string {
	return filepath.Join(e.StateDir, watchDirName)
}

func (e Entry) statePath() string {
	return filepath.Join(e.dir(), "state.json")
}

func (e Entry) deskNames() []string {
	names := make([]string, 0, len(e.Desks))
	for name := range e.Desks {
		names = append(names, name)
	}
	slices.Sort(names)
	return names
}

func configDir() (string, error) {
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude"), nil
}

var ErrNoDrive = errors.New("no registered drive")

func Find(name string) (Entry, error) {
	dir, err := configDir()
	if err != nil {
		return Entry{}, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "long-running", "drives", "*.json"))
	if err != nil {
		return Entry{}, err
	}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return Entry{}, err
		}
		var entry Entry
		if err := json.Unmarshal(data, &entry); err != nil {
			return Entry{}, fmt.Errorf("%s: %w", path, err)
		}
		if entry.Drive == name || entry.Bus() == name {
			return entry, nil
		}
	}
	return Entry{}, fmt.Errorf("%w named %s", ErrNoDrive, name)
}

func launchReceipts(run string) (string, error) {
	state := os.Getenv("XDG_STATE_HOME")
	if state == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		state = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(state, "long-running", "orca-launch", run), nil
}

func receiptLanes(run string) (map[string]string, error) {
	dir, err := launchReceipts(run)
	if err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	lanes := map[string]string{}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var receipt struct {
			Result struct {
				DispatchID string `json:"dispatchId"`
			} `json:"result"`
		}
		if json.Unmarshal(data, &receipt) == nil && receipt.Result.DispatchID != "" {
			lanes[receipt.Result.DispatchID] = strings.TrimSuffix(filepath.Base(path), ".json")
		}
	}
	return lanes, nil
}
