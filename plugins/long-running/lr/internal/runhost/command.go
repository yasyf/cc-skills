package runhost

import (
	"errors"
	"os"
	"path/filepath"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "run-host", Short: "Serve the drive's Orca Run from its desk host, so launches outlive the Mac"}
	cmd.AddCommand(&cobra.Command{
		Use:   "install",
		Short: "Run orca serve on runner.json's host.sprite as a Sprite service, forward its port to the Mac with sprite proxy under launchd, and pair the Mac's Orca with it",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			settings, err := Load(filepath.Join(state, "runner.json"))
			if err != nil {
				return err
			}
			path := os.Getenv("PATH")
			if path == "" {
				return errors.New("PATH is empty; launchd would run sprite proxy with no sprite")
			}
			agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
			if err != nil {
				return err
			}
			host := Host{Runner: a.Runner, Agents: agents, Dial: Dial, Sleep: time.Sleep, Out: cmd.OutOrStdout()}
			return host.Install(cmd.Context(), settings, cfg.Drive, filepath.Join(state, "run-host-proxy.log"), path)
		},
	})
	return cmd
}
