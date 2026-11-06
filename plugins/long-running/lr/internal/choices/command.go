package choices

import (
	"context"
	"fmt"
	"os"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/explain"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
)

func New(ctx context.Context, a *app.App, entry drive.Entry, claude string, s *store.Store) *Check {
	cards := board.CardsOf(a, entry, entry.Root(), s)
	check := &Check{
		Drive: entry.Bus(),
		Asks:  cards.OpenAsks,
		Drawn: cards.DrawnAsks,
		Desk: func() (string, error) {
			route, err := watch.Desks(s)
			if err != nil {
				return "", err
			}
			return route(pass.BoardDesk), nil
		},
		Main: func(ctx context.Context) ([]string, error) {
			return board.MainBoard(ctx, a.Runner, entry.Root())
		},
		Owners: func(lane string) ([]string, error) {
			return lanes.Owners(s, lane)
		},
		Now:      a.Now,
		Patience: patience,
	}
	if checkout, err := explain.Locate(ctx, a.Runner, entry.Repo, claude); err == nil {
		check.Docs = &Docs{Runner: a.Runner, Dir: checkout.Dir}
	}
	return check
}

func Command(a *app.App) *cobra.Command {
	var driveName string
	cmd := &cobra.Command{
		Use:   "choices",
		Short: "List what he should be choosing and cannot see: a CARD record with no card, a card with no Slack copy, a park on a board that is not his main one, a choice in prose or a design doc, and a repeated order",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			claude, err := drive.ConfigDir()
			if err != nil {
				return err
			}
			check := New(c.Context(), a, entry, claude, s)
			check.Patience = 0
			for _, finding := range check.Findings(c.Context()) {
				fmt.Fprintf(c.OutOrStdout(), "-> %s: %s\n", strings.Join(finding.To, ","), finding.Line)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	return cmd
}
