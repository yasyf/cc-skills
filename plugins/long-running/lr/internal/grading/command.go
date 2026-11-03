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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
)

const pluginRootEnv = "BINRUN_PLUGIN_ROOT"

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "grade", Short: "Grade one drive head, from the job a desk wrote to DIR"}
	cmd.AddCommand(
		gradeVerb(a, GraderFinder, "One codex finder pass over one drive head", grader.Finder),
		gradeVerb(a, GraderFidelity, "One fidelity grade of one drive head", grader.Fidelity),
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

func liveDeps(a *app.App) (grader.Deps, error) {
	plugin := os.Getenv(pluginRootEnv)
	if plugin == "" {
		return grader.Deps{}, errors.New("lr grade runs through the plugin's bin/lr, which sets " + pluginRootEnv)
	}
	config := os.Getenv("CLAUDE_CONFIG_DIR")
	if config == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return grader.Deps{}, err
		}
		config = filepath.Join(home, ".claude")
	}
	return grader.Deps{
		Runner: a.Runner,
		Post:   client.Post,
		Sleep:  time.Sleep,
		Now:    a.Now,
		Log:    a.Log,
		Bin:    filepath.Join(plugin, "bin"),
		Config: config,
	}, nil
}
