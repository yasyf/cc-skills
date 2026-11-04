package drive

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strconv"
)

type Pin struct {
	Name     string   `json:"name"`
	Delivers string   `json:"delivers"`
	Words    []string `json:"words"`
	PRs      []int    `json:"prs,omitempty"`
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

func Live() ([]Entry, error) {
	found, err := entries()
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(found, func(entry Entry) bool { return len(entry.Sessions) == 0 }), nil
}

func Named(name string) (Entry, error) {
	live, err := Live()
	if err != nil {
		return Entry{}, err
	}
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

func Holding(session string) (Entry, error) {
	found, err := entries()
	if err != nil {
		return Entry{}, err
	}
	if i := slices.IndexFunc(found, func(entry Entry) bool { return slices.Contains(entry.Sessions, session) }); i >= 0 {
		return found[i], nil
	}
	return Entry{}, fmt.Errorf("no drive registry entry holds session %q", session)
}

// HoldingWindow finds the drive of the Claude window with this pid, by the session
// that window runs now. A resumed window's MCP servers keep the session id they
// booted with, so a server asks the window rather than its own environment. It
// only reads: the order of an entry's sessions names the coordinator's and stays
// as it is.
func HoldingWindow(pid int) (Entry, error) {
	dir, err := ConfigDir()
	if err != nil {
		return Entry{}, err
	}
	data, err := os.ReadFile(filepath.Join(dir, "sessions", strconv.Itoa(pid)+".json"))
	if err != nil {
		return Entry{}, fmt.Errorf("read the session of window %d: %w", pid, err)
	}
	var window struct {
		SessionID string `json:"sessionId"`
	}
	if err := json.Unmarshal(data, &window); err != nil {
		return Entry{}, fmt.Errorf("decode the session of window %d: %w", pid, err)
	}
	return Holding(window.SessionID)
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

func DashboardURL(state string) (string, error) {
	path := filepath.Join(state, "dashboard.json")
	body, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	var config struct {
		Host string `json:"host"`
		Port int    `json:"port"`
	}
	if err := json.Unmarshal(body, &config); err != nil {
		return "", fmt.Errorf("%s: %w", path, err)
	}
	return fmt.Sprintf("http://%s:%d/", config.Host, config.Port), nil
}
