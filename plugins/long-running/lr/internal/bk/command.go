package bk

import (
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/roll"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "bk", Short: "Buildkite reads for lanes with no Buildkite access: they ask the bk-desk lane over cci, and it answers from the desk host"}
	cmd.AddCommand(&cobra.Command{
		Use:   "desk",
		Short: "Answer each ask addressed to bk-desk with this host's tools/ci bk and read-only token, one read at a time, until the lr install link moves to another version",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			taking, err := roll.Watch(cmd.Context())
			if err != nil {
				return err
			}
			cfg, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			settings, err := LoadSettings(filepath.Join(state, "bk.json"))
			if err != nil {
				return err
			}
			desk, err := NewDesk(cmd.Context(), a.Runner, settings, cfg.Drive, state, a.Now, cmd.ErrOrStderr())
			if err != nil {
				return err
			}
			return roll.Exit(desk.Serve(cmd.Context(), taking), cmd.ErrOrStderr())
		},
	})
	return cmd
}
