package codexguard

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/BurntSushi/toml"
)

const (
	matcher       = "^Bash$"
	eventKey      = "PreToolUse"
	eventLabel    = "pre_tool_use"
	timeoutSecond = 10
)

func checkLine(bin string) string {
	return fmt.Sprintf("%q codex-guard check", bin)
}

func Install(home, line string) error {
	hooksPath := filepath.Join(home, "hooks.json")
	group, err := addHook(hooksPath, line)
	if err != nil {
		return err
	}
	return trust(filepath.Join(home, "config.toml"), fmt.Sprintf("%s:%s:%d:0", hooksPath, eventLabel, group), trustHash(line))
}

func addHook(path, line string) (int, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	var root map[string]any
	if err := json.Unmarshal(raw, &root); err != nil {
		return 0, fmt.Errorf("%s: %w", path, err)
	}
	hooks := root["hooks"].(map[string]any)
	groups, _ := hooks[eventKey].([]any)
	for i, group := range groups {
		for _, hook := range group.(map[string]any)["hooks"].([]any) {
			if hook.(map[string]any)["command"] == line {
				return i, nil
			}
		}
	}
	hooks[eventKey] = append(groups, map[string]any{"matcher": matcher, "hooks": []any{handler(line)}})
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(root); err != nil {
		return 0, err
	}
	return len(groups), replaceFile(path, out.Bytes())
}

func handler(line string) map[string]any {
	return map[string]any{"type": "command", "command": line, "timeout": timeoutSecond}
}

func trustHash(line string) string {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	withAsync := handler(line)
	withAsync["async"] = false
	if err := encoder.Encode(map[string]any{"event_name": eventLabel, "matcher": matcher, "hooks": []any{withAsync}}); err != nil {
		panic(err)
	}
	sum := sha256.Sum256(bytes.TrimSpace(out.Bytes()))
	return "sha256:" + hex.EncodeToString(sum[:])
}

func trust(path, key, hash string) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var config struct {
		Hooks struct {
			State map[string]struct {
				TrustedHash string `toml:"trusted_hash"`
			} `toml:"state"`
		} `toml:"hooks"`
	}
	if _, err := toml.Decode(string(raw), &config); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	entry, found := config.Hooks.State[key]
	if found && entry.TrustedHash == hash {
		return nil
	}
	if found {
		return replaceFile(path, []byte(strings.Replace(string(raw), entry.TrustedHash, hash, 1)))
	}
	return replaceFile(path, append(raw, fmt.Appendf(nil, "\n[hooks.state.%q]\ntrusted_hash = %q\n", key, hash)...))
}

func replaceFile(path string, content []byte) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(temp.Name()) }()
	if _, err := temp.Write(content); err != nil {
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(temp.Name(), info.Mode()); err != nil {
		return err
	}
	return os.Rename(temp.Name(), path)
}
