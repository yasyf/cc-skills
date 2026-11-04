package landing

import (
	"context"
	"fmt"
	"io"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

var ownerWordLanes = []string{"board-watch", "owner-relay"}

func orderCommand(a *app.App) *cobra.Command {
	var seq int64
	cmd := &cobra.Command{
		Use:   "order PR --record SEQ",
		Short: "Write one PR's land order at its current head, on the owner's word that record holds: a thread answer the relay recorded, or a hand carry of his tap",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			number, err := strconv.Atoi(strings.TrimPrefix(args[0], "#"))
			if err != nil {
				return err
			}
			return runOrder(cmd.Context(), a, number, seq, cmd.OutOrStdout())
		},
	}
	cmd.Flags().Int64Var(&seq, "record", 0, "the seq of the record holding his word")
	if err := cmd.MarkFlagRequired("record"); err != nil {
		panic(err)
	}
	return cmd
}

func runOrder(ctx context.Context, a *app.App, number int, seq int64, stdout io.Writer) error {
	cfg, err := a.Config()
	if err != nil {
		return err
	}
	records, err := client.Among(ctx, cfg.Drive, []int64{seq})
	if err != nil {
		return err
	}
	if len(records) == 0 {
		return fmt.Errorf("no record #%d on drive %s", seq, cfg.Drive)
	}
	if !holdsOwnerWord(records[0], number) {
		return fmt.Errorf("record #%d, from lane %s, is no owner word naming #%d", seq, records[0].Lane, number)
	}
	heads, err := currentHeads(ctx, a.CCX, cfg.Repo, []int{number}, "")
	if err != nil {
		return err
	}
	head, open := heads[number]
	if !open {
		return fmt.Errorf("#%d is not an open pull request", number)
	}
	posted, err := bus.Bus{Drive: cfg.Drive, Lane: facts.OrderLane}.PostOrder(ctx, bus.Tap{PR: number, Head: head, By: bus.OwnerBy, Value: string(machine.TapTapped)}, records[0])
	if err != nil {
		return err
	}
	fmt.Fprintf(stdout, "ORDER #%d at %s: posted #%d on %s\n", number, head[:7], posted.Seq, records[0].Name())
	return nil
}

func holdsOwnerWord(record client.Record, number int) bool {
	names := regexp.MustCompile(`#` + strconv.Itoa(number) + `\b`)
	return slices.Contains(ownerWordLanes, client.Stem(record.Lane)) && names.MatchString(record.Text)
}
