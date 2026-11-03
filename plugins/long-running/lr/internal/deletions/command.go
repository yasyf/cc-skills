package deletions

import (
	"fmt"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "deletions", Short: "Deletions a ruling owes, held to the drive's owed-deletions list"}
	cmd.AddCommand(&cobra.Command{
		Use:   "check",
		Short: "Exit 1 naming each path or symbol an in-force removes row names with no owed-deletion task and no exception row, with the rows that remove it",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			cfg, err := a.Config()
			if err != nil {
				return err
			}
			gaps, err := Of(a.Runner, cfg).Gaps(c.Context(), "rulings.py", cfg.Drive)
			if err != nil {
				return err
			}
			for _, gap := range gaps {
				fmt.Fprintln(c.OutOrStdout(), gap)
			}
			if len(gaps) > 0 {
				return fmt.Errorf("%d removes targets owe an owed-deletion task or an exception row", len(gaps))
			}
			fmt.Fprintln(c.OutOrStdout(), "every in-force removes row has an owed-deletion task or an exception row")
			return nil
		},
	})
	return cmd
}
