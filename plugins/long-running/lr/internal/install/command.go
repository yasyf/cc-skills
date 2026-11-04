package install

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	updateTimeout = 10 * time.Minute
	pluginRootEnv = "BINRUN_PLUGIN_ROOT"
	shimFile      = "bin-shim.sh"
)

var shimLine = regexp.MustCompile(`(?m)^\s*(\S+)\)\s+exec "\$ROOT/bin/lr" (.+) "\$@"`)

type options struct {
	ciRedOK string
}

func Command(a *app.App) *cobra.Command {
	var opts options
	cmd := &cobra.Command{
		Use:   "install VERSION",
		Short: "Refuse before the swap, move the install link, and wait for the supervisor's lease",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			generation, err := install(cmd.Context(), a, args[0], opts)
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "lr install: %s serves on generation %d\n", args[0], generation)
			return err
		},
	}
	cmd.Flags().StringVar(&opts.ciRedOK, "ci-red-ok", "", "why this version must run though main's CI has not passed on it")
	cmd.AddCommand(&cobra.Command{
		Use:   "check",
		Short: "Refuse when this binary could not run this drive: its config, its credentials and every shim's verb",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if faults := check(cmd, a); len(faults) > 0 {
				return errors.New(strings.Join(faults, "; "))
			}
			return nil
		},
	})
	return cmd
}

func Installs(claude string) (string, error) {
	if plugin := os.Getenv(pluginRootEnv); plugin != "" {
		return filepath.Dir(plugin), nil
	}
	resolved, err := filepath.EvalSymlinks(link(claude))
	if err != nil {
		return "", fmt.Errorf("run lr through the plugin's bin/lr, or install a version first: %w", err)
	}
	return filepath.Dir(resolved), nil
}

func install(ctx context.Context, a *app.App, version string, opts options) (int64, error) {
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return 0, err
	}
	claude, err := drive.ConfigDir()
	if err != nil {
		return 0, err
	}
	dir, err := Installs(claude)
	if err != nil {
		return 0, err
	}
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		return 0, err
	}
	defer func() { _ = s.Close() }()
	checks := Checks{Runner: a.Runner, CCX: a.CCX, Installs: dir, Claude: claude}
	if _, err := os.Stat(checks.dir(version)); err != nil {
		if _, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"claude", "plugin", "update", pluginName + "@" + filepath.Base(filepath.Dir(dir))}, Timeout: updateTimeout}); err != nil {
			return 0, err
		}
	}
	post := bus.Bus{Drive: cfg.Drive, Lane: lane}
	configPath, err := filepath.Abs(a.ConfigPath)
	if err != nil {
		return 0, err
	}
	generation, err := Run(ctx, s, cfg.Drive, version, Steps{
		Preflight: func(ctx context.Context) []string {
			return preflight(ctx, checks, configPath, version, opts)
		},
		Swap: func(_ context.Context, version string) error { return Promote(claude, checks.dir(version)) },
		Post: func(ctx context.Context, to []string, body bus.Body) error {
			_, err := post.Post(ctx, to, body)
			return err
		},
		Now:   a.Now,
		Sleep: time.Sleep,
	})
	if err != nil {
		return 0, err
	}
	if err := orca.MoveSpritePins(ctx, s, claude, filepath.Join(state, "runner.json"), a.Stderr); err != nil && !errors.Is(err, fs.ErrNotExist) {
		a.Log.Warn("the Sprite pins did not all move", "error", err)
	}
	if err := orca.RollHost(ctx, a.Runner, s, filepath.Join(state, "runner.json"), a.Now, a.Stderr); err != nil && !errors.Is(err, fs.ErrNotExist) {
		a.Log.Warn("the desk host did not move to the Sprite pins", "error", err)
	}
	return generation, nil
}

func preflight(ctx context.Context, checks Checks, configPath, version string, opts options) []string {
	faults := checks.ReleaseFaults(ctx, version)
	if len(faults) > 0 {
		return faults
	}
	if opts.ciRedOK == "" {
		if red := checks.CIFault(ctx, version); red != "" {
			faults = append(faults, red+"; install once it passes, or pass --ci-red-ok with the reason this version must run now")
		}
	}
	if fault := checks.TargetFault(ctx, version, configPath); fault != "" {
		faults = append(faults, fault)
	}
	return faults
}

