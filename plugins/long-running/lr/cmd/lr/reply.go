package main

import (
	"os"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/reply"
)

func init() {
	register(func(a *app.App) *cobra.Command {
		return reply.Command(a, func() *cobra.Command { return root(app.New(os.Stderr)) })
	})
}
