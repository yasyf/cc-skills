package lanes

import (
	"context"
	"fmt"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
)

const (
	boardWatch    = "board-watch"
	backfillEvent = "backfill"
	tapped        = "tapped"
)

func ApprovedCommand(a *app.App) *cobra.Command {
	var since int64
	cmd := &cobra.Command{
		Use:   "approved",
		Short: "The PRs whose newest board verdict approves them, split into not open and still open, from one record read and one batched PR read; backfilled verdicts excluded",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, state, err := sessionScope(a)
			if err != nil {
				return err
			}
			env, err := NewEnv(a.Runner, cfg, state, a.Now)
			if err != nil {
				return err
			}
			approved, gone, open, err := env.Approved(cmd.Context(), since)
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "approved: %s\nnot open: %s\nstill open: %s\n", prList(approved), prList(gone), prList(open))
			return nil
		},
	}
	cmd.Flags().Int64Var(&since, "since", 0, "read only records after this seq")
	return cmd
}

func (e Env) Approved(ctx context.Context, since int64) (approved, gone, open []int, err error) {
	records, err := e.Bus.Tail(ctx, client.Query{Drive: e.Config.Drive, After: since, Kinds: []string{"owner"}, Lanes: []string{boardWatch}})
	if err != nil {
		return nil, nil, nil, err
	}
	approved = approvedIn(records)
	states, err := (&prReader{env: e, ctx: ctx, read: map[int]prRead{}}).states(approved)
	if err != nil {
		return nil, nil, nil, err
	}
	for _, n := range approved {
		if states[n] == Closed {
			gone = append(gone, n)
		} else {
			open = append(open, n)
		}
	}
	return approved, gone, open, nil
}

func approvedIn(records []client.Record) []int {
	newest := map[int]string{}
	for _, record := range records {
		if record.Fields.Attrs["event"] == backfillEvent {
			continue
		}
		for _, order := range bus.OrdersOf(record, "") {
			newest[order.PR] = order.Value
		}
	}
	var approved []int
	for n, value := range newest {
		if value == tapped {
			approved = append(approved, n)
		}
	}
	slices.Sort(approved)
	return approved
}

func prList(numbers []int) string {
	if len(numbers) == 0 {
		return "none"
	}
	out := make([]string, len(numbers))
	for i, n := range numbers {
		out[i] = fmt.Sprintf("#%d", n)
	}
	return strings.Join(out, " ")
}
