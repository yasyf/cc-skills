package grading

import (
	"context"
	"errors"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	pluginRootEnv   = "BINRUN_PLUGIN_ROOT"
	moveOffHomeVerb = "move-off-home"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "grade", Short: "Grade one drive head, from the job a desk wrote to DIR"}
	cmd.AddCommand(
		gradeVerb(a, GraderFinder, "One codex finder pass over one drive head", grader.Finder),
		gradeVerb(a, GraderFidelity, "One fidelity grade of one drive head", grader.Fidelity),
		moveOffHomeCommand(),
	)
	return cmd
}

func gradeVerb(a *app.App, name, short string, grade func(context.Context, string, grader.Deps) error) *cobra.Command {
	return &cobra.Command{
		Use:   name + " DIR",
		Short: short,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := os.Setenv("CCX_CALLER", name); err != nil {
				return err
			}
			spec, err := jobNames(args[0])
			if err != nil {
				return err
			}
			if spec.Sprite != "" {
				if err := onLocalDisk(spec.Checkout, spriteLocalDisk); err != nil {
					return err
				}
				if err := startMoveOffHome(spec.Checkout, spriteLocalDisk); err != nil {
					return err
				}
			}
			deps, err := liveDeps(a)
			if err != nil {
				return err
			}
			ctx, stop := signal.NotifyContext(cmd.Context(), syscall.SIGTERM, syscall.SIGHUP, syscall.SIGINT)
			defer stop()
			return grade(ctx, args[0], deps)
		},
	}
}

func moveOffHomeCommand() *cobra.Command {
	return &cobra.Command{
		Use:    moveOffHomeVerb + " CHECKOUT",
		Short:  "Move a Sprite's worktree and the tools a pass reads from its network home to its local disk",
		Args:   cobra.ExactArgs(1),
		Hidden: true,
		RunE: func(_ *cobra.Command, args []string) error {
			home, err := os.UserHomeDir()
			if err != nil {
				return err
			}
			return moveOffHome(args[0], home, spriteLocalDisk)
		},
	}
}

func pluginRoot() (string, error) {
	plugin := os.Getenv(pluginRootEnv)
	if plugin == "" {
		return "", errors.New("lr runs its graders through the plugin's bin/lr, which sets " + pluginRootEnv)
	}
	return plugin, nil
}

func claudeDir() (string, error) {
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude"), nil
}

func liveDeps(a *app.App) (grader.Deps, error) {
	plugin, err := pluginRoot()
	if err != nil {
		return grader.Deps{}, err
	}
	claude, err := claudeDir()
	if err != nil {
		return grader.Deps{}, err
	}
	return grader.Deps{
		Runner: a.Runner,
		Post:   client.Post,
		Sleep:  time.Sleep,
		Now:    a.Now,
		Log:    a.Log,
		Bin:    filepath.Join(plugin, "bin"),
		Config: claude,
	}, nil
}

func NewLivePool(a *app.App, runnerPath string, s *store.Store) (*JobPool, error) {
	plugin, err := pluginRoot()
	if err != nil {
		return nil, err
	}
	claude, err := claudeDir()
	if err != nil {
		return nil, err
	}
	runner, err := config.LoadRunner(runnerPath)
	if err != nil {
		return nil, err
	}
	return NewJobPool(runner, PoolDeps{
		Runner:  a.Runner,
		Spawn:   Detached,
		Workers: s.Workers,
		Cursors: s,
		Ledger:  s.LedgerRows,
		Picks:   s.CheckedRules,
		Now:     a.Now,
		Log:     a.Log,
		Self:    filepath.Join(plugin, "bin", "lr"),
		Scripts: filepath.Join(plugin, "skills", "long-running", "scripts"),
		Config:  claude,
	})
}
