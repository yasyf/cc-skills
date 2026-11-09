package install

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

type followWorld struct {
	follow     Follow
	shell      *shelltest.Fake
	installed  []string
	rolledBack []string
	blocked    []string
}

func newFollowWorld(t *testing.T, slackStatus string) *followWorld {
	t.Helper()
	w := &followWorld{shell: &shelltest.Fake{}}
	checks := cachedChecks(t, nil)
	checks.Runner = w.shell
	for _, version := range []string{"0.7.1", "0.7.2"} {
		if err := os.MkdirAll(checks.dir(version), 0o750); err != nil {
			t.Fatal(err)
		}
	}
	if err := Promote(checks.Claude, checks.dir("0.7.1")); err != nil {
		t.Fatal(err)
	}
	w.shell.Reply("a\trefs/tags/long-running--v0.7.1\nb\trefs/tags/long-running--v0.7.2\n", "git", "ls-remote")
	w.shell.Reply("", "lockf")
	w.shell.Reply("", "brew")
	w.shell.Reply("v0.16.21\n", "gh", "release", "view")
	w.shell.Reply("cc-slack version 0.16.20\n", slackBinary, "--version")
	w.shell.Reply(slackStatus+"\nhttp: 127.0.0.1:1\n", slackBinary, "status")
	w.shell.Reply("", slackBinary, "stop")
	bin := t.TempDir()
	if err := os.WriteFile(filepath.Join(bin, slackBinary), []byte("0.16.20"), 0o700); err != nil { //nolint:gosec // the stub has to execute
		t.Fatal(err)
	}
	t.Setenv("PATH", bin)
	w.follow = Follow{
		Checks: checks,
		Install: func(_ context.Context, version string) error {
			w.installed = append(w.installed, version)
			return nil
		},
		Rollback: func(_ context.Context, version string) error {
			w.rolledBack = append(w.rolledBack, version)
			return Restore(checks.Claude, checks.dir(version))
		},
		Block: func(_ context.Context, subject, text string) error {
			w.blocked = append(w.blocked, subject+": "+text)
			return nil
		},
		State: t.TempDir(),
		Sleep: func(time.Duration) {},
	}
	return w
}

func (w *followWorld) slackBinaryHolds(t *testing.T) string {
	t.Helper()
	path, err := slackPath()
	if err != nil {
		t.Fatal(err)
	}
	read, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(read)
}

func TestFollowInstallsTheNewestTagAndEndsSoLaunchdRelaunchesOnIt(t *testing.T) {
	w := newFollowWorld(t, "daemon: running (0.16.21)")

	if err := w.follow.Run(context.Background()); err != nil {
		t.Fatal(err)
	}

	if !slices.Equal(w.installed, []string{"0.7.2"}) || len(w.rolledBack) != 0 || len(w.blocked) != 0 {
		t.Fatalf("installed %v, rolled back %v, blocked %v; want 0.7.2 alone", w.installed, w.rolledBack, w.blocked)
	}
}

func TestFollowRollsBackAPackThatNeverServesAndSkipsItUntilANewerTag(t *testing.T) {
	w := newFollowWorld(t, "daemon: running (0.16.21)")
	w.follow.Install = func(_ context.Context, version string) error {
		w.installed = append(w.installed, version)
		if err := Promote(w.follow.Checks.Claude, w.follow.Checks.dir(version)); err != nil {
			t.Fatal(err)
		}
		return errors.New("no successor within 15 minutes")
	}

	for range 2 {
		if installed, err := w.follow.pack(context.Background()); installed || err != nil {
			t.Fatalf("pack = %v, %v; want a rollback", installed, err)
		}
	}

	if got := Installed(w.follow.Checks.Claude); got != "0.7.1" {
		t.Fatalf("installed link = %s; want 0.7.1 after the rollback", got)
	}
	if !slices.Equal(w.installed, []string{"0.7.2"}) || !slices.Equal(w.rolledBack, []string{"0.7.1"}) {
		t.Fatalf("installed %v, rolled back %v; want one try at 0.7.2 rolled back to 0.7.1", w.installed, w.rolledBack)
	}
	if len(w.blocked) != 1 || !strings.Contains(w.blocked[0], "restarted the supervisor's launchd job") {
		t.Fatalf("blocked %v; want one blocker naming the restart", w.blocked)
	}
}

func TestFollowSkipsAPackWhoseInstallNeverMovedTheLinkWithoutARollback(t *testing.T) {
	w := newFollowWorld(t, "daemon: running (0.16.21)")
	w.follow.Install = func(context.Context, string) error {
		return &Refused{Version: "0.7.2", Faults: []string{"the 0.7.2 binary refuses this drive"}}
	}

	if _, err := w.follow.pack(context.Background()); err != nil {
		t.Fatal(err)
	}

	if len(w.rolledBack) != 0 || len(w.blocked) != 1 || !strings.Contains(w.blocked[0], "never moved") {
		t.Fatalf("rolled back %v, blocked %v; want no rollback and one blocker", w.rolledBack, w.blocked)
	}
}

type upgradingRunner struct {
	*shelltest.Fake
	binary string
}

func (u upgradingRunner) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if slices.Equal(c.Argv, []string{"brew", "upgrade", "--cask", slackBinary}) {
		if err := os.WriteFile(u.binary, []byte("0.16.21"), 0o600); err != nil {
			return shell.Result{}, err
		}
	}
	return u.Fake.Run(ctx, c)
}

func TestFollowPutsTheOldCcSlackBackWhenTheNewDaemonNeverServes(t *testing.T) {
	w := newFollowWorld(t, "daemon: running (0.16.20)")
	path, err := slackPath()
	if err != nil {
		t.Fatal(err)
	}
	w.follow.Checks.Runner = upgradingRunner{w.shell, path}

	if err := w.follow.slack(context.Background()); err != nil {
		t.Fatal(err)
	}

	if got := w.slackBinaryHolds(t); got != "0.16.20" {
		t.Fatalf("cc-slack binary holds %s; want 0.16.20 put back", got)
	}
	stopped := slices.ContainsFunc(w.shell.Calls(), func(c shell.Command) bool { return slices.Equal(c.Argv, []string{slackBinary, "stop"}) })
	if !stopped || len(w.blocked) != 1 || !strings.Contains(w.blocked[0], "rolled cc-slack 0.16.21 back to 0.16.20") {
		t.Fatalf("stopped %v, blocked %v; want the daemon restarted and one blocker", stopped, w.blocked)
	}
}

func TestFollowKeepsACcSlackWhoseDaemonServesTheNewRelease(t *testing.T) {
	w := newFollowWorld(t, "daemon: running (0.16.21)")
	path, err := slackPath()
	if err != nil {
		t.Fatal(err)
	}
	w.follow.Checks.Runner = upgradingRunner{w.shell, path}

	if err := w.follow.slack(context.Background()); err != nil {
		t.Fatal(err)
	}

	if got := w.slackBinaryHolds(t); got != "0.16.21" || len(w.blocked) != 0 {
		t.Fatalf("cc-slack binary holds %s, blocked %v; want 0.16.21 kept", got, w.blocked)
	}
}
