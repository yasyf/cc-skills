package install

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func fetchTag(ctx context.Context, runner shell.Runner, releases, url, tagName string) error {
	if _, err := os.Stat(releases); err != nil {
		if _, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "init", "-q", "--bare", releases}, Timeout: readTimeout}); err != nil {
			return err
		}
	}
	_, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", releases, "fetch", "-q", "--depth", "1", url, fmt.Sprintf("+refs/tags/%s:refs/tags/%s", tagName, tagName)}, Timeout: readTimeout})
	return err
}

func (c Checks) Fetch(ctx context.Context, version string) error {
	source, err := c.release()
	if err != nil {
		return err
	}
	releases := filepath.Join(c.Claude, pluginName, "releases.git")
	if err := fetchTag(ctx, c.Runner, releases, source.URL, tag(version)); err != nil {
		return fmt.Errorf("the release tag %s did not fetch from %s: %s", tag(version), source.URL, lastLine(err))
	}
	work, err := os.MkdirTemp(filepath.Join(c.Claude, pluginName), "fetch-")
	if err != nil {
		return err
	}
	defer func() { _ = os.RemoveAll(work) }()
	archive, tree := filepath.Join(work, "pack.tar"), filepath.Join(work, version)
	if _, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", releases, "archive", "--format=tar", "-o", archive, tag(version) + ":" + source.Path}, Timeout: readTimeout}); err != nil {
		return err
	}
	if err := os.Mkdir(tree, 0o750); err != nil {
		return err
	}
	if _, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"tar", "-xf", archive, "-C", tree}, Timeout: readTimeout}); err != nil {
		return err
	}
	if err := os.MkdirAll(c.Installs, 0o750); err != nil {
		return err
	}
	return os.Rename(tree, c.dir(version))
}
