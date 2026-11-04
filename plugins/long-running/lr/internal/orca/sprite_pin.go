package orca

import (
	"bytes"
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"golang.org/x/mod/semver"
)

const pinFetchTimeout = 5 * time.Minute

var (
	checksumFiles  = []string{"checksums.txt", "SHA256SUMS.txt"}
	errUnpublished = errors.New("no published release")
)

func MoveSpritePins(ctx context.Context, s *store.Store, claude, runner string, out io.Writer) error {
	file, err := readRunnerFile(runner)
	if err != nil {
		return err
	}
	if file.Orca.Sprite == nil {
		return nil
	}
	running, err := macPlugins(claude)
	if err != nil {
		return err
	}
	tools := slices.Clone(file.Orca.Sprite.Tools)
	listed := len(tools)
	if cci, pinned := cciPin(file.Orca.Sprite.Env); pinned {
		tools = append(tools, cci)
	}
	moved := false
	var failed []string
	for i, tool := range tools {
		plugin := tool.Follows
		if tool.Kind == "plugin" {
			plugin = tool.Name
		}
		version := running[plugin]
		if plugin == "" || version == tool.Version {
			continue
		}
		if version == "" {
			failed = append(failed, fmt.Sprintf("%s follows %s, which this Mac has not installed", tool.Name, plugin))
			continue
		}
		versions, err := versionsToTry(claude, plugin, version, tool.Version)
		if err != nil {
			return err
		}
		stays := ""
		for _, candidate := range versions {
			why, err := s.BadTool(tool.Name, candidate)
			if err != nil {
				return err
			}
			if why != "" {
				stays = cmp.Or(stays, fmt.Sprintf("%s is marked bad, since %s", candidate, why))
				continue
			}
			next, err := pinAt(ctx, tool, candidate)
			if errors.Is(err, errUnpublished) {
				stays = cmp.Or(stays, candidate+" has no published release yet")
				continue
			}
			if err != nil {
				failed = append(failed, err.Error())
				stays = ""
				break
			}
			fmt.Fprintf(out, "lr sprites pin: %s moved from %s to %s\n", tool.Name, tool.Version, candidate)
			tools[i], moved, stays = next, true, ""
			break
		}
		if stays != "" {
			fmt.Fprintf(out, "lr sprites pin: %s stays at %s: %s\n", tool.Name, tool.Version, stays)
		}
	}
	if moved {
		if err := writePins(runner, tools[:listed], tools[listed:]); err != nil {
			return err
		}
	}
	if len(failed) > 0 {
		return refuse("lr sprites pin: %s", strings.Join(failed, "; "))
	}
	return nil
}

func versionsToTry(claude, plugin, running, pinned string) ([]string, error) {
	name, marketplace, _ := strings.Cut(plugin, "@")
	entries, err := os.ReadDir(filepath.Join(claude, "plugins", "cache", marketplace, name))
	if err != nil {
		return nil, err
	}
	var older []string
	for _, entry := range entries {
		version := "v" + entry.Name()
		if semver.IsValid(version) && semver.Compare(version, "v"+running) < 0 && semver.Compare(version, "v"+pinned) > 0 {
			older = append(older, entry.Name())
		}
	}
	slices.SortFunc(older, func(a, b string) int { return semver.Compare("v"+b, "v"+a) })
	return append([]string{running}, older...), nil
}

func macPlugins(claude string) (map[string]string, error) {
	raw, err := os.ReadFile(filepath.Join(claude, "plugins", "installed_plugins.json"))
	if err != nil {
		return nil, err
	}
	var installed struct {
		Plugins map[string][]struct {
			Scope   string `json:"scope"`
			Version string `json:"version"`
		} `json:"plugins"`
	}
	if err := json.Unmarshal(raw, &installed); err != nil {
		return nil, err
	}
	running := map[string]string{}
	for id, entries := range installed.Plugins {
		for _, entry := range entries {
			if entry.Scope == "user" {
				running[id] = entry.Version
			}
		}
	}
	return running, nil
}

