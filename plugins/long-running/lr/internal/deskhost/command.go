package deskhost

import (
	"os"
	"os/exec"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "desk", Short: "Run a desk on an always-on host whose keys come from SSM by its instance role"}
	var keys string
	run := &cobra.Command{
		Use:   "exec --keys FILE -- COMMAND [ARGS...]",
		Short: "Read the keys FILE names by the host's role and exec COMMAND with them in its environment, writing them nowhere",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			env, err := KeyEnv(c.Context(), a.Runner, keys)
			if err != nil {
				return err
			}
			path, err := exec.LookPath(args[0])
			if err != nil {
				return err
			}
			return syscall.Exec(path, args, append(env, os.Environ()...))
		},
	}
	run.Flags().SetInterspersed(false)
	run.Flags().StringVar(&keys, "keys", "", "the JSON file naming the region and each environment key's SSM parameter")
	if err := run.MarkFlagRequired("keys"); err != nil {
		panic(err)
	}
	var d Deploy
	deploy := &cobra.Command{
		Use:   "deploy --host NAME --profile PROFILE --region REGION",
		Short: "Point the host at this pack's pinned lr and cci and have it install them, each checked against its checksum and probed before it replaces the running one",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			d.Runner, d.Out, d.Sleep, d.Now, d.Config = a.Runner, c.OutOrStdout(), time.Sleep, a.Now, a.ConfigPath
			return d.Run(c.Context())
		},
	}
	deploy.Flags().StringVar(&d.Host, "host", "", "the box's Name tag")
	deploy.Flags().StringVar(&d.Profile, "profile", "", "the AWS profile that may write the deployment and send the command")
	deploy.Flags().StringVar(&d.Region, "region", "", "the box's region")
	for _, name := range []string{"host", "profile", "region"} {
		if err := deploy.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
	cmd.AddCommand(run, deploy)
	return cmd
}
