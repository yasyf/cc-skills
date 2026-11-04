package alert

import (
	"path/filepath"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "alert",
		Short: "Act on a production alert at the owner's word",
	}
	cmd.AddCommand(muteCommand(a))
	return cmd
}

func muteCommand(a *app.App) *cobra.Command {
	var why string
	cmd := &cobra.Command{
		Use:   "mute MONITOR --why TEXT",
		Short: "Mute a Datadog monitor indefinitely, stand down its lanes, and record it owner-muted so no lane launches on it again",
		Long:  "A mute acts on production monitoring, so only the owner's explicit order runs this verb. It creates an indefinite Datadog downtime on MONITOR through pup with TEXT as its message, posts one root OWNER-MUTED record to the communicator and the supervisor carrying the downtime id, and posts a root stand-down to each live dd-MONITOR-* lane. The supervisor's alert watch and the orca desk launch nothing on an owner-muted monitor. A mute that fails changes nothing else.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			s, err := store.Open(filepath.Join(state, lanes.StoreFile))
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			rows, err := s.Lanes(store.LaneLaunching, store.LaneDesired)
			if err != nil {
				return err
			}
			live := make([]string, 0, len(rows))
			for _, row := range rows {
				live = append(live, row.Name)
			}
			return Mute{Bus: incident.CCI{}, Runner: a.Runner, Drive: cfg.Drive, Monitor: args[0], Why: why, Live: live, Out: cmd.OutOrStdout()}.Run(cmd.Context())
		},
	}
	cmd.Flags().StringVar(&why, "why", "", "the reason in plain words, stored as the downtime's message")
	if err := cmd.MarkFlagRequired("why"); err != nil {
		panic(err)
	}
	return cmd
}
