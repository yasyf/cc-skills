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
		Short: "Print an OWN-RED line for each finished failing check a merge would land that is green on its base, and an INHERITED line for each that is red there too",
		Long: `Read every PR the merge would land and compare each check that finished red
with the same check of the same workflow on the base branch, in one call for
every PR: the nearest finished run at or below the PR's merge base. Green there prints an OWN-RED line
with the failing job's link, whichever files the failure names, since a change
breaks tests it does not touch. Red there prints INHERITED. No readable run
prints BASE-UNREAD. A pending check prints nothing.

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
arrived, with a line naming how to read the rest. The store desk's answers and
their parts count as neither: the lr call that asked already read them.`,
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
