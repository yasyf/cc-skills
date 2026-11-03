package drive

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type Entry struct {
	Drive    string   `json:"drive"`
	CCIDrive string   `json:"cci_drive"`
	Program  string   `json:"program"`
	Ledger   string   `json:"ledger"`
	Checkout string   `json:"checkout"`
	StateDir string   `json:"state_dir"`
	Sessions []string `json:"sessions"`
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

func (e Entry) Root() string {
	return e.Sessions[len(e.Sessions)-1]
}

func (e Entry) Store() string {
	return filepath.Join(e.StateDir, "lr.sqlite")
}

func ConfigDir() (string, error) {
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude"), nil
}

func Named(name string) (Entry, error) {
	dir, err := ConfigDir()
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
		if entry.Bus() == name && len(entry.Sessions) > 0 {
			return entry, nil
		}
	}
	return Entry{}, fmt.Errorf("no drive registry entry for %s names a root session", name)
}
