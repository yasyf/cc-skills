package orca

import (
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const failedExit = 1

func LaunchCommand(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "launch",
		Short: "Start Orca workers for the drive's lanes",
	}
	cmd.AddCommand(startCommand(a))
	return cmd
}

func startCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "start LANE MODEL EFFORT BRIEF",
		Short: "Launch one Orca worker from the terminal that coordinates the Run, and print one line",
		Long: `Launch one Orca worker for LANE and print one line:

  LANE ready task=ID dispatch=ID terminal=HANDLE worktree=PATH

Anything else is a failure, printed as one line naming the step, exit 1. A launch that fails before worker-start, or that worker-start refuses before it dispatches anything, rolls back what it made and says so.

A relaunch whose receipt names a worktree that is still a checkout resumes in it. Every other launch makes a worktree under a name no launch has used: PREFIX+LANE-RUN-N, on branch USER/PREFIX+LANE-RUN-N. orca worktree create makes it by default; ORCA_LAUNCH_SPARSE=1 adds it with git worktree add from the main checkout, sparse in a repo over ORCA_LAUNCH_SPARSE_FILES tracked files.

MODEL is opus, sonnet, fable, a claude-* model id, sol or codex (gpt-6.1-sol on the standard tier), incident (gpt-6.1-sol on the fast tier), astra (gpt-6-astra, for exceptional cases only), or a gpt-* model id. EFFORT is low, medium, high, xhigh or max.

Only the terminal bound to the Run may call worker-start, so a launch from any other terminal creates nothing and names the rebind command.

  ORCA_LAUNCH_RUN            orchestration Run id, default the Run of this session's drive
  ORCA_LAUNCH_STALL_SECONDS  give up with LAUNCH-FAILED once one wait lasts this long, default 180
  ORCA_LAUNCH_REPO           Orca repo id, default the repo at the parent's main checkout
  ORCA_LAUNCH_PARENT         coordinator worktree path, default the current directory
  ORCA_LAUNCH_NO_PARENT      1 creates a top-level worktree, as incident always does
  ORCA_LAUNCH_PREFIX         worktree name prefix, default none
  ORCA_LAUNCH_ROOT           directory a new worktree goes in
  ORCA_LAUNCH_BASE           base branch, default the parent checkout's origin/HEAD
  ORCA_LAUNCH_STATE          receipt directory, default $XDG_STATE_HOME/long-running/orca-launch/RUN
  ORCA_LAUNCH_CLAUDE_ARGS    further claude args, default none
  ORCA_LAUNCH_MCP_CONFIG     space-separated --mcp-config values for a claude worker, default none
  ORCA_LAUNCH_CODEX_MCP      inline TOML table for an incident worker's mcp_servers, default {}
  ORCA_LAUNCH_BOOT_SECONDS   ceiling on the waits for a terminal, its agent and its first turn, default 180
  ORCA_LAUNCH_WORKTREE_SECONDS  ceiling on the waits for a worktree to register and check out, default 180
  ORCA_LAUNCH_PREPARE        shell command run in each worktree this launch creates, before its worker starts
  ORCA_LAUNCH_PREPARE_SECONDS  ceiling on that command, default 120
  ORCA_LAUNCH_SPARSE         1 adds each new worktree by git worktree add
  ORCA_LAUNCH_SPARSE_FILES   tracked-file count above which an added worktree is sparse, default 5000`,
		Args: cobra.ExactArgs(4),
		RunE: func(cmd *cobra.Command, args []string) error {
			starter := &Starter{Runner: a.Runner, Bus: CCI{}, Env: os.Getenv, Sleep: time.Sleep, Now: a.Now, Stderr: cmd.ErrOrStderr()}
			line, err := starter.Run(cmd.Context(), args[0], args[1], args[2], args[3])
			var failed *Failed
			if errors.As(err, &failed) {
				fmt.Fprintln(cmd.OutOrStdout(), failed.Line)
				return &wait.ExitError{Code: failedExit}
			}
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), line)
			return nil
		},
	}
}
