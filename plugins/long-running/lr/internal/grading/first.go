package grading

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const kindFirst = "lr.first"

func firstDraft(bus, writer string, number int) client.Draft {
	return client.Draft{Drive: bus, Lane: writer, Kind: kindFirst, Text: fmt.Sprintf("first: #%d", number)}
}

func firstAsker(ctx context.Context, runner shell.Runner, named string) (string, error) {
	if named != "" {
		return named, nil
	}
	bound, err := lanes.BoundLane(ctx, runner)
	if err != nil || bound != "" {
		return bound, err
	}
	return "", errors.New("this session is bound to no lane, so the record could not say who moved the PR; pass --lane <your lane>")
}

func firstCommand(a *app.App) *cobra.Command {
	var driveName, asker string
	hours := int(rootFirstHorizon.Hours())
	cmd := &cobra.Command{
		Use:   "first N",
		Short: fmt.Sprintf("Move PR N to the front of the finder and fidelity queues for %d hours; the pool starts its passes, with the slots it reserves for a first PR", hours),
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			number, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("%q is not a PR number", args[0])
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			writer, err := firstAsker(c.Context(), a.Runner, asker)
			if err != nil {
				return err
			}
			if _, err := client.Post(c.Context(), firstDraft(entry.Bus(), writer, number)); err != nil {
				return err
			}
			fmt.Fprintf(c.OutOrStdout(), "#%d goes first in the finder and fidelity queues for %d hours; `lr grade queue finder` shows its row\n", number, hours)
			return nil
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	cmd.Flags().StringVar(&asker, "lane", "", "the lane asking, which the record names; default: the lane this session is bound to, or root for the drive's root")
	return cmd
}
