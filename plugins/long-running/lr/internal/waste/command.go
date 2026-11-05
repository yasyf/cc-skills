package waste

import (
	"fmt"
	"path/filepath"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func Command(a *app.App) *cobra.Command {
	var window time.Duration
	cmd := &cobra.Command{
		Use:   "waste",
		Short: "Print the drive's largest wastes of deliveries and tokens in a window, ranked, with who is responsible",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			drive, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			cfg, err := ReadConfig(state)
			if err != nil {
				return err
			}
			env, err := lanes.NewEnv(a.Runner, drive, state, a.Now)
			if err != nil {
				return err
			}
			s, err := store.Open(filepath.Join(state, lanes.StoreFile))
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			running, err := Current(a.CCX, env.Claude, cmd.Root().Version)
			if err != nil {
				return err
			}
			report, err := Read(cmd.Context(), Live(env.Bus, env.Runner, s, running), cfg, drive, a.Now(), window)
			if err != nil {
				return err
			}
			text, _ := Render(report.Findings, report.note())
			_, err = fmt.Fprint(cmd.OutOrStdout(), text)
			return err
		},
	}
	cmd.Flags().DurationVar(&window, "window", Window, "how far back to read")
	return cmd
}
