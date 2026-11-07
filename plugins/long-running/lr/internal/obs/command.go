package obs

import (
	"fmt"
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/findings"
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
				claude, err := drive.ConfigDir()
				if err != nil {
					return err
				}
				desk, err := NewDesk(cmd.Context(), a.Runner, settings, cfg.Drive, state, a.Now, cmd.ErrOrStderr())
				if err != nil {
					return err
				}
				return desk.Serve(cmd.Context(), claude)
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
					Env:       [][2]string{{"CLAUDE_LONG_RUNNING_DRIVE", cfg.Drive}, {lanes.LaneEnv, Lane}},
					Arguments: []string{filepath.Join(claude, "long-running", "current", "bin", "lr"), "obs", "desk", "--config", config},
					Directory: state,
					Log:       filepath.Join(state, "obs-desk.log"),
				})
			},
		},
		restartCommand(a),
	)
	return cmd
}

func restartCommand(a *app.App) *cobra.Command {
	var record int64
	cmd := &cobra.Command{
		Use:   "restart --record SEQ",
		Short: "Restart the loaded obs desk once, so it runs the installed lr, on the owner record holding his word for that restart; a record past the pass that read it waits on its lane's still-unmet record",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, _, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			entry, err := drive.Named(cfg.Drive)
			if err != nil {
				return err
			}
			if err := findings.New(a, entry).Clears(cmd.Context(), record); err != nil {
				return err
			}
			agents, err := launchd.New(a.Runner, cmd.OutOrStdout())
			if err != nil {
				return err
			}
			if err := agents.Restart(cmd.Context(), Label(cfg.Drive)); err != nil {
				return fmt.Errorf("%w; lr obs install writes it", err)
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "lr: %s restarted\n", Label(cfg.Drive))
			return err
		},
	}
	cmd.Flags().Int64Var(&record, "record", 0, "the seq of the owner record holding his word for this restart")
	if err := cmd.MarkFlagRequired("record"); err != nil {
		panic(err)
	}
	return cmd
}
