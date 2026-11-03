package facts

import (
	"encoding/json"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func Command(a *app.App) *cobra.Command {
	var storePath string
	cmd := &cobra.Command{
		Use:   "facts",
		Short: "Read one pass's facts for every open pull request and print them as JSON lines",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, err := config.Load(a.ConfigPath, cmd.ErrOrStderr())
			if err != nil {
				return err
			}
			s, err := openStore(storePath)
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			rows, err := New(cfg, a.Runner, s, a.Now).Snapshot(cmd.Context())
			if err != nil {
				return err
			}
			out := json.NewEncoder(cmd.OutOrStdout())
			for _, row := range rows {
				if err := out.Encode(row); err != nil {
					return err
				}
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&storePath, "store", "", "the drive's store; empty reads into a throwaway store and changes nothing")
	return cmd
}

func openStore(path string) (*store.Store, error) {
	if path == "" {
		return store.Memory()
	}
	return store.Open(path)
}
