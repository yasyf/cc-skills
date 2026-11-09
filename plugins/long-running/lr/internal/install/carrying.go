package install

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	carryingWait  = time.Hour
	carryingEvery = time.Minute
	updateLimit   = 5 * time.Minute
)

func (c Checks) Carrying(ctx context.Context, commit string, sleep func(time.Duration)) (string, error) {
	source, err := c.release()
	if err != nil {
		return "", err
	}
	found := githubRepo.FindStringSubmatch(source.URL)
	if found == nil {
		return "", fmt.Errorf("%s is not a GitHub repository, so no tag can be checked for %s", source.URL, commit)
	}
	deadline := c.Now().Add(carryingWait)
	for {
		version, err := c.newestCarrying(ctx, source, found[1], commit)
		if err != nil || version != "" {
			return version, err
		}
		if !c.Now().Before(deadline) {
			return "", fmt.Errorf("no %s tag carried %s within %s", pluginName, commit, carryingWait)
		}
		sleep(carryingEvery)
	}
}

func (c Checks) newestCarrying(ctx context.Context, source release, repo, commit string) (string, error) {
	newest, err := c.newestTag(ctx, source.URL)
	if err != nil || newest == "" {
		return "", err
	}
	var descriptor struct {
		Content string `json:"content"`
	}
	if err := c.read(ctx, fmt.Sprintf("repos/%s/contents/%s/bin/lr.binrun?ref=%s", repo, source.Path, tag(newest)), &descriptor); err != nil {
		return "", err
	}
	pin, err := pinnedLR(descriptor.Content)
	if err != nil {
		return "", fmt.Errorf("%s: %w", tag(newest), err)
	}
	var compare struct {
		Status string `json:"status"`
	}
	if err := c.read(ctx, fmt.Sprintf("repos/%s/compare/%s...lr--v%s", repo, commit, pin), &compare); err != nil {
		return "", err
	}
	if compare.Status != "ahead" && compare.Status != "identical" {
		return "", nil
	}
	return newest, nil
}

func pinnedLR(encoded string) (string, error) {
	raw, err := base64.StdEncoding.DecodeString(strings.ReplaceAll(encoded, "\n", ""))
	if err != nil {
		return "", err
	}
	_, body, _ := strings.Cut(string(raw), "\n")
	var descriptor struct {
		Version struct {
			Static string `json:"static"`
		} `json:"version"`
	}
	if err := json.Unmarshal([]byte(body), &descriptor); err != nil {
		return "", err
	}
	if descriptor.Version.Static == "" {
		return "", errors.New("lr.binrun pins no static lr version")
	}
	return descriptor.Version.Static, nil
}

func (c Checks) newestTag(ctx context.Context, url string) (string, error) {
	prefix := "refs/tags/" + tag("")
	listed, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"git", "ls-remote", "--tags", "--refs", url, prefix + "*"}, Timeout: readTimeout})
	if err != nil {
		return "", err
	}
	newest := ""
	for _, line := range strings.Split(strings.TrimSpace(listed.Stdout), "\n") {
		_, ref, _ := strings.Cut(line, "\t")
		version, tagged := strings.CutPrefix(ref, prefix)
		if tagged && (newest == "" || newer(version, newest)) {
			newest = version
		}
	}
	return newest, nil
}

func newer(version, than string) bool {
	return slices.Compare(versionKey(version), versionKey(than)) > 0
}

func (c Checks) UpdatePlugin(ctx context.Context) error {
	lock := filepath.Join(c.Claude, "plugins", ".update.lock")
	for _, argv := range [][]string{
		{"lockf", "-k", lock, "claude", "plugin", "marketplace", "update", "skills"},
		{"lockf", "-k", lock, "claude", "plugin", "update", registeredPlugin, "--scope", "user"},
	} {
		if _, err := c.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: updateLimit}); err != nil {
			return fmt.Errorf("%s: %s", strings.Join(argv[3:], " "), lastLine(err))
		}
	}
	return nil
}