func check(cmd *cobra.Command, a *app.App) []string {
	ctx := cmd.Context()
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return []string{err.Error()}
	}
	for _, drifted := range lanesFaults(ctx, a.Runner, cfg, a.ConfigPath) {
		_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "lr install check: "+drifted)
	}
	var faults []string
	if edited := checkoutFault(ctx, a.Runner, cfg); edited != "" {
		faults = append(faults, edited)
	}
	if _, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"gh", "auth", "token"}, Timeout: readTimeout}); err != nil {
		faults = append(faults, "gh holds no token, and the drive cannot read GitHub without it: "+lastLine(err))
	}
	if slices.Contains(cfg.Movers, config.MoverLanding) && cfg.QueueKey != "" {
		result, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", cfg.QueueKey}, Timeout: readTimeout})
		if err != nil || strings.TrimSpace(result.Stdout) == "" {
			faults = append(faults, "queue_key prints no key, and the landing mover cannot reach its queue without one")
		}
	}
	plugin := os.Getenv(pluginRootEnv)
	if plugin == "" {
		return faults
	}
	faults = append(faults, cciFaults(ctx, a.Runner, plugin, state)...)
	script, err := os.ReadFile(filepath.Join(plugin, "scripts", shimFile))
	if err != nil {
		return append(faults, err.Error())
	}
	return append(faults, ShimFaults(cmd.Root(), string(script))...)
}

func ShimFaults(root *cobra.Command, script string) []string {
	matches := shimLine.FindAllStringSubmatch(script, -1)
	faults := make([]string, 0, len(matches))
	for _, match := range matches {
		words := strings.Fields(match[2])
		found, rest, err := root.Find(words)
		if err != nil || len(rest) > 0 || found == root {
			faults = append(faults, fmt.Sprintf("the %s shim runs lr %s, which this binary lacks", match[1], match[2]))
		}
	}
	return faults
}

func lanesFaults(ctx context.Context, runner shell.Runner, cfg config.Config, source string) []string {
	names := cfg.GoLaneNames()
	unregistered := make([]string, 0, len(names))
	for _, name := range names {
		if !registered(ctx, runner, cfg.Drive, name) {
			unregistered = append(unregistered, name)
		}
	}
	if len(unregistered) == 0 {
		return nil
	}
	return []string{fmt.Sprintf("%s names %s, which never registered on drive %s; start each lane or remove it from the config", source, strings.Join(unregistered, ", "), cfg.Drive)}
}

func registered(ctx context.Context, runner shell.Runner, driveName, name string) bool {
	who, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "who", "--drive", driveName, "--lane", name, "--all", "--json"}, Timeout: readTimeout})
	if err == nil && strings.TrimSpace(who.Stdout) != "" {
		return true
	}
	posted, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "tail", "--drive", driveName, "--lane", name, "--since", "0", "--limit", "1", "--json"}, Timeout: readTimeout})
	return err == nil && strings.TrimSpace(posted.Stdout) != ""
}

func checkoutFault(ctx context.Context, runner shell.Runner, cfg config.Config) string {
	if !slices.Contains(cfg.Movers, config.MoverLanding) {
		return ""
	}
	status, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", cfg.Checkout, "status", "--porcelain", "--untracked-files=no"}, Timeout: readTimeout})
	if err != nil {
		return ""
	}
	lines := strings.Split(status.Stdout, "\n")
	edited := make([]string, 0, len(lines))
	for _, line := range lines {
		if len(line) > 3 {
			edited = append(edited, line[3:])
		}
	}
	if len(edited) == 0 {
		return ""
	}
	upstream, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", cfg.Checkout, "diff", "--name-only", "HEAD", "origin/" + cfg.Trunk}, Timeout: readTimeout})
	if err != nil {
		return ""
	}
	moving := strings.Split(upstream.Stdout, "\n")
	edited = slices.DeleteFunc(edited, func(path string) bool { return !slices.Contains(moving, path) })
	if len(edited) == 0 {
		return ""
	}
	return fmt.Sprintf("%s has local edits to %s, so no pass can fast-forward it to origin/%s", cfg.Checkout, strings.Join(edited, ", "), cfg.Trunk)
}
