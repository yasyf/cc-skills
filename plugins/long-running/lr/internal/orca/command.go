package orca

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const planRefused = 3

func stateHome() (string, error) {
	if dir := os.Getenv("XDG_STATE_HOME"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "state"), nil
}

func GCCommand(a *app.App) *cobra.Command {
	var run, plan string
	var dispatches, done []string
	var dryRun bool
	cmd := &cobra.Command{
		Use:   "gc",
		Short: "Reclaim what an Orca Run's settled dispatches left behind",
		Long: `Reclaim what an Orca Run's settled dispatches left behind.

A settled dispatch whose terminal Orca owns is released with worker-release. One whose terminal a launch created, which Orca retains as external_terminal and never closes, has its tab closed once its screen reads idle. A recorded handle Orca shows as disconnected is already closed; one Orca calls stale is kept and named.

A settled dispatch's worktree is removed with orca worktree rm when no unsettled dispatch, no other agent and no busy shell is left in it, its tree is clean, its HEAD is on its own repository's origin or landed there, and no live process holds it, read right before the removal. A worktree whose directory is gone has its registration dropped; one git cannot read is kept and named. A worktree Orca no longer tracks is removed with git worktree remove after the same checks.

--done ends a lane the coordinator decided is done, settled or not: worker-stop fences the dispatch, or worker-abandon settles one Orca already fenced, and its terminal closes without the idle check before its worktree is reclaimed by the same rules.

A dispatch on a paired Orca server goes through that server's --environment. A dispatch whose worktree and terminal a cc-remote Sprite task records has its Sprite destroyed once no unsettled dispatch shares the workspace, its terminal reads idle twice (skipped under --done), and its checkout, read over ssh, is clean with its HEAD on origin or landed.

--plan takes a dry run's output; gc plans again first and refuses, printing the difference, unless the two match. Cleanup is paused until $XDG_STATE_HOME/orca-gc/resumed exists. --dry-run reads through a pause. Prints one line per action, skipped action or kept resource, then the tally.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			state, err := stateHome()
			if err != nil {
				return err
			}
			home, err := os.UserHomeDir()
			if err != nil {
				return err
			}
			gc := &GC{
				Orca:   Live{Client{Runner: a.Runner}, Processes{Runner: a.Runner, Home: home}},
				Git:    &Git{Runner: a.Runner},
				Fleet:  CCRemote{Runner: a.Runner, State: filepath.Join(state, "cc-remote")},
				Pause:  Pause{Resumed: filepath.Join(state, "orca-gc", "resumed")},
				Lock:   filepath.Join(state, "orca-gc", "remove.lock"),
				Out:    cmd.OutOrStdout(),
				DryRun: dryRun,
			}
			if plan != "" {
				planned, err := os.ReadFile(plan)
				if err != nil {
					return err
				}
				replanned, err := gc.Replan(cmd.Context(), run, dispatches, done)
				if err != nil {
					return err
				}
				if moved := Drift(string(planned), replanned); len(moved) > 0 {
					fmt.Fprintf(cmd.OutOrStdout(), "refused: the plan differs from %s\n%s\n", plan, strings.Join(moved, "\n"))
					return &wait.ExitError{Code: planRefused}
				}
			}
			tally, err := gc.Run(cmd.Context(), run, dispatches, done)
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), tally)
			return nil
		},
	}
	cmd.Flags().StringVar(&run, "run", "", "the Orca Run id")
	cmd.Flags().StringArrayVar(&dispatches, "dispatch", nil, "only this settled dispatch; repeatable")
	cmd.Flags().StringArrayVar(&done, "done", nil, "worker-stop this dispatch, close its terminal, and reclaim its worktree; repeatable")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "print what gc would do and change nothing")
	cmd.Flags().StringVar(&plan, "plan", "", "a dry run's output; gc plans again first and refuses, printing the difference, unless the new plan prints the same lines")
	if err := cmd.MarkFlagRequired("run"); err != nil {
		panic(err)
	}
	return cmd
}
