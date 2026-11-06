package runhost

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "run-host", Short: "Reach the Orca server on the cloud service host from this Mac over an SSM port forward"}
	cmd.AddCommand(installCommand(a), forwardCommand(a))
	return cmd
}

func flags(cmd *cobra.Command, s *Settings) {
	cmd.Flags().StringVar(&s.Host, "host", "", "the service host's EC2 Name tag")
	cmd.Flags().StringVar(&s.Profile, "profile", "", "the AWS profile the Mac reaches the host as, the machine certificate's own")
	cmd.Flags().StringVar(&s.Region, "region", "", "the host's AWS region")
	cmd.Flags().IntVar(&s.Port, "port", 0, "the port orca serve listens on, forwarded to the same port on this Mac")
	for _, name := range []string{"host", "profile", "region", "port"} {
		_ = cmd.MarkFlagRequired(name)
	}
}

func installCommand(a *app.App) *cobra.Command {
	var s Settings
	cmd := &cobra.Command{
		Use:   "install",
		Short: "Read the host's Orca server over SSM, keep a launchd port forward to it, and pair this Mac's Orca with it as --environment",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			env, err := lanes.NewEnv(a.Runner, cfg, state, a.Now)
			if err != nil {
				return err
			}
			agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
			if err != nil {
				return err
			}
			host := Host{Runner: a.Runner, Agents: agents, Dial: Dial, Sleep: time.Sleep, Out: cmd.OutOrStdout()}
			binary := filepath.Join(env.Claude, "long-running", "current", "bin", "lr")
			return host.Install(cmd.Context(), s, cfg.Drive, binary, filepath.Join(state, "run-host.log"))
		},
	}
	flags(cmd, &s)
	cmd.Flags().StringVar(&s.Environment, "environment", "", "the Orca environment this Mac names the host's server")
	_ = cmd.MarkFlagRequired("environment")
	return cmd
}

func forwardCommand(a *app.App) *cobra.Command {
	var s Settings
	cmd := &cobra.Command{
		Use:   "forward",
		Short: "Become the SSM port forward to the host's Orca server; launchd runs this and restarts it when the session ends",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			argv, err := ForwardArgv(cmd.Context(), a.Runner, s)
			if err != nil {
				return err
			}
			binary, err := exec.LookPath(argv[0])
			if err != nil {
				return err
			}
			return syscall.Exec(binary, argv, os.Environ())
		},
	}
	flags(cmd, &s)
	return cmd
}
