package install

import (
	"context"
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
		version, err := c.newestCarrying(ctx, source.URL, found[1], commit)
		if err != nil || version != "" {
			return version, err
		}
		if !c.Now().Before(deadline) {
			return "", fmt.Errorf("no %s tag carried %s within %s", pluginName, commit, carryingWait)
		}
		sleep(carryingEvery)
	}
}

func (c Checks) newestCarrying(ctx context.Context, url, repo, commit string) (string, error) {
	prefix := "refs/tags/" + tag("")
	listed, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"git", "ls-remote", "--tags", "--refs", url, prefix + "*"}, Timeout: readTimeout})
	if err != nil {
		return "", err
	}
	newest := ""
	for _, line := range strings.Split(strings.TrimSpace(listed.Stdout), "\n") {
		_, ref, _ := strings.Cut(line, "\t")
		version, tagged := strings.CutPrefix(ref, prefix)
		if tagged && (newest == "" || slices.Compare(versionKey(version), versionKey(newest)) > 0) {
			newest = version
		}
	}
	if newest == "" {
		return "", nil
	}
	var compare struct {
		Status string `json:"status"`
	}
	if err := c.read(ctx, fmt.Sprintf("repos/%s/compare/%s...%s", repo, commit, tag(newest)), &compare); err != nil {
		return "", err
	}
	if compare.Status != "ahead" && compare.Status != "identical" {
		return "", nil
	}
	return newest, nil
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
