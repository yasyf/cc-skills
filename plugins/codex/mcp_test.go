package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

var stubCodexListsMCPServers = stubCodex(`[{"name":"slack","enabled":true},`+
	`{"name":"node_repl","enabled":true},`+
	`{"name":"computer-use","enabled":false}]`, stubCodexReplyBody)

func TestDefaultDispatchBansMCP(t *testing.T) {
	stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexListsMCPServers, "ping")
	if code != 0 {
		t.Fatalf("default dispatch exit %d\nstderr: %s", code, stderr)
	}
	sdir := laneDir(t, stdout)
	argv := cmdArgv(t, sdir)
	if contains(argv, "mcp_servers={}") {
		t.Fatalf("default argv carries the no-op table override:\n%v", argv)
	}
	for _, want := range []string{
		"mcp_servers.slack.enabled=false",
		"mcp_servers.node_repl.enabled=false",
		"mcp_servers.computer-use.enabled=false",
	} {
		if !contains(argv, want) {
			t.Fatalf("default argv missing %q:\n%v", want, argv)
		}
	}
	if got := flagValue(argv, "--disable"); got != "apps" {
		t.Fatalf("default argv --disable = %q, want apps:\n%v", got, argv)
	}
	if dev := developerInstructions(t, sdir); strings.Contains(dev, "## MCP") {
		t.Fatalf("default developer_instructions carries an MCP contract:\n%s", dev)
	}
}

func TestMCPMountsOnlyTheNamedServers(t *testing.T) {
	stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexListsMCPServers, "--mcp", "slack", "ping")
	if code != 0 {
		t.Fatalf("--mcp slack exit %d\nstderr: %s", code, stderr)
	}
	sdir := laneDir(t, stdout)
	argv := cmdArgv(t, sdir)
	if contains(argv, "mcp_servers={}") {
		t.Fatalf("--mcp argv still carries the blanket ban:\n%v", argv)
	}
	for _, want := range []string{
		"mcp_servers.node_repl.enabled=false",
		"mcp_servers.computer-use.enabled=false",
	} {
		if !contains(argv, want) {
			t.Fatalf("--mcp argv missing %q:\n%v", want, argv)
		}
	}
	if contains(argv, "mcp_servers.slack.enabled=false") {
		t.Fatalf("--mcp argv disabled the requested server:\n%v", argv)
	}
	if got := flagValue(argv, "--disable"); got != "apps" {
		t.Fatalf("--mcp argv --disable = %q, want apps:\n%v", got, argv)
	}

	dev := developerInstructions(t, sdir)
	block := strings.Index(dev, "## MCP")
	if block < 0 {
		t.Fatalf("developer_instructions has no MCP contract:\n%s", dev)
	}
	if !strings.Contains(dev[block:], "slack") {
		t.Fatalf("MCP contract does not name slack:\n%s", dev[block:])
	}
}

// A user-level codex plugin's .mcp.json is merged at config load, so a plugin
// declaring a bad transport used to kill every dispatch before the prompt was read.
func TestDispatchDisablesUserPlugins(t *testing.T) {
	runs := mustTempDir(t)
	recorded := filepath.Join(mustTempDir(t), "mcp-argv")
	stub := "#!/bin/sh\n[ \"$1\" = mcp ] && { printf '%s\\n' \"$@\" > " + recorded + "; printf '%s' '[]'; exit 0; }\n" +
		stubCodexReplyBody
	stdout, stderr, code := askRun(t, runs, stub, "ping")
	if code != 0 {
		t.Fatalf("dispatch exit %d\nstderr: %s", code, stderr)
	}
	listing, err := os.ReadFile(recorded) //nolint:gosec // reads the argv this test's own stub recorded
	if err != nil {
		t.Fatalf("read recorded listing argv: %v", err)
	}
	if !contains(strings.Split(strings.TrimRight(string(listing), "\n"), "\n"), "plugins") {
		t.Fatalf("`codex mcp list` ran with plugins loaded:\n%s", listing)
	}
	argv := cmdArgv(t, laneDir(t, stdout))
	for i, arg := range argv {
		if arg == "--disable" && argv[i+1] == "plugins" {
			return
		}
	}
	t.Fatalf("dispatch argv does not disable user plugins:\n%v", argv)
}

