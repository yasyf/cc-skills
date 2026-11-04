package board

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const onlyHim = "Only your approval."

func ReviewRows(snapshot []machine.Facts) []store.ReviewRow {
	byNumber := make(map[int]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		byNumber[f.Number] = f
	}
	rows := make([]store.ReviewRow, 0, len(snapshot))
	for _, f := range snapshot {
		if f.Closed || f.Merged || !f.Ours() {
			continue
		}
		waits := withoutTap(gates.Missing(f))
		if below, stuck := stuckBelow(f, byNumber); stuck {
			waits = append(waits, fmt.Sprintf("the change below it, %s", titleOf(below)))
		}
		said := onlyHim
		if len(waits) > 0 {
			said = strings.Join(waits, "; ")
		}
		rows = append(rows, store.ReviewRow{Number: f.Number, Head: f.Head, Lane: f.Lane, Title: titleOf(f), Waits: said, Ready: len(waits) == 0, Tapped: f.Tapped()})
	}
	return rows
}

func titleOf(f machine.Facts) string {
	if f.Title != "" {
		return f.Title
	}
	return fmt.Sprintf("#%d", f.Number)
}

func WriteReviewRows(s *store.Store, repo string, snapshot []machine.Facts, at time.Time) error {
	return s.PutReviewRows(repo, ReviewRows(snapshot), at)
}

func rowsCommand(opened func() (drive.Entry, string, error)) *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "rows",
		Short: "Every open drive PR from the landing store, with what each waits on: the one list the review tab, status lines and asks read",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
			if err != nil {
				return err
			}
			cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), c.ErrOrStderr())
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			rows, err := s.ReviewRows(cfg.Repo)
			if err != nil {
				return err
			}
			if asJSON {
				return json.NewEncoder(c.OutOrStdout()).Encode(rows)
			}
			for _, row := range rows {
				fmt.Fprintf(c.OutOrStdout(), "#%d at %s (%s): %s: %s\n", row.Number, abbreviate(row.Head), row.Lane, row.Title, row.Waits)
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "print the rows as one JSON array")
	return cmd
}
