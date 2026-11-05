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
	var dispatches, done, keep, stale []string
	var dryRun, fresh bool
	cmd := &cobra.Command{
		Use:   "gc",
		Short: "Reclaim what an Orca Run's settled dispatches left behind",
		Long: `Reclaim what an Orca Run's settled dispatches left behind.

A settled dispatch whose terminal Orca owns is released with worker-release. One whose terminal a launch created, which Orca retains as external_terminal and never closes, has its tab closed once its screen sits at an empty prompt, Orca records the dispatch as the terminal's owner, its worktree is clean and pushed or landed, and one agent process names the terminal: a claude or codex that leads its own process group under that terminal's login shell, for that worktree. gc reads that process's pid, ppid, pgid, start time and full command, and reads them again right before the close; any difference keeps the terminal. A status-line repaint is not activity, so output timing plays no part. A recorded handle Orca shows as disconnected is already closed, as is one Orca calls stale that no process names; a stale handle a process still names is kept and named.

A settled dispatch's worktree is removed with orca worktree rm when no unsettled dispatch, no other agent and no busy shell is left in it, its tree is clean, its HEAD is on its own repository's origin or landed there, and no live process holds it, read right before the removal. A worktree whose directory is gone has its registration dropped; one git cannot read is kept and named. A worktree Orca no longer tracks is removed with ccx vcs worktree rm --path after the same checks.

--done ends a lane the coordinator decided is done, settled or not: worker-stop fences the dispatch, or worker-abandon settles one Orca already fenced, and its terminal closes without the idle check before its worktree is reclaimed by the same rules.

--stale ends a dispatch Orca never settled, whose lane the supervisor found retired and silent, only with proof the session is stale: Orca's worker-list says its host is unavailable, so worker-abandon settles it with nothing left to read, or its screen sits at an empty prompt, one verified agent process names its terminal, and its tree is clean and pushed or landed. Anything it cannot read keeps the session and names why.

A dispatch on a paired Orca server goes through that server's --environment; one on a server Orca no longer knows has nothing left to reach, so gc skips it and says nothing. A dispatch whose worktree and terminal a cc-remote Sprite task records has its Sprite destroyed once no unsettled dispatch shares the workspace, its terminal reads idle twice or, with its Orca runtime down, sprite exec finds no claude or codex process on the machine and none with a working directory in its checkout (both skipped under --done), and its checkout and every other checkout lr sprites hold recorded on it, read through sprite exec, are clean with HEAD on origin or landed; right before the destroy, gc deletes from each checkout's origin the refs/lr/work/* refs that checkout holds, and a delete that fails keeps the Sprite. What sprite exec cannot read keeps the Sprite. A destroy runs WORKER_CC_REMOTE with CC_REMOTE_CONFIG when set, otherwise the bin/cc-remote and config.yaml under tools/cc-remote of the checkout gc runs from, otherwise cc-remote from PATH with its own config lookup.

--new sweeps up to forty settled dispatches a pass, those no pass has swept first, newest first, then those swept longest ago, and records the order under the state directory. The lane supervisor runs it every five minutes, with --keep for the dispatch of every lane the registry still wants, so a dispatch is reclaimed soon after it settles and its lane retires, and what a pass kept is read again on a later one.

--plan takes a dry run's output; gc plans again first and refuses, printing the difference, unless the two match. Cleanup is paused until $XDG_STATE_HOME/orca-gc/resumed exists. --dry-run reads through a pause and runs every read a removal runs, the live-process scan included, so its lines name what a real run would do. Prints one line per action, skipped action or kept resource, then the tally.`,
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
				Keep:   map[string]bool{},
			}
			for _, dispatch := range keep {
				gc.Keep[dispatch] = true
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
			ledger := Swept{Path: filepath.Join(state, "orca-gc", "swept-"+run+".json")}
			proven, err := gc.proveStale(cmd.Context(), run, stale)
			if err != nil {
				return err
			}
			done = append(done, proven...)
			if fresh {
				if dispatches, err = gc.due(cmd.Context(), run, ledger); err != nil {
					return err
				}
				if len(dispatches) == 0 && len(done) == 0 {
					fmt.Fprintln(cmd.OutOrStdout(), Tally{})
					return nil
				}
			}
			tally, err := gc.Run(cmd.Context(), run, dispatches, done)
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), tally)
			if fresh && !dryRun && !gc.Pause.Held() {
				return ledger.record(dispatches)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&run, "run", "", "the Orca Run id")
	cmd.Flags().StringArrayVar(&dispatches, "dispatch", nil, "only this settled dispatch; repeatable")
	cmd.Flags().StringArrayVar(&done, "done", nil, "worker-stop this dispatch, close its terminal, and reclaim its worktree; repeatable")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "print what gc would do and change nothing")
	cmd.Flags().StringVar(&plan, "plan", "", "a dry run's output; gc plans again first and refuses, printing the difference, unless the new plan prints the same lines")
	cmd.Flags().StringArrayVar(&keep, "keep", nil, "treat this dispatch as live, so its terminal and worktree stay, whatever Orca says of it; repeatable")
	cmd.Flags().StringArrayVar(&stale, "stale", nil, "treat this unsettled dispatch as --done once its screen sits at an empty prompt, one verified agent process names its terminal and its tree is clean and pushed; repeatable")
	cmd.Flags().BoolVar(&fresh, "new", false, "settled dispatches no pass swept yet, newest first, then those swept longest ago, up to forty a pass")
	cmd.MarkFlagsMutuallyExclusive("new", "dispatch")
	cmd.MarkFlagsMutuallyExclusive("new", "done")
	cmd.MarkFlagsMutuallyExclusive("new", "plan")
	if err := cmd.MarkFlagRequired("run"); err != nil {
		panic(err)
	}
	return cmd
}
