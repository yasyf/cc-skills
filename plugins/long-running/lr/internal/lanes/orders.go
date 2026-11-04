package lanes

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"slices"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func OpenOrders(ctx context.Context, bus Bus, s *store.Store, drive, repo, state string) ([]owner.Row, error) {
	ledger, err := os.ReadFile(owner.Ledger(state))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	rows, err := LivenessRows(s)
	if err != nil {
		return nil, err
	}
	prs, err := s.PRs(repo)
	if err != nil {
		return nil, err
	}
	open := map[int]bool{}
	for _, pr := range prs {
		open[pr.Number] = pr.State != machine.StateLanded && pr.State != machine.StateClosed
	}
	var carriers []owner.Lane
	for _, row := range rows {
		if len(row.Orders) > 0 {
			carriers = append(carriers, owner.Lane{Name: row.Lane, Working: row.Live || slices.ContainsFunc(row.PRs, func(pr int) bool { return open[pr] }), Orders: row.Orders})
		}
	}
	proofs, err := bus.Tail(ctx, client.Query{Drive: drive, Kinds: []string{"landed", "done", "report"}})
	if err != nil {
		return nil, fmt.Errorf("%w: cci tail: %w", ErrUnread, err)
	}
	proofs = slices.DeleteFunc(proofs, func(proof client.Record) bool { return proof.Lane == Self && proof.Kind == "report" })
	parsed := owner.Parse(ledger)
	late := owner.PastDue(parsed, time.Now())
	return append(late, slices.DeleteFunc(owner.Undelivered(parsed, carriers, proofs), func(order owner.Row) bool {
		return slices.ContainsFunc(late, func(row owner.Row) bool { return row.ID == order.ID })
	})...), nil
}

func AuditCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "audit",
		Short: "Print every owner ask nobody has delivered: each order with no live lane, no open PR and no delivered proof, marked in progress or parked, and each question still open; it advises and never blocks",
		Long:  "Only a record's delivered attr credits an order, as `cci post --kind report --attr delivered=owner:m11` once he has what the row asked for. A live lane whose brief names owner:m<row>, or one with an open PR, keeps the row off the list while it works. A landed or done record naming the row or carried by its lane, or a report naming it, moves the row to in progress, never to delivered. A row whose last lane's latest record names a hold or a part left open shows as parked, with that hold and lane, and the supervisor sends the parked list to the progress desk every two hours. A board tap, a bare question and a row marked `lr owner mark <row> no-order` are not orders, and a bare question is listed until its row is marked. The ledger keeps one row per ask, so a message with several asks has rows m<N>.1, m<N>.2 and so on, and each row is marked and delivered by its own id.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(cmd.Flag("drive").Value.String())
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			orders, err := OpenOrders(cmd.Context(), CCI{}, s, entry.Bus(), entry.Repo, entry.StateDir)
			if err != nil {
				return err
			}
			for _, order := range orders {
				fmt.Fprintln(cmd.OutOrStdout(), order.Line())
			}
			return nil
		},
	}
}
