package watch

import (
	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/prompts"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "watch",
		Short: "Find what nobody can see waiting: terminals at a prompt and waits past their deadline",
	}
	cmd.AddCommand(prompts.Command(a))
	return cmd
}
