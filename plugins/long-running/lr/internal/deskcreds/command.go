package deskcreds

import (
	"errors"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "desk-creds", Short: "Hold a Sprite host's credentials in memory, never in a file, a record, an argument or a service's environment"}
	var servePort, getPort, pushPort int
	serve := &cobra.Command{
		Use:   "serve",
		Short: "Hold each profile's credentials and each pushed secret in memory, answering on 127.0.0.1, and keep the Sprite awake",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return Serve(cmd.Context(), a.Runner, servePort, cmd.ErrOrStderr())
		},
	}
	serve.Flags().IntVar(&servePort, "port", DefaultPort, "the local port")
	get := &cobra.Command{
		Use:   "get PROFILE [FIELD]",
		Short: "Print a profile's credentials for its credential_process",
		Args:  cobra.RangeArgs(1, 2),
		RunE: func(cmd *cobra.Command, args []string) error {
			field := ""
			if len(args) == 2 {
				field = args[1]
			}
			return Get(cmd.Context(), args[0], field, getPort, cmd.OutOrStdout())
		},
	}
	get.Flags().IntVar(&getPort, "port", DefaultPort, "the local port")
	cmd.AddCommand(serve, get, pushCommand(a, &pushPort))
	return cmd
}

func pushCommand(a *app.App, port *int) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "push",
		Short: "Hand the host's session credentials and secrets to its serve over standard input",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if a.ConfigPath == "" {
				return errors.New("name runner.json with --config")
			}
			e := Env{Runner: a.Runner, Out: cmd.OutOrStdout(), Err: cmd.ErrOrStderr(), Now: a.Now}
			return e.Push(cmd.Context(), a.ConfigPath, *port)
		},
	}
	cmd.Flags().IntVar(port, "port", DefaultPort, "the host's local port")
	return cmd
}
