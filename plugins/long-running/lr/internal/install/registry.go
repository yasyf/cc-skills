package install

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
)

const registeredPlugin = pluginName + "@skills"

func (c Checks) RegistryFault(version string) string {
	raw, err := os.ReadFile(filepath.Join(c.Claude, "plugins", "installed_plugins.json"))
	if err != nil {
		return fmt.Sprintf("Claude Code's plugin registry is unreadable (%v), so no hook of %s would load", err, version)
	}
	var registry struct {
		Plugins map[string][]struct {
			InstallPath string `json:"installPath"`
		} `json:"plugins"`
	}
	if err := json.Unmarshal(raw, &registry); err != nil {
		return fmt.Sprintf("Claude Code's plugin registry does not parse (%v), so no hook of %s would load", err, version)
	}
	for _, entry := range registry.Plugins[registeredPlugin] {
		if slices.Compare(versionKey(filepath.Base(entry.InstallPath)), versionKey(version)) >= 0 {
			return ""
		}
	}
	return fmt.Sprintf("Claude Code's plugin registry loads %s's hooks from an older version than %s, so lr would move while every session's hooks stay behind; run `claude plugin marketplace update skills` and `claude plugin update %s`, then install again", registeredPlugin, version, registeredPlugin)
}
