package deskcreds

import (
	"errors"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "desk-creds", Short: "Hold a Sprite host's credentials in memory, never in a file, a record, an argument or a service's environment"}
	var servePort, getPort, pushPort, keepPort int
	var wait bool
	serve := &cobra.Command{
		Use:   "serve",
		Short: "Hold each profile's credentials and each pushed secret in memory, answering on 127.0.0.1, and keep the Sprite awake",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, err := a.Config()
			if err != nil {
				return err
			}
			return Serve(cmd.Context(), a.Runner, servePort, cfg.Drive, cmd.ErrOrStderr())
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
			if !wait {
				return Get(cmd.Context(), args[0], field, getPort, cmd.OutOrStdout())
			}
			cfg, err := a.Config()
			if err != nil {
				return err
			}
			return Wait(cmd.Context(), args[0], field, getPort, cfg.Drive, EscalateAfter, cmd.ErrOrStderr(), cmd.OutOrStdout())
		},
	}
	get.Flags().IntVar(&getPort, "port", DefaultPort, "the local port")
	get.Flags().BoolVar(&wait, "wait", false, "wait until serve holds the credentials, telling root once if nothing pushes them within two minutes")
	cmd.AddCommand(serve, get, pushCommand(a, &pushPort), keepCommand(a, &keepPort))
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

func keepCommand(a *app.App, port *int) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "keep",
		Short: "Push the host's credentials now, every 15 minutes, and each time its serve starts holding nothing",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if a.ConfigPath == "" {
				return errors.New("name runner.json with --config")
			}
			e := Env{Runner: a.Runner, Out: cmd.OutOrStdout(), Err: cmd.ErrOrStderr(), Now: a.Now}
			return e.Keep(cmd.Context(), a.ConfigPath, *port)
		},
	}
	cmd.Flags().IntVar(port, "port", DefaultPort, "the host's local port")
	return cmd
}
