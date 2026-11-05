package codexguard

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/BurntSushi/toml"
)

const (
	line      = `"/home/lane/.local/bin/lr" codex-guard check`
	startJSON = `{"hooks":{"PreToolUse":[{"hooks":[{"type":"command","command":"orca && hook","timeout":10}]},{"matcher":"^Bash$","hooks":[{"type":"command","command":"guard","timeout":10}]}],"Stop":[]}}`
	startTOML = "model = \"m\"\n\n[hooks.state]\n"
)

func TestTrustHashMatchesWhatCodexPersists(t *testing.T) {
	got := trustHash(`"/home/lane/.local/bin/capt-search-guard"`)
	if want := "sha256:0ebed415dbf92829941469fad10477687434d0ac3c91b51f89911c373e6f48b7"; got != want {
		t.Fatalf("trustHash = %s, want %s", got, want)
	}
}

func home(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	for name, content := range map[string]string{"hooks.json": startJSON, "config.toml": startTOML} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func TestInstallAddsTheHookAndItsTrustOnce(t *testing.T) {
	dir := home(t)
	for range 2 {
		if err := Install(dir, line); err != nil {
			t.Fatal(err)
		}
	}
	raw, err := os.ReadFile(filepath.Join(dir, "hooks.json"))
	if err != nil {
		t.Fatal(err)
	}
	var written struct {
		Hooks struct {
			PreToolUse []struct {
				Matcher string `json:"matcher"`
				Hooks   []struct {
					Command string `json:"command"`
				} `json:"hooks"`
			} `json:"PreToolUse"`
		} `json:"hooks"`
	}
	if err := json.Unmarshal(raw, &written); err != nil {
		t.Fatal(err)
	}
	groups := written.Hooks.PreToolUse
	if len(groups) != 3 || groups[0].Hooks[0].Command != "orca && hook" || groups[2].Matcher != matcher || groups[2].Hooks[0].Command != line {
		t.Fatalf("hooks.json = %s", raw)
	}
	config, err := os.ReadFile(filepath.Join(dir, "config.toml"))
	if err != nil {
		t.Fatal(err)
	}
	var state struct {
		Model string `toml:"model"`
		Hooks struct {
			State map[string]struct {
				TrustedHash string `toml:"trusted_hash"`
			} `toml:"state"`
		} `toml:"hooks"`
	}
	if _, err := toml.Decode(string(config), &state); err != nil {
		t.Fatalf("config.toml no longer parses: %v\n%s", err, config)
	}
	key := filepath.Join(dir, "hooks.json") + ":pre_tool_use:2:0"
	if state.Model != "m" || state.Hooks.State[key].TrustedHash != trustHash(line) || strings.Count(string(config), key) != 1 {
		t.Fatalf("config.toml = %s", config)
	}
}

func TestInstallCorrectsAStaleTrust(t *testing.T) {
	dir := home(t)
	path := filepath.Join(dir, "config.toml")
	key := filepath.Join(dir, "hooks.json") + ":pre_tool_use:2:0"
	stale := startTOML + "\n[hooks.state.\"" + key + "\"]\ntrusted_hash = \"sha256:old\"\n"
	if err := os.WriteFile(path, []byte(stale), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := Install(dir, line); err != nil {
		t.Fatal(err)
	}
	config, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(config), "sha256:old") || !strings.Contains(string(config), trustHash(line)) {
		t.Fatalf("config.toml = %s", config)
	}
}
