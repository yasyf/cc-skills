package config

import (
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"os"
	"slices"
	"strings"
)

const MoverLanding = "landing"

var Movers = []string{"lanes", "shadow", MoverLanding, "grading", "items"}

type Config struct {
	Repo          string
	Drive         string
	Checkout      string
	Author        string
	Label         string
	Movers        []string
	OrcaRun       string
	RemoteBox     string
	GoLanes       string
	Trunk         string
	QueueScript   string
	QueueKey      string
	AlertChannels string
	Parent        string
}

func (c Config) GoLaneNames() []string {
	var names []string
	for name := range strings.SplitSeq(c.GoLanes, ",") {
		if name = strings.TrimSpace(name); name != "" {
			names = append(names, name)
		}
	}
	return names
}

func Load(path string, warn io.Writer) (Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil || raw == nil {
		return Config{}, fmt.Errorf("%s: must be a JSON object", path)
	}
	loaded := Config{Trunk: "main"}
	required := map[string]*string{
		"repo": &loaded.Repo, "drive": &loaded.Drive, "checkout": &loaded.Checkout,
		"author": &loaded.Author, "label": &loaded.Label,
	}
	optional := map[string]*string{
		"orca_run": &loaded.OrcaRun, "remote_box": &loaded.RemoteBox, "go_lanes": &loaded.GoLanes,
		"trunk": &loaded.Trunk, "queue_script": &loaded.QueueScript, "queue_key": &loaded.QueueKey,
		"alert_channels": &loaded.AlertChannels, "parent": &loaded.Parent,
	}
	var unknown []string
	for _, name := range slices.Sorted(maps.Keys(raw)) {
		if required[name] == nil && optional[name] == nil && name != "movers" {
			unknown = append(unknown, name)
		}
	}
	if len(unknown) > 0 {
		fmt.Fprintf(warn, "%s: ignoring %s, which this release does not read; workers and desks register themselves with their first heartbeat and never appear here\n", path, strings.Join(unknown, ", "))
	}
	var missing []string
	for _, name := range slices.Sorted(maps.Keys(required)) {
		if _, found := raw[name]; !found {
			missing = append(missing, name)
		}
	}
	if _, found := raw["movers"]; !found {
		missing = append(missing, "movers")
		slices.Sort(missing)
	}
	if len(missing) > 0 {
		return Config{}, fmt.Errorf("%s: missing %s", path, strings.Join(missing, ", "))
	}
	if loaded.Movers, err = movers(path, raw["movers"], warn); err != nil {
		return Config{}, err
	}
	for name, field := range required {
		if err := json.Unmarshal(raw[name], field); err != nil || strings.TrimSpace(*field) == "" {
			return Config{}, fmt.Errorf("%s: %s must be non-empty text", path, name)
		}
	}
	for name, field := range optional {
		if value, found := raw[name]; found {
			if err := json.Unmarshal(value, field); err != nil {
				return Config{}, fmt.Errorf("%s: %s must be text", path, name)
			}
		}
	}
	if slices.Contains(loaded.Movers, MoverLanding) && strings.TrimSpace(loaded.QueueScript) == "" {
		return Config{}, fmt.Errorf("%s: the landing mover needs queue_script, the repository's queue script relative to the checkout", path)
	}
	return loaded, nil
}

func movers(path string, raw json.RawMessage, warn io.Writer) ([]string, error) {
	var named []string
	if err := json.Unmarshal(raw, &named); err != nil || named == nil {
		return nil, fmt.Errorf("%s: movers must be a list of mover names, got %s", path, raw)
	}
	var runs, newer []string
	for _, mover := range named {
		if slices.Contains(Movers, mover) {
			runs = append(runs, mover)
		} else {
			newer = append(newer, mover)
		}
	}
	if len(newer) > 0 {
		fmt.Fprintf(warn, "%s: ignoring movers %s, which this release does not run\n", path, strings.Join(newer, ", "))
	}
	if len(runs) == 0 {
		return nil, fmt.Errorf("%s: movers must name at least one of %s", path, strings.Join(Movers, ", "))
	}
	return runs, nil
}
