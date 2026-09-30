package main

import (
	"github.com/spf13/cobra"
	"github.com/yasyf/daemonkit"
)

func appTrust() daemonkit.Trust {
	return daemonkit.Trust{Serving: daemonkit.ServingSameUser()}
}

func platformCmds() []*cobra.Command { return []*cobra.Command{superviseCmd()} }

func superviseCmd() *cobra.Command {
	return &cobra.Command{
		Use:    "supervise",
		Short:  "Supervise the background daemon in the foreground",
		Hidden: true,
		Args:   cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			return daemonkit.Supervise(c.Context(), codexServiceLabel)
		},
	}
}