func pinAt(ctx context.Context, tool spriteTool, version string) (spriteTool, error) {
	url := strings.ReplaceAll(tool.URL, tool.Version, version)
	if url == tool.URL {
		return tool, fmt.Errorf("%s: %s does not name version %s, so it cannot move to %s", tool.Name, tool.URL, tool.Version, version)
	}
	dir, asset := path.Split(url)
	want, err := publishedDigest(ctx, dir, asset)
	if err != nil {
		return tool, err
	}
	response, err := fetch(ctx, url)
	if err != nil {
		return tool, err
	}
	defer func() { _ = response.Body.Close() }()
	digest := sha256.New()
	if _, err := io.Copy(digest, response.Body); err != nil {
		return tool, fmt.Errorf("%s: %w", url, err)
	}
	got := hex.EncodeToString(digest.Sum(nil))
	if got != want {
		return tool, fmt.Errorf("%s has SHA-256 %s and its release says %s, so %s stays at %s", url, got, want, tool.Name, tool.Version)
	}
	tool.URL, tool.SHA256, tool.Version = url, got, version
	return tool, nil
}

func publishedDigest(ctx context.Context, dir, asset string) (string, error) {
	for _, name := range checksumFiles {
		response, err := fetch(ctx, dir+name)
		if errors.Is(err, errUnpublished) {
			continue
		}
		if err != nil {
			return "", err
		}
		raw, err := io.ReadAll(response.Body)
		_ = response.Body.Close()
		if err != nil {
			return "", fmt.Errorf("%s%s: %w", dir, name, err)
		}
		for line := range strings.Lines(string(raw)) {
			if words := strings.Fields(line); len(words) == 2 && strings.TrimPrefix(words[1], "./") == asset {
				return words[0], nil
			}
		}
		return "", fmt.Errorf("%s%s names no %s", dir, name, asset)
	}
	return "", errUnpublished
}

func fetch(ctx context.Context, url string) (*http.Response, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	response, err := (&http.Client{Timeout: pinFetchTimeout}).Do(request)
	if err != nil {
		return nil, err
	}
	if response.StatusCode == http.StatusOK {
		return response, nil
	}
	_ = response.Body.Close()
	if response.StatusCode == http.StatusNotFound {
		return nil, errUnpublished
	}
	return nil, fmt.Errorf("%s: %s", url, response.Status)
}

func cciPin(env map[string]string) (spriteTool, bool) {
	version := cciVersion.FindStringSubmatch(env[cciURLEnv])
	if version == nil {
		return spriteTool{}, false
	}
	return spriteTool{Kind: "binary", Name: "cci", URL: env[cciURLEnv], SHA256: env[cciSHAEnv], Version: version[1], Follows: "cc-inbox@cc-inbox"}, true
}

func writePins(runner string, tools, cci []spriteTool) error {
	raw, err := os.ReadFile(runner)
	if err != nil {
		return err
	}
	info, err := os.Stat(runner)
	if err != nil {
		return err
	}
	var whole map[string]any
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&whole); err != nil {
		return err
	}
	sprite := whole["orca"].(map[string]any)["sprite"].(map[string]any)
	sprite["tools"] = tools
	for _, pin := range cci {
		env := sprite["env"].(map[string]any)
		env[cciURLEnv], env[cciSHAEnv] = pin.URL, pin.SHA256
	}
	var body bytes.Buffer
	encoder := json.NewEncoder(&body)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(whole); err != nil {
		return err
	}
	staged, err := os.CreateTemp(filepath.Dir(runner), filepath.Base(runner)+".*.new")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(staged.Name()) }()
	if _, err := staged.Write(body.Bytes()); err != nil {
		return err
	}
	if err := staged.Chmod(info.Mode()); err != nil {
		return err
	}
	if err := staged.Close(); err != nil {
		return err
	}
	return os.Rename(staged.Name(), runner)
}
