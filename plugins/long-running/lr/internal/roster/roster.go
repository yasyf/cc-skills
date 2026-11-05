package roster

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

const Fresh = 15 * time.Minute

type saved struct {
	Run     string            `json:"run"`
	At      time.Time         `json:"at"`
	Workers []json.RawMessage `json:"workers"`
}

func Path(state, run string) string {
	return filepath.Join(state, "long-running", "orca-roster", run+".json")
}

func Write(path, run string, at time.Time, workers []json.RawMessage) error {
	data, err := json.Marshal(saved{Run: run, At: at, Workers: workers})
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	staged := path + "." + strconv.Itoa(os.Getpid())
	if err := os.WriteFile(staged, data, 0o600); err != nil {
		return err
	}
	return os.Rename(staged, path)
}

func Read(path string, now time.Time, workers any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("the supervisor's worker roster is unread: %w", err)
	}
	var read struct {
		Run     string          `json:"run"`
		At      time.Time       `json:"at"`
		Workers json.RawMessage `json:"workers"`
	}
	if err := json.Unmarshal(data, &read); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	if age := now.Sub(read.At); age >= Fresh {
		return fmt.Errorf("the supervisor's worker roster of %s is stale: its read began %s ago, and one older than %s is not used", read.Run, age.Round(time.Second), Fresh)
	}
	return json.Unmarshal(read.Workers, workers)
}
