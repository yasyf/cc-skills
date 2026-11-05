package obs

import (
	"errors"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
)

func Label(drive string) string {
	return "com.long-running." + drive + ".obs"
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "obs", Short: "Sentry, Datadog and ai-oncall reads for lanes with no access: they ask the obs-desk lane over cci, and it answers from this Mac"}
	cmd.AddCommand(
		&cobra.Command{
			Use:   "desk",
			Short: "Answer each ask addressed to obs-desk with read calls only, holding the Sentry and Datadog keys obs.json names in memory from SSM",
			Args:  cobra.NoArgs,
			RunE: func(cmd *cobra.Command, _ []string) error {
				cfg, state, err := lanes.Loaded(a)
				if err != nil {
					return err
				}
				settings, err := LoadSettings(filepath.Join(state, "obs.json"))
				if err != nil {
					return err
				}
				desk, err := NewDesk(cmd.Context(), a.Runner, settings, cfg.Drive, state, a.Now, cmd.ErrOrStderr())
				if err != nil {
					return err
				}
				return desk.Serve(cmd.Context())
			},
		},
		&cobra.Command{
			Use:   "install",
			Short: "Write the obs desk's launchd agent, which runs the installed lr, and bootstrap it",
			Args:  cobra.NoArgs,
			RunE: func(cmd *cobra.Command, _ []string) error {
				cfg, state, err := lanes.Loaded(a)
				if err != nil {
					return err
				}
				if _, err := LoadSettings(filepath.Join(state, "obs.json")); err != nil {
					return err
				}
				path := os.Getenv("PATH")
				if path == "" {
					return errors.New("PATH is empty; launchd would run the desk with no aws CLI")
				}
				claude, err := drive.ConfigDir()
				if err != nil {
					return err
				}
				config, err := a.ConfigFile()
				if err != nil {
					return err
				}
				if config, err = filepath.Abs(config); err != nil {
					return err
				}
				agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
				if err != nil {
					return err
				}
				return agents.Install(cmd.Context(), launchd.Job{
					Label:     Label(cfg.Drive),
					Env:       [][2]string{{"CLAUDE_LONG_RUNNING_DRIVE", cfg.Drive}, {lanes.LaneEnv, Lane}, {"PATH", path}},
					Arguments: []string{filepath.Join(claude, "long-running", "current", "bin", "lr"), "obs", "desk", "--config", config},
					Directory: state,
					Log:       filepath.Join(state, "obs-desk.log"),
				})
			},
		},
	)
	return cmd
}
