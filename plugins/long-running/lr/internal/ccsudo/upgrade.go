package ccsudo

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"golang.org/x/mod/semver"
)

const (
	cli           = "cc-sudo"
	cask          = "yasyf/tap/cc-sudo"
	signedBy      = `anchor apple generic and certificate leaf[subject.OU] = "SXKCTF23Q2" and identifier "cc-sudo"`
	idleLimit     = 5 * time.Minute
	readWait      = 30 * time.Second
	brewWait      = 5 * time.Minute
	tapWait       = 5 * time.Minute
	typeInstall   = "he must type `sudo cc-sudo install` at this Mac"
	mustBeAtMac   = "he must be at this Mac with the console unlocked: the tap prompts there, and the install reads his Secure Enclave key, which a locked console refuses"
	verifierFails = "`cc-sudo doctor` shows no verifier version, so " + typeInstall
)

var (
	verifierAt = regexp.MustCompile(`(?m)^\[ok\] verifier: .* at (\S+)$`)
	skewedAt   = regexp.MustCompile(`(?m)^\[FAIL\] verifier: version skew: CLI \S+ vs verifier (\S+) `)
	unlocked   = regexp.MustCompile(`"IOConsoleLocked" = No\b`)
	hidIdle    = regexp.MustCompile(`"HIDIdleTime" = (\d+)`)
)

type verifier struct {
	version string
	skewed  bool
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   cli,
		Short: "Keep cc-sudo run working across a cc-sudo release",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return cmd.Help()
		},
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "upgrade",
		Short: "Move the root verifier to the tap's newest cc-sudo on one Touch ID tap while CLI and verifier still match, then upgrade the cask",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			version, err := upgrade(cmd.Context(), a.Runner)
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "cc-sudo CLI and verifier are at %s\n", version)
			return err
		},
	})
	return cmd
}

func upgrade(ctx context.Context, runner shell.Runner) (string, error) {
	current, err := readVerifier(ctx, runner)
	if err != nil {
		return "", err
	}
	if current.skewed {
		return "", errors.New("version skew refuses `cc-sudo run`, so no tap can carry the install; " + typeInstall)
	}
	target, err := newest(ctx, runner)
	if err != nil {
		return "", err
	}
	if target == current.version {
		return target, nil
	}
	if semver.Compare("v"+target, "v"+current.version) < 0 {
		return "", fmt.Errorf("the tap names %s, older than the verifier's %s, so no tap was asked", target, current.version)
	}
	if err := atTheMac(ctx, runner); err != nil {
		return "", err
	}
	dir, err := os.MkdirTemp("", "cc-sudo-upgrade-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(dir)
	binary, err := staged(ctx, runner, target, dir)
	if err != nil {
		return "", err
	}
	_, tapErr := runner.Run(ctx, shell.Command{Argv: []string{cli, "run", "--", binary, "install"}, Timeout: tapWait})
	moved, err := readVerifier(ctx, runner)
	if err != nil {
		return "", errors.Join(tapErr, err)
	}
	if moved.version != target {
		return "", errors.Join(fmt.Errorf("the verifier stays at %s and the cask did not move", moved.version), tapErr)
	}
	if err := brew(ctx, runner, "upgrade", "--cask", cask); err != nil {
		return "", fmt.Errorf("the verifier is at %s but the cask did not move, so `cc-sudo run` is refused until `brew upgrade --cask %s` runs: %w", target, cask, err)
	}
	final, err := readVerifier(ctx, runner)
	if err != nil {
		return "", err
	}
	if final.skewed || final.version != target {
		return "", fmt.Errorf("after the cask upgrade the verifier reads %s and skew is %t, not %s and false", final.version, final.skewed, target)
	}
	if tapErr != nil {
		return "", fmt.Errorf("CLI and verifier are at %s but the install stopped part way, so %s: %w", target, typeInstall, tapErr)
	}
	return target, nil
}

func readVerifier(ctx context.Context, runner shell.Runner) (verifier, error) {
	result, err := runner.Run(ctx, shell.Command{Argv: []string{cli, "doctor"}, Timeout: readWait})
	if at := verifierAt.FindStringSubmatch(result.Stdout); at != nil {
		return verifier{version: at[1]}, nil
	}
	if at := skewedAt.FindStringSubmatch(result.Stdout); at != nil {
		return verifier{version: at[1], skewed: true}, nil
	}
	return verifier{}, errors.Join(errors.New(verifierFails), err)
}

func newest(ctx context.Context, runner shell.Runner) (string, error) {
	if err := brew(ctx, runner, "update", "--quiet"); err != nil {
		return "", err
	}
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"brew", "info", "--json=v2", "--cask", cask}, Timeout: brewWait})
	if err != nil {
		return "", err
	}
	var info struct {
		Casks []struct {
			Version string `json:"version"`
		} `json:"casks"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &info); err != nil {
		return "", fmt.Errorf("brew info --cask %s: %w", cask, err)
	}
	if len(info.Casks) != 1 {
		return "", fmt.Errorf("brew info --cask %s lists %d casks", cask, len(info.Casks))
	}
	return info.Casks[0].Version, nil
}

func atTheMac(ctx context.Context, runner shell.Runner) error {
	console, err := runner.Run(ctx, shell.Command{Argv: []string{"ioreg", "-n", "Root", "-d1"}, Timeout: readWait})
	if err != nil {
		return err
	}
	if !unlocked.MatchString(console.Stdout) {
		return errors.New(mustBeAtMac)
	}
	input, err := runner.Run(ctx, shell.Command{Argv: []string{"ioreg", "-c", "IOHIDSystem", "-d", "4"}, Timeout: readWait})
	if err != nil {
		return err
	}
	idle := hidIdle.FindStringSubmatch(input.Stdout)
	if idle == nil {
		return errors.New(mustBeAtMac)
	}
	nanos, err := strconv.ParseInt(idle[1], 10, 64)
	if err != nil {
		return err
	}
	if time.Duration(nanos) >= idleLimit {
		return errors.New(mustBeAtMac)
	}
	return nil
}

func staged(ctx context.Context, runner shell.Runner, version, dir string) (string, error) {
	if err := brew(ctx, runner, "fetch", "--cask", cask); err != nil {
		return "", err
	}
	archive, err := runner.Run(ctx, shell.Command{Argv: []string{"brew", "--cache", "--cask", cask}, Timeout: readWait})
	if err != nil {
		return "", err
	}
	if _, err := runner.Run(ctx, shell.Command{Argv: []string{"ditto", "-x", "-k", "--noqtn", strings.TrimSpace(archive.Stdout), dir}, Timeout: readWait}); err != nil {
		return "", err
	}
	binary := filepath.Join(dir, cli)
	if _, err := runner.Run(ctx, shell.Command{Argv: []string{"/usr/bin/codesign", "--verify", "--strict", "-R=" + signedBy, "--", binary}, Timeout: readWait}); err != nil {
		return "", fmt.Errorf("the fetched binary is not cc-sudo's signed release, so no tap was asked: %w", err)
	}
	stamped, err := runner.Run(ctx, shell.Command{Argv: []string{binary, "--version"}, Timeout: readWait})
	if err != nil {
		return "", err
	}
	if got := strings.TrimSpace(stamped.Stdout); got != version {
		return "", fmt.Errorf("the fetched binary reads %s, not the tap's %s, so no tap was asked", got, version)
	}
	return binary, nil
}

func brew(ctx context.Context, runner shell.Runner, args ...string) error {
	_, err := runner.Run(ctx, shell.Command{Argv: append([]string{"brew"}, args...), Timeout: brewWait, Idempotent: true})
	return err
}
