package main

import (
	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
)

func init() {
	register(func(a *app.App) *cobra.Command {
		cmd := owner.Command(a, lanes.CardLane)
		cmd.AddCommand(lanes.AuditCommand())
		return cmd
	})
}
