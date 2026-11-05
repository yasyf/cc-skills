package prompts

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
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

func Bind(name, terminal string, cards bool) error {
	entry, err := Find(name)
	if err != nil {
		return err
	}
	dir, err := configDir()
	if err != nil {
		return err
	}
	path := filepath.Join(dir, "long-running", "drives", entry.Drive+".json")
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	if terminal != "" {
		if fields["root_terminal"], err = json.Marshal(terminal); err != nil {
			return err
		}
	}
	if cards {
		fields["slack_cards"] = json.RawMessage("true")
	}
	return writeAtomic(path, fields)
}

func stateHome() (string, error) {
	if state := os.Getenv("XDG_STATE_HOME"); state != "" {
		return state, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "state"), nil
}

func launchReceipts(run string) (string, error) {
	state, err := stateHome()
	if err != nil {
		return "", err
	}
	return filepath.Join(state, "long-running", "orca-launch", run), nil
}

type receiptCache struct {
	dir   string
	moved time.Time
	read  map[string]string
}

func (c *receiptCache) lanes(run string) (map[string]string, error) {
	dir, err := launchReceipts(run)
	if err != nil {
		return nil, err
	}
	info, err := os.Stat(dir)
	if errors.Is(err, fs.ErrNotExist) {
		return map[string]string{}, nil
	}
	if err != nil {
		return nil, err
	}
	if dir == c.dir && info.ModTime().Equal(c.moved) {
		return c.read, nil
	}
	read, err := receiptLanes(dir)
	if err != nil {
		return nil, err
	}
	c.dir, c.moved, c.read = dir, info.ModTime(), read
	return read, nil
}

func receiptLanes(dir string) (map[string]string, error) {
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
