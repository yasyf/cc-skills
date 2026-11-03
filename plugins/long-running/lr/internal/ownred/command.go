package ownred

import (
	"fmt"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	var repo string
	var below, stack bool
	cmd := &cobra.Command{
		Use:   "own-red [PR]",
		Short: "Print an OWN-RED line for each finished failing check a merge would land past on its own PR's change, and an INHERITED line for each red outside it",
		Long: `Read every PR the merge would land and print, for each check that finished
red, one OWN-RED line per finding in a file that PR changes, or one INHERITED
line when its findings all sit outside the PR's diff. A check's findings are its
annotations, or the path:line mentions in its Actions job log when it annotates
no file. A pending check prints nothing.

The PR is a number, URL or branch, the current branch's when none is given.
With --below, the merge also lands every open PR below it in its native stack,
as ccx vcs merge does. With --stack, the selector reads as gh stack merge reads
it: a stack number, or a stacked PR merged together with every open PR below
it, and with none the whole stack of the current branch's PR.`,
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m := Merge{Repo: repo, Mode: One}
			if len(args) == 1 {
				m.Selector = args[0]
			}
			switch {
			case below:
				m.Mode = Below
			case stack:
				m.Mode = Stack
			}
			lines, err := Guard{Runner: a.Runner}.Lines(cmd.Context(), m)
			for _, line := range lines {
				fmt.Fprintln(cmd.OutOrStdout(), line)
			}
			return err
		},
	}
	cmd.Flags().StringVarP(&repo, "repo", "R", "", "the owner/name repository (default: the current checkout's)")
	cmd.Flags().BoolVar(&below, "below", false, "the merge also lands every open PR below this one in its native stack")
	cmd.Flags().BoolVar(&stack, "stack", false, "read the selector as gh stack merge does")
	cmd.MarkFlagsMutuallyExclusive("below", "stack")
	return cmd
}
