package launchd

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type launchctl struct {
	loaded bool
	verbs  []string
}

func (l *launchctl) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	l.verbs = append(l.verbs, c.Argv[1])
	if c.Argv[1] == "print" && !l.loaded {
		return shell.Result{Code: 113}, nil
	}
	return shell.Result{}, nil
}

func TestAnInstalledPlistRunsOnStablePathsAndNeverTheInstallingShellsPath(t *testing.T) {
	home := t.TempDir()
	t.Setenv("PATH", strings.Join([]string{
		filepath.Join(home, "repos", "worktrees", "lane", "tools", "bin"),
		filepath.Join(home, ".claude", "plugins", "cache", "cc-inbox", "cc-inbox", "0.49.71", "bin"),
		"/usr/bin",
	}, string(os.PathListSeparator)))
	agents := Launchd{Runner: &launchctl{}, Home: home, Claude: filepath.Join(home, ".claude"), UID: 501, Out: io.Discard}

	if err := agents.Install(context.Background(), Job{Label: "l", Env: [][2]string{{"OTHER", "kept"}}}); err != nil {
		t.Fatal(err)
	}

	body, err := os.ReadFile(filepath.Join(home, "Library", "LaunchAgents", "l.plist"))
	if err != nil {
		t.Fatal(err)
	}
	stable := strings.Join([]string{
		filepath.Join(home, ".claude", "long-running", "current", "bin"),
		filepath.Join(home, ".local", "bin"),
		filepath.Join(home, ".local", "share", "mise", "shims"),
		"/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
	}, string(os.PathListSeparator))
	for _, want := range []string{"<key>PATH</key>\n\t\t<string>" + stable + "</string>", "<key>OTHER</key>\n\t\t<string>kept</string>"} {
		if !strings.Contains(string(body), want) {
			t.Fatalf("%q not in\n%s", want, body)
		}
	}
}

func TestLoadLeavesALoadedJobRunningEvenWhenItsPlistChanged(t *testing.T) {
	home := t.TempDir()
	ctl := &launchctl{loaded: true}
	agents := Launchd{Runner: ctl, Home: home, Claude: filepath.Join(home, ".claude"), UID: 501, Out: io.Discard}

	if err := agents.Load(context.Background(), Job{Label: "creds", Arguments: []string{"/lr", "desk-creds", "keep"}}); err != nil {
		t.Fatal(err)
	}

	if strings.Join(ctl.verbs, " ") != "print" {
		t.Fatalf("launchctl %q; a loaded job whose start can raise a Touch ID prompt is never booted out or bootstrapped", ctl.verbs)
	}
	if _, err := os.Stat(filepath.Join(home, "Library", "LaunchAgents", "creds.plist")); !os.IsNotExist(err) {
		t.Fatalf("the plist was written: %v", err)
	}
}
