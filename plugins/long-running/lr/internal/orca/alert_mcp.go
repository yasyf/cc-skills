package orca

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
)

const (
	datadogPlugin = "datadog@claude-plugins-official"
	datadogServer = "plugin:datadog:mcp"
	datadogListed = "mcp"
)

var urlDefault = regexp.MustCompile(`\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}`)

// alertTelemetryMCP is the --mcp-config value that mounts Sentry and Datadog on
// a strict-MCP Claude lane through the Mac's existing logins. Claude Code keys a
// server's login on its name, url and headers, so Datadog copies the installed
// plugin's entry with its url's defaults filled in and its headers untouched.
func alertTelemetryMCP(env func(string) string) (string, error) {
	servers := map[string]any{"sentry": map[string]any{"type": "http", "url": "https://mcp.sentry.dev/mcp"}}
	datadog, err := installedDatadog(env)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	if err == nil {
		servers[datadogServer] = datadog
	}
	config, err := json.Marshal(map[string]any{"mcpServers": servers})
	return string(config), err
}

func installedDatadog(env func(string) string) (map[string]any, error) {
	dir := env("CLAUDE_CONFIG_DIR")
	if dir == "" {
		dir = filepath.Join(env("HOME"), ".claude")
	}
	var installed struct {
		Plugins map[string][]struct {
			InstallPath string `json:"installPath"`
		} `json:"plugins"`
	}
	if err := readJSON(filepath.Join(dir, "plugins", "installed_plugins.json"), &installed); err != nil {
		return nil, err
	}
	installs := installed.Plugins[datadogPlugin]
	if len(installs) == 0 {
		return nil, os.ErrNotExist
	}
	root := installs[0].InstallPath
	var manifest struct {
		MCPServers string `json:"mcpServers"`
	}
	if err := readJSON(filepath.Join(root, ".claude-plugin", "plugin.json"), &manifest); err != nil {
		return nil, err
	}
	var listed struct {
		MCPServers map[string]map[string]any `json:"mcpServers"`
	}
	if err := readJSON(filepath.Join(root, manifest.MCPServers), &listed); err != nil {
		return nil, err
	}
	server, ok := listed.MCPServers[datadogListed]
	if !ok {
		return nil, fmt.Errorf("%s lists no %s server", datadogPlugin, datadogListed)
	}
	url, ok := server["url"].(string)
	if !ok {
		return nil, fmt.Errorf("%s's %s server has no url", datadogPlugin, datadogListed)
	}
	server["url"] = urlDefault.ReplaceAllStringFunc(url, func(placeholder string) string {
		parts := urlDefault.FindStringSubmatch(placeholder)
		if value := env(parts[1]); value != "" {
			return value
		}
		return parts[2]
	})
	return server, nil
}

func readJSON(path string, into any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(raw, into); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return nil
}
