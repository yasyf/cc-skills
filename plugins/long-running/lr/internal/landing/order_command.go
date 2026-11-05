package landing

import (
	"context"
	"fmt"
	"io"
	"path/filepath"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const ownerWordKind = "owner"

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
		return fmt.Errorf("record #%d, a %s from lane %s, is no owner word naming #%d; post his words, then run this on that record: cci post --drive %s --lane owner-relay --kind %s --pr %s#%d \"Owner: <his words> #%d\"", seq, records[0].Kind, records[0].Lane, number, cfg.Drive, ownerWordKind, cfg.Repo, number, number)
	}
	s, err := store.Open(filepath.Join(filepath.Dir(a.ConfigPath), storeFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	return writeOrder(ctx, s, cfg.Repo, bus.Bus{Drive: cfg.Drive, Lane: facts.OrderLane}, number, records[0], stdout)
}

func writeOrder(ctx context.Context, s *store.Store, repo string, orders bus.Bus, number int, word client.Record, stdout io.Writer) error {
	tap := bus.Tap{PR: number, By: bus.OwnerBy, Value: string(machine.TapTapped)}
	head, refusal, err := prrow.TapAt(s, repo, tap)
	if err != nil {
		return err
	}
	tap.Head = head
	if refusal != "" {
		if err := orders.PostRefusal(ctx, repo, tap, refusal); err != nil {
			return err
		}
		fmt.Fprintf(stdout, "REFUSED #%d: %s\n", number, refusal)
		return &wait.ExitError{Code: 1}
	}
	if err := orders.PostWordTap(ctx, repo, tap, word); err != nil {
		return err
	}
	fmt.Fprintf(stdout, "ORDER #%d at %s: recorded on %s; the landing host writes the land order on its next tick\n", number, head[:7], word.Name())
	return nil
}

func holdsOwnerWord(record client.Record, number int) bool {
	names := regexp.MustCompile(`#` + strconv.Itoa(number) + `\b`)
	return string(record.Kind) == ownerWordKind && slices.Contains(ownerWordLanes, client.Stem(record.Lane)) && names.MatchString(bus.FullText(record))
}
