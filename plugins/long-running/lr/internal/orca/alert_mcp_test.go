package orca

import (
	"path/filepath"
	"strings"
	"testing"
)

func (h *harness) installDatadog(domain string) {
	h.t.Helper()
	config := filepath.Join(h.root, "claude-config")
	install := filepath.Join(h.root, "datadog-install")
	h.env["CLAUDE_CONFIG_DIR"] = config
	h.writeFile(filepath.Join(config, "plugins", "installed_plugins.json"), `{"version":2,"plugins":{"datadog@claude-plugins-official":[{"installPath":"`+install+`"}]}}`)
	h.writeFile(filepath.Join(install, ".claude-plugin", "plugin.json"), `{"mcpServers":"./servers.json"}`)
	h.writeFile(filepath.Join(install, "servers.json"), `{"mcpServers":{"mcp":{"type":"http","url":"https://${DD_MCP_DOMAIN:-mcp.datadoghq.com}/v1/mcp?plugin_version=9","headers":{"DD_API_KEY":"${DD_API_KEY:-}"}}}}`)
	if domain != "" {
		h.env["DD_MCP_DOMAIN"] = domain
	}
}

func (h *harness) terminalCommand() string {
	h.t.Helper()
	return flag(h.orcaCalls("terminal create")[0], "--command")
}

func TestAnAlertLaneMountsSentryAndTheInstalledDatadogWithItsLogin(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.installDatadog("")
	h.launchedAs(modelIncidentOpus, "xhigh")
	command := h.terminalCommand()
	for _, want := range []string{
		`"sentry":{"type":"http","url":"https://mcp.sentry.dev/mcp"}`,
		`"plugin:datadog:mcp":`,
		`"url":"https://mcp.datadoghq.com/v1/mcp?plugin_version=9"`,
		`"DD_API_KEY":"${DD_API_KEY:-}"`,
	} {
		if !strings.Contains(command, want) {
			t.Fatalf("missing %s in %s", want, command)
		}
	}
}

func TestAnAlertLaneFollowsTheDatadogDomainTheMacSets(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.installDatadog("mcp.datadoghq.eu")
	h.launchedAs(modelIncidentOpus, "xhigh")
	if command := h.terminalCommand(); !strings.Contains(command, `"url":"https://mcp.datadoghq.eu/v1/mcp?plugin_version=9"`) {
		t.Fatal(command)
	}
}

func TestAnAlertLaneOnAMachineWithoutDatadogStillStartsWithSentry(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.env["CLAUDE_CONFIG_DIR"] = filepath.Join(h.root, "no-plugins")
	h.launchedAs(modelIncidentOpus, "xhigh")
	command := h.terminalCommand()
	if !strings.Contains(command, `"sentry":`) || strings.Contains(command, datadogServer) {
		t.Fatal(command)
	}
}

func TestAnOrdinaryLaneMountsNoTelemetryServer(t *testing.T) {
	h := newHarness(t)
	h.healthyClaude()
	h.installDatadog("")
	h.launchedAs("opus", "high")
	if command := h.terminalCommand(); strings.Contains(command, "mcp.sentry.dev") || strings.Contains(command, datadogServer) {
		t.Fatal(command)
	}
}