func TestMCPContractLandsAfterTheLaneContract(t *testing.T) {
	stdout, stderr, code := askRun(t, mustTempDir(t), stubCodexListsMCPServers,
		"--lane", "review", "--mcp", "slack", "ping")
	if code != 0 {
		t.Fatalf("--lane review --mcp slack exit %d\nstderr: %s", code, stderr)
	}
	dev := developerInstructions(t, laneDir(t, stdout))
	if !strings.HasPrefix(dev, readAgentsMd()) {
		t.Fatalf("developer_instructions no longer opens on the AGENTS.md baseline:\n%s", dev)
	}
	if !strings.HasSuffix(dev, mcpContract([]string{"slack"})) {
		t.Fatalf("MCP contract is not the trailing block:\n%s", dev)
	}
}

func TestMCPUnknownServerRefusesBeforeMinting(t *testing.T) {
	runs := mustTempDir(t)
	_, stderr, code := askRun(t, runs, stubCodexListsMCPServers, "--mcp", "nope", "ping")
	if code != 2 {
		t.Fatalf("unknown --mcp server exit %d, want 2\nstderr: %s", code, stderr)
	}
	for _, name := range []string{"slack", "node_repl", "computer-use"} {
		if !strings.Contains(stderr, name) {
			t.Fatalf("stderr does not name configured server %q:\n%s", name, stderr)
		}
	}
	mustBeEmpty(t, runs, "a refused --mcp server")
}

func TestMCPDisabledServerRefusesBeforeMinting(t *testing.T) {
	runs := mustTempDir(t)
	_, stderr, code := askRun(t, runs, stubCodexListsMCPServers, "--mcp", "computer-use", "ping")
	if code != 2 {
		t.Fatalf("disabled --mcp server exit %d, want 2\nstderr: %s", code, stderr)
	}
	if !strings.Contains(stderr, "config.toml") {
		t.Fatalf("refusal does not point at ~/.codex/config.toml:\n%s", stderr)
	}
	mustBeEmpty(t, runs, "a disabled --mcp server")
}

func TestMCPEmptyValueRefusesBeforeMinting(t *testing.T) {
	runs := mustTempDir(t)
	_, stderr, code := askRun(t, runs, stubCodexListsMCPServers, "--mcp", "", "ping")
	if code != 2 {
		t.Fatalf("empty --mcp exit %d, want 2\nstderr: %s", code, stderr)
	}
	if !strings.Contains(stderr, "comma-separated server list") {
		t.Fatalf("stderr = %q, want the empty --mcp refusal", stderr)
	}
	mustBeEmpty(t, runs, "an empty --mcp")
}

func TestMCPListingHangRefusesBeforeMinting(t *testing.T) {
	runs := mustTempDir(t)
	hang := "#!/bin/sh\n[ \"$1\" = mcp ] && { sleep 60; exit 0; }\n" + stubCodexReplyBody
	start := time.Now()
	_, stderr, code := askRun(t, runs, hang, "ping")
	if code != 2 {
		t.Fatalf("hung listing exit %d, want 2\nstderr: %s", code, stderr)
	}
	if !strings.Contains(stderr, "timed out") {
		t.Fatalf("stderr = %q, want the listing timeout refusal", stderr)
	}
	if elapsed := time.Since(start); elapsed > mcpListTimeout+10*time.Second {
		t.Fatalf("hung listing held the caller %s, past the %s bound", elapsed, mcpListTimeout)
	}
	mustBeEmpty(t, runs, "a hung MCP listing")
}
