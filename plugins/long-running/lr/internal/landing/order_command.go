package landing

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	ownerWordKind = "owner"
	openState     = "OPEN"
)

var ownerWordLanes = []string{"board-watch", "owner-relay", "pr-cards"}

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
	hosted := !errors.Is(err, fs.ErrNotExist)
	if hosted && err != nil {
		return err
	}
	if !hosted {
		entry, err := drive.Session()
		if err != nil {
			return err
		}
		cfg = config.Config{Drive: entry.Bus(), Repo: entry.Repo}
	}
	records, err := client.Among(ctx, cfg.Drive, []int64{seq})
	if err != nil {
		return err
	}
	if len(records) == 0 {
		return fmt.Errorf("no record #%d on drive %s", seq, cfg.Drive)
	}
	if gap := ownerWordGap(records[0], number); gap != "" {
		return fmt.Errorf("record #%d is no owner word naming #%d: %s; an order needs a %s record from lane %s whose own text or body names #%d, and a --pr field alone does not count; post his words naming it, then run this on that record: cci post --drive %s --lane owner-relay --kind %s --pr %s#%d \"Owner: <his words> #%d\". A test-flake fix needs no order, since his standing word covers the class: claim it on the head record, cci post --drive %s --lane <your lane> --kind head --head %s#%d=<head> --attr %s=<the test that flaked>, and the gate lands it untapped once every changed file is test or fixture code and no added line names a timeout", seq, number, gap, ownerWordKind, strings.Join(ownerWordLanes, " or "), number, cfg.Drive, ownerWordKind, cfg.Repo, number, number, cfg.Drive, cfg.Repo, number, bus.FlakeFixAttr)
	}
	writer, err := lanes.BoundLane(ctx, a.Runner)
	if err != nil {
		return err
	}
	if writer == "" {
		return errors.New("this session is bound to no lane, so its order could not say who wrote it; run it from a lane's session")
	}
	orders := bus.Bus{Drive: cfg.Drive, Lane: facts.OrderLane}
	if !hosted {
		return postOrder(ctx, a.CCX, cfg.Repo, orders, number, records[0], writer, stdout)
	}
	s, err := store.Open(filepath.Join(filepath.Dir(a.ConfigPath), storeFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	return writeOrder(ctx, s, cfg.Repo, orders, number, records[0], writer, stdout)
}

func writeOrder(ctx context.Context, s *store.Store, repo string, orders bus.Bus, number int, word client.Record, writer string, stdout io.Writer) error {
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
	if err := orders.PostWordTap(ctx, repo, tap, word, writer); err != nil {
		return err
	}
	fmt.Fprintf(stdout, "ORDER #%d at %s: recorded on %s; the landing host writes the land order on its next tick\n", number, head[:7], word.Name())
	return nil
}

func postOrder(ctx context.Context, prs ccx.Client, repo string, orders bus.Bus, number int, word client.Record, writer string, stdout io.Writer) error {
	rows, err := prs.PRStatus(ctx, repo, []int{number})
	if err != nil {
		return err
	}
	at := slices.IndexFunc(rows, func(row ccx.PRStatus) bool { return row.Number == number && row.State == openState })
	if at < 0 {
		return fmt.Errorf("#%d is no open PR of %s, so no order was posted", number, repo)
	}
	tap := bus.Tap{PR: number, Head: rows[at].Head, By: bus.OwnerBy, Value: string(machine.TapTapped)}
	if err := orders.PostWordTap(ctx, repo, tap, word, writer); err != nil {
		return err
	}
	fmt.Fprintf(stdout, "ORDER #%d at %s: posted on the bus by %s, since this box holds no land store; the landing host gates it on its next tick and writes the land order, or a %s naming why not\n", number, tap.Head, writer, bus.TapRefusedKind)
	return nil
}

func ownerWordGap(record client.Record, number int) string {
	switch {
	case string(record.Kind) != ownerWordKind:
		return fmt.Sprintf("it is a %s", record.Kind)
	case !slices.Contains(ownerWordLanes, client.Stem(record.Lane)):
		return fmt.Sprintf("it is from lane %s", record.Lane)
	case !regexp.MustCompile(`#` + strconv.Itoa(number) + `\b`).MatchString(bus.FullText(record)):
		return fmt.Sprintf("neither its text nor its body names #%d", number)
	}
	return ""
}
