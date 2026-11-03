package install

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"golang.org/x/mod/semver"
)

var releaseVersion = regexp.MustCompile(`/download/v([^/]+)/`)

type dependency struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

func cciFaults(ctx context.Context, runner shell.Runner, plugin, state string) []string {
	floor, err := cciFloor(plugin)
	if err != nil {
		return []string{err.Error()}
	}
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "--version"}, Timeout: readTimeout})
	if err != nil {
		return []string{"cci --version failed: " + lastLine(err)}
	}
	var faults []string
	if have := strings.TrimSpace(result.Stdout); semver.Compare("v"+have, "v"+floor) < 0 {
		faults = append(faults, fmt.Sprintf("this machine's cci is %s, and the pack needs cc-inbox %s or later; run cci upgrade", have, floor))
	}
	url, err := spriteCCIURL(state)
	if err != nil {
		return append(faults, err.Error())
	}
	if url == "" {
		return faults
	}
	if found := releaseVersion.FindStringSubmatch(url); found == nil || semver.Compare("v"+found[1], "v"+floor) < 0 {
		faults = append(faults, fmt.Sprintf("runner.json's WORKER_CCI_URL %s is no cc-inbox %s or later release, so every Sprite worker would install a cci this pack outruns", url, floor))
	}
	return faults
}

func cciFloor(plugin string) (string, error) {
	data, err := os.ReadFile(filepath.Join(plugin, ".claude-plugin", "plugin.json"))
	if err != nil {
		return "", err
	}
	var manifest struct {
		Dependencies []dependency `json:"dependencies"`
	}
	if err := json.Unmarshal(data, &manifest); err != nil {
		return "", fmt.Errorf("plugin.json: %w", err)
	}
	i := slices.IndexFunc(manifest.Dependencies, func(d dependency) bool { return d.Name == "cc-inbox" })
	if i < 0 {
		return "", errors.New("plugin.json names no cc-inbox dependency, so nothing says which cci the pack needs")
	}
	return strings.TrimPrefix(manifest.Dependencies[i].Version, ">="), nil
}

func spriteCCIURL(state string) (string, error) {
	data, err := os.ReadFile(filepath.Join(state, "runner.json"))
	if errors.Is(err, fs.ErrNotExist) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	var runner struct {
		Orca struct {
			Sprite struct {
				Env map[string]string `json:"env"`
			} `json:"sprite"`
		} `json:"orca"`
	}
	if err := json.Unmarshal(data, &runner); err != nil {
		return "", fmt.Errorf("runner.json: %w", err)
	}
	return runner.Orca.Sprite.Env["WORKER_CCI_URL"], nil
}
