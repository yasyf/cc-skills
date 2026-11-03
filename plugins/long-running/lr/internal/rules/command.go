package rules

import (
	"fmt"
	"io"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(*app.App) *cobra.Command {
	rules := &cobra.Command{Use: "rules", Short: "Owner rules as checks over a diff's hunks"}
	rules.AddCommand(&cobra.Command{
		Use:   "check RULE PREDICATE[:ARGUMENT]",
		Short: "Grade the unified diff on stdin against one rule; exit 1 when it is not met",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			diff, err := io.ReadAll(cmd.InOrStdin())
			if err != nil {
				return err
			}
			check, err := Evaluate(args[0], args[1], Parse(string(diff)))
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s: %s\n", check.Rule, check.How)
			if !check.Met {
				return fmt.Errorf("rule %s not met", check.Rule)
			}
			return nil
		},
	})
	return rules
}
