package ownred

import (
	"fmt"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	var repo, drive, lane string
	var below, stack bool
	cmd := &cobra.Command{
		Use:   "own-red [PR]",
		Short: "Print an OWN-RED line for each finished failing check a merge would land past on its own PR's change, and an INHERITED line for each red inherited from its base",
		Long: `Read every PR the merge would land and print, for each check that finished
red, one OWN-RED line per finding in a file that PR changes, or one INHERITED
line when its findings all sit outside the PR's diff. A check's findings are its
annotations, or the path:line mentions in its Actions job log when it annotates
no file. A pending check prints nothing.

A compiler can name either side of a redeclaration, so a Go build or vet, tsc,
tsgo or compiler error outside the diff proves nothing. That check reads the
base branch instead, in one call for every PR: the nearest finished run of the
same check at or below the PR's merge base. Red there prints INHERITED, green
prints an OWN-RED line per compile error, and no readable run prints INHERITED
marked "base branch state unread".

The PR is a number, URL or branch, the current branch's when none is given.
With --below, the merge also lands every open PR below it in its native stack,
as ccx vcs merge does. With --stack, the selector reads as gh stack merge reads
it: a stack number, or a stacked PR merged together with every open PR below
it, and with none the whole stack of the current branch's PR.

With --lane, the merge first reads that lane's records on --drive, as the lane
reads them, through its own cursor, whole and in one read. It prints one UNREAD
line for each record another lane addressed to it that no reply has closed, that
no record resolved and that the lane has not answered, and one MAIL line for
each other record from another lane, the oldest and the newest 20 when more
arrived, with a line naming how to read the rest.`,
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m := Merge{Repo: repo, Mode: One, Drive: drive, Lane: lane}
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
	cmd.Flags().StringVar(&drive, "drive", "", "the cci drive the lane reads")
	cmd.Flags().StringVar(&lane, "lane", "", "the lane running the merge, whose unread records refuse it")
	cmd.MarkFlagsMutuallyExclusive("below", "stack")
	cmd.MarkFlagsRequiredTogether("drive", "lane")
	return cmd
}
