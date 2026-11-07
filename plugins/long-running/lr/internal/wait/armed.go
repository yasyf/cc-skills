package wait

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"syscall"
)

type Armed struct {
	PID      int    `json:"pid"`
	Terminal string `json:"terminal"`
	Output   string `json:"output"`
}

func armedDir() (string, error) {
	state := os.Getenv("XDG_STATE_HOME")
	if state == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		state = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(state, "lr", "waits"), nil
}

func recordArmed(armed Armed) error {
	dir, err := armedDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	body, err := json.Marshal(armed)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, strconv.Itoa(armed.PID)+".json"), body, 0o600)
}

func disarm(pid int) error {
	dir, err := armedDir()
	if err != nil {
		return err
	}
	if err := os.Remove(filepath.Join(dir, strconv.Itoa(pid)+".json")); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}

// ArmedFor reads the waits armed for terminal whose process still runs, so a
// wait counts on a host whose ps shows no process environment. A record whose
// process is gone is removed.
func ArmedFor(terminal string) ([]Armed, error) {
	dir, err := armedDir()
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(dir)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var live []Armed
	for _, entry := range entries {
		if filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		record := filepath.Join(dir, entry.Name())
		body, err := os.ReadFile(record)
		if err != nil {
			return nil, err
		}
		var armed Armed
		if err := json.Unmarshal(body, &armed); err != nil {
			return nil, fmt.Errorf("armed wait record %s: %w", record, err)
		}
		if err := syscall.Kill(armed.PID, 0); err != nil && !errors.Is(err, syscall.EPERM) {
			if err := disarm(armed.PID); err != nil {
				return nil, err
			}
			continue
		}
		if armed.Terminal == terminal {
			live = append(live, armed)
		}
	}
	return live, nil
}
