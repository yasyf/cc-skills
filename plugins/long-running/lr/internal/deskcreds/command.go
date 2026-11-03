package deskcreds

import (
	"errors"
	"os"
	"os/user"
	"syscall"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func env(cmd *cobra.Command, a *app.App) (Env, error) {
	current, err := user.Current()
	if err != nil {
		return Env{}, err
	}
	home := os.Getenv("HOME")
	if home == "" {
		home = current.HomeDir
	}
	if a.ConfigPath == "" {
		return Env{}, errors.New("name runner.json with --config")
	}
	plugin := os.Getenv("BINRUN_PLUGIN_ROOT")
	if plugin == "" {
		return Env{}, errors.New("run lr through the plugin's bin/lr, which names the plugin root")
	}
	return Env{Runner: a.Runner, Out: cmd.OutOrStdout(), Err: cmd.ErrOrStderr(), Now: a.Now, Plugin: plugin, Home: home, UID: os.Getuid(), Path: os.Getenv("PATH"), SHM: shmDir, Port: DefaultPort, Exec: syscall.Exec}, nil
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "desk-creds", Short: "Run a desk on a Sprite host whose credentials are short-lived or held in memory, never in a file, a record, an argument or a service's environment"}
	var servePort, getPort, pushPort int
	serve := &cobra.Command{
		Use:   "serve",
		Short: "Hold each profile's credentials and each pushed secret in memory, answering on 127.0.0.1, and keep the Sprite awake",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return Serve(cmd.Context(), a.Runner, servePort, shmDir, cmd.ErrOrStderr())
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
	cmd.AddCommand(serve, get, pushCommand(a, &pushPort), installCommand(a), runCommand(a))
	return cmd
}

func pushCommand(a *app.App, port *int) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "push",
		Short: "Hand the host's session credentials and secrets to its serve over standard input",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			e, err := env(cmd, a)
			if err != nil {
				return err
			}
			return e.Push(cmd.Context(), a.ConfigPath, *port)
		},
	}
	cmd.Flags().IntVar(port, "port", DefaultPort, "the host's local port")
	return cmd
}

func installCommand(a *app.App) *cobra.Command {
	var desk string
	cmd := &cobra.Command{
		Use:   "install",
		Short: "Move a desk onto the Sprite runner.json's host.sprite names",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			e, err := env(cmd, a)
			if err != nil {
				return err
			}
			return e.Install(cmd.Context(), a.ConfigPath, desk)
		},
	}
	cmd.Flags().StringVar(&desk, "desk", "", "the desk")
	_ = cmd.MarkFlagRequired("desk")
	return cmd
}

func runCommand(a *app.App) *cobra.Command {
	var desk string
	var preflight bool
	cmd := &cobra.Command{
		Use:   "run",
		Short: "Point the host's tools at the pushed credentials and exec the desk runner, or its preflight",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			e, err := env(cmd, a)
			if err != nil {
				return err
			}
			return e.Run(cmd.Context(), a.ConfigPath, desk, preflight)
		},
	}
	cmd.Flags().StringVar(&desk, "desk", "", "the desk")
	cmd.Flags().BoolVar(&preflight, "preflight", false, "ask the desk runner whether it would start on this config")
	_ = cmd.MarkFlagRequired("desk")
	return cmd
}
