package lanes

import (
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
)

func RunnerCommand(a *app.App) *cobra.Command {
	runnerFile := func() (string, error) {
		_, state, err := Loaded(a)
		return filepath.Join(state, "runner.json"), err
	}
	cmd := &cobra.Command{Use: "runner", Short: "Edit the drive's runner.json"}
	set := &cobra.Command{
		Use:   "set KEY VALUE",
		Short: "Set a dotted KEY such as landing.desk_lane to VALUE, parsed as JSON when it is JSON and kept as a string otherwise; the file is replaced by rename, only after the edited file loads",
		Args:  cobra.ExactArgs(2),
		RunE: func(_ *cobra.Command, args []string) error {
			path, err := runnerFile()
			if err != nil {
				return err
			}
			return config.SetRunnerKey(path, args[0], args[1])
		},
	}
	unset := &cobra.Command{
		Use:   "unset KEY",
		Short: "Remove a dotted KEY, by the same atomic replace",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			path, err := runnerFile()
			if err != nil {
				return err
			}
			return config.UnsetRunnerKey(path, args[0])
		},
	}
	configured := &cobra.Command{Use: "config", Short: "Set or unset one runner.json key"}
	configured.AddCommand(set, unset)
	cmd.AddCommand(configured)
	return cmd
}
