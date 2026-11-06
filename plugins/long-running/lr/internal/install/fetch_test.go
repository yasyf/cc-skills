package install

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func TestFetchInstallsANamedTagBehindTheNewestAsItsReleaseHoldsIt(t *testing.T) {
	origin, plugins := t.TempDir(), t.TempDir()
	git(t, origin, "init", "-q")
	root := filepath.Join(origin, "plugins", "long-running")
	write(t, filepath.Join(root, ".claude-plugin", "plugin.json"), `{"name":"long-running"}`)
	write(t, filepath.Join(root, "a.txt"), "first\n")
	if err := os.Symlink("a.txt", filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(origin, "plugins", "other", "b.txt"), "not this plugin\n")
	git(t, origin, "add", ".")
	git(t, origin, "commit", "-q", "-m", "first")
	git(t, origin, "tag", "long-running--v1.0.1")
	write(t, filepath.Join(root, "a.txt"), "second\n")
	git(t, origin, "commit", "-q", "-am", "second")
	git(t, origin, "tag", "long-running--v1.0.2")
	write(t, filepath.Join(plugins, "marketplaces", "skills", ".claude-plugin", "marketplace.json"),
		`{"plugins":[{"name":"long-running","source":{"url":`+strconv.Quote(origin)+`,"path":"plugins/long-running"}}]}`)
	checks := Checks{Runner: shell.Exec{}, Installs: filepath.Join(plugins, "cache", "skills", "long-running"), Claude: t.TempDir()}

	if err := checks.Fetch(context.Background(), "1.0.1"); err != nil {
		t.Fatal(err)
	}

	if got, err := os.ReadFile(filepath.Join(checks.dir("1.0.1"), "a.txt")); err != nil || string(got) != "first\n" {
		t.Fatalf("a.txt %q %v", got, err)
	}
	if target, err := os.Readlink(filepath.Join(checks.dir("1.0.1"), "link")); err != nil || target != "a.txt" {
		t.Fatalf("link %q %v", target, err)
	}
	if faults := checks.ReleaseFaults(context.Background(), "1.0.1"); len(faults) != 0 {
		t.Fatalf("faults %v", faults)
	}
	if _, err := os.Stat(checks.dir("1.0.2")); !os.IsNotExist(err) {
		t.Fatalf("the newest tag was installed too: %v", err)
	}
	if left, err := filepath.Glob(filepath.Join(checks.Claude, pluginName, "fetch-*")); err != nil || len(left) != 0 {
		t.Fatalf("work left behind %v %v", left, err)
	}
}

func TestATagThatWillNotFetchRefusesTheInstall(t *testing.T) {
	plugins := t.TempDir()
	write(t, filepath.Join(plugins, "marketplaces", "skills", ".claude-plugin", "marketplace.json"),
		`{"plugins":[{"name":"long-running","source":{"url":`+strconv.Quote(t.TempDir())+`,"path":"plugins/long-running"}}]}`)
	checks := Checks{Runner: shell.Exec{}, Installs: filepath.Join(plugins, "cache", "skills", "long-running"), Claude: t.TempDir()}

	err := checks.Fetch(context.Background(), "9.9.9")

	if err == nil {
		t.Fatal("a missing tag fetched")
	}
	if _, statErr := os.Stat(checks.dir("9.9.9")); !os.IsNotExist(statErr) {
		t.Fatalf("a refused fetch left an install: %v", statErr)
	}
}
