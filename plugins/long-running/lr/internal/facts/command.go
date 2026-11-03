package facts

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
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
			rows, err := New(a, cfg, s).Snapshot(cmd.Context())
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
	cmd.AddCommand(whoseCommand(a))
	return cmd
}

func whoseCommand(a *app.App) *cobra.Command {
	var driveName string
	cmd := &cobra.Command{
		Use:   "whose PR...",
		Short: "Print each pull request's GitHub author, its drive lane, and whether it is the drive's own, as JSON lines",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			numbers := make([]int, len(args))
			for i, arg := range args {
				number, err := strconv.Atoi(arg)
				if err != nil {
					return err
				}
				numbers[i] = number
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			cfg, err := config.Load(filepath.Join(entry.StateDir, "lr.json"), cmd.ErrOrStderr())
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			rows, err := New(a, cfg, s).Whose(cmd.Context(), numbers)
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
	cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	return cmd
}

func openStore(path string) (*store.Store, error) {
	if path == "" {
		return store.Memory()
	}
	return store.Open(path)
}
