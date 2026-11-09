package install

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	followEvery = time.Minute
	followRedOK = "install on merge with self-rollback, his pick: a merged change reaches his Mac within minutes"
	slackRepo   = "yasyf/cc-slack"
	slackBinary = "cc-slack"
	slackProof  = 10 * time.Minute
	slackPoll   = 30 * time.Second
)

type Follow struct {
	Checks   Checks
	Install  func(ctx context.Context, version string) error
	Rollback func(ctx context.Context, version string) error
	Block    func(ctx context.Context, subject, text string) error
	State    string
	Sleep    func(time.Duration)
}

func (f Follow) Run(ctx context.Context) error {
	for {
		if err := f.slack(ctx); err != nil {
			return err
		}
		installed, err := f.pack(ctx)
		if err != nil || installed {
			return err
		}
		f.Sleep(followEvery)
	}
}

func (f Follow) pack(ctx context.Context) (bool, error) {
	source, err := f.Checks.release()
	if err != nil {
		return false, err
	}
	newest, err := f.Checks.newestTag(ctx, source.URL)
	if err != nil {
		return false, err
	}
	running := Installed(f.Checks.Claude)
	subject := "pack " + newest
	if newest == "" || !newer(newest, running) || f.skipped(subject) {
		return false, nil
	}
	if err := f.Checks.UpdatePlugin(ctx); err != nil {
		return false, err
	}
	err = f.Install(ctx, newest)
	if err == nil {
		return true, nil
	}
	if Installed(f.Checks.Claude) == running {
		return false, f.skip(ctx, subject, fmt.Sprintf("lr install follow skips long-running %s until a newer tag: %v. The install link never moved, so %s still serves.", newest, err, running))
	}
	if rolled := f.Rollback(ctx, running); rolled != nil {
		return false, f.skip(ctx, subject, fmt.Sprintf("lr install follow: long-running %s failed (%v), and the rollback to %s failed too: %v", newest, err, running, rolled))
	}
	return false, f.skip(ctx, subject, fmt.Sprintf("lr install follow rolled long-running %s back to %s: %v. It pointed the install link at %s, moved the Sprite pins back and restarted the supervisor's launchd job, and skips %s until a newer tag.", newest, running, err, running, newest))
}

func (f Follow) slack(ctx context.Context) error {
	released, err := f.Checks.Runner.Run(ctx, shell.Command{Argv: []string{"gh", "release", "view", "-R", slackRepo, "--json", "tagName", "--jq", ".tagName"}, Timeout: readTimeout})
	if err != nil {
		return err
	}
	latest := strings.TrimPrefix(strings.TrimSpace(released.Stdout), "v")
	running, err := f.slackVersion(ctx)
	if err != nil {
		return err
	}
	subject := "cc-slack " + latest
	if !newer(latest, running) || f.skipped(subject) {
		return nil
	}
	binary, err := slackPath()
	if err != nil {
		return err
	}
	saved := filepath.Join(f.State, slackBinary+"-"+running)
	if err := copyFile(binary, saved); err != nil {
		return err
	}
	if err := f.upgradeSlack(ctx); err != nil {
		return f.skip(ctx, subject, fmt.Sprintf("lr install follow skips cc-slack %s until a newer release: the upgrade failed (%v); cc-slack %s still runs.", latest, err, running))
	}
	serving := f.slackServes(ctx, latest)
	if serving == "" {
		return os.Remove(saved)
	}
	if err := f.restoreSlack(ctx, saved, running); err != nil {
		return f.skip(ctx, subject, fmt.Sprintf("lr install follow: cc-slack %s never served within %s (%s), and the rollback to %s failed: %v", latest, slackProof, serving, running, err))
	}
	return f.skip(ctx, subject, fmt.Sprintf("lr install follow rolled cc-slack %s back to %s: its daemon never reported %s within %s (%s). It put the %s binary back and restarted the cc-slack daemon with cc-slack stop, and skips %s until a newer release.", latest, running, latest, slackProof, serving, running, latest))
}

func (f Follow) upgradeSlack(ctx context.Context) error {
	lock := filepath.Join(f.Checks.Claude, "plugins", ".update.lock")
	for _, argv := range [][]string{
		{"brew", "update", "--quiet"},
		{"brew", "upgrade", "--cask", slackBinary},
		{"lockf", "-k", lock, "claude", "plugin", "marketplace", "update", slackBinary},
		{"lockf", "-k", lock, "claude", "plugin", "update", slackBinary + "@" + slackBinary, "--scope", "user"},
	} {
		if _, err := f.Checks.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: updateLimit}); err != nil {
			return fmt.Errorf("%s: %s", strings.Join(argv, " "), lastLine(err))
		}
	}
	return nil
}

func (f Follow) slackServes(ctx context.Context, version string) string {
	want := fmt.Sprintf("daemon: running (%s)", version)
	said := ""
	for range int(slackProof / slackPoll) {
		status, err := f.Checks.Runner.Run(ctx, shell.Command{Argv: []string{slackBinary, "status"}, Timeout: readTimeout})
		said = strings.TrimSpace(strings.SplitN(status.Stdout, "\n", 2)[0])
		if err != nil {
			said = lastLine(err)
		}
		if said == want {
			return ""
		}
		f.Sleep(slackPoll)
	}
	return cmp.Or(said, "cc-slack status printed nothing")
}

func (f Follow) restoreSlack(ctx context.Context, saved, version string) error {
	binary, err := slackPath()
	if err != nil {
		return err
	}
	if err := copyFile(saved, binary); err != nil {
		return err
	}
	if _, err := f.Checks.Runner.Run(ctx, shell.Command{Argv: []string{slackBinary, "stop"}, Timeout: readTimeout}); err != nil {
		return err
	}
	if said := f.slackServes(ctx, version); said != "" {
		return errors.New(said)
	}
	return nil
}

func (f Follow) slackVersion(ctx context.Context) (string, error) {
	said, err := f.Checks.Runner.Run(ctx, shell.Command{Argv: []string{slackBinary, "--version"}, Timeout: readTimeout})
	if err != nil {
		return "", err
	}
	words := strings.Fields(said.Stdout)
	if len(words) == 0 {
		return "", errors.New("cc-slack --version printed nothing")
	}
	return words[len(words)-1], nil
}

func slackPath() (string, error) {
	found, err := exec.LookPath(slackBinary)
	if err != nil {
		return "", err
	}
	return filepath.EvalSymlinks(found)
}

func (f Follow) skipsFile() string { return filepath.Join(f.State, "skips") }

func (f Follow) skipped(subject string) bool {
	read, err := os.ReadFile(f.skipsFile())
	return err == nil && slices.Contains(strings.Split(string(read), "\n"), subject)
}

func (f Follow) skip(ctx context.Context, subject, text string) error {
	if err := os.MkdirAll(f.State, 0o750); err != nil {
		return err
	}
	file, err := os.OpenFile(f.skipsFile(), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	_, written := fmt.Fprintln(file, subject)
	return errors.Join(written, file.Close(), f.Block(ctx, subject, text))
}

func copyFile(from, to string) error {
	info, err := os.Stat(from)
	if err != nil {
		return err
	}
	data, err := os.ReadFile(from)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(to), 0o750); err != nil {
		return err
	}
	staged := to + ".staged"
	if err := os.WriteFile(staged, data, info.Mode().Perm()); err != nil {
		return err
	}
	return os.Rename(staged, to)
}
