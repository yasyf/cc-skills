package drive

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
)

type Pin struct {
	Name     string   `json:"name"`
	Delivers string   `json:"delivers"`
	Words    []string `json:"words"`
}

type Entry struct {
	Drive        string            `json:"drive"`
	CCIDrive     string            `json:"cci_drive"`
	Program      string            `json:"program"`
	Ledger       string            `json:"ledger"`
	Repo         string            `json:"repo"`
	GitCommonDir string            `json:"git_common_dir"`
	Checkout     string            `json:"checkout"`
	OrcaRun      string            `json:"orca_run"`
	StateDir     string            `json:"state_dir"`
	Sessions     []string          `json:"sessions"`
	StartedAt    string            `json:"started_at"`
	UpdatedAt    string            `json:"updated_at"`
	Parent       string            `json:"parent,omitempty"`
	Objective    string            `json:"objective,omitempty"`
	Reason       string            `json:"reason,omitempty"`
	Facts        map[string]string `json:"facts,omitempty"`
	Pinned       []Pin             `json:"pinned_rows,omitempty"`
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

func entriesDir() (string, error) {
	dir, err := ConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "long-running", "drives"), nil
}

func entries() ([]Entry, error) {
	dir, err := entriesDir()
	if err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	found := make([]Entry, 0, len(paths))
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		var entry Entry
		if err := json.Unmarshal(data, &entry); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		found = append(found, entry)
	}
	return found, nil
}

func Named(name string) (Entry, error) {
	found, err := entries()
	if err != nil {
		return Entry{}, err
	}
	live := slices.DeleteFunc(found, func(entry Entry) bool { return len(entry.Sessions) == 0 })
	for _, matches := range []func(Entry) bool{
		func(entry Entry) bool { return entry.Drive == name },
		func(entry Entry) bool { return entry.Bus() == name },
	} {
		if i := slices.IndexFunc(live, matches); i >= 0 {
			return live[i], nil
		}
	}
	return Entry{}, fmt.Errorf("no drive registry entry for %s names a coordinator session", name)
}

func Children(parent string) ([]Entry, error) {
	found, err := entries()
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(found, func(entry Entry) bool { return entry.Parent != parent }), nil
}

func Save(entry Entry) error {
	dir, err := entriesDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(entry, "", "  ")
	if err != nil {
		return err
	}
	staged, err := os.CreateTemp(dir, entry.Drive+".*.tmp")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(staged.Name()) }()
	_, err = staged.Write(append(data, '\n'))
	if closed := staged.Close(); err == nil {
		err = closed
	}
	if err != nil {
		return err
	}
	return os.Rename(staged.Name(), filepath.Join(dir, entry.Drive+".json"))
}

func Remove(name string) error {
	dir, err := entriesDir()
	if err != nil {
		return err
	}
	return os.Remove(filepath.Join(dir, name+".json"))
}
