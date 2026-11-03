package asks

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const mirrorFresh = 10 * time.Minute

type Mirror struct {
	Source ledger.Store
	Store  *store.Store
	Ledger string
	Now    func() time.Time
}

func (m Mirror) Sync(ctx context.Context) (int, error) {
	rows, err := m.Source.Rows(ctx, m.Ledger)
	if err != nil {
		return 0, err
	}
	byKey := make(map[string]map[string]string, len(rows))
	for _, row := range rows {
		byKey[row.Key] = row.Fields
	}
	return len(rows), m.Store.PutLedgerRows(m.Ledger, byKey, m.Now())
}

func (m Mirror) Book(ctx context.Context) (Book, error) {
	synced, err := m.Store.LedgerSynced(m.Ledger)
	if err != nil {
		return Book{}, err
	}
	if m.Now().Sub(synced) >= mirrorFresh {
		if _, err := m.Sync(ctx); err != nil {
			return Book{}, err
		}
	}
	rows, err := m.Store.LedgerRows(m.Ledger)
	if err != nil {
		return Book{}, err
	}
	return Read(rows)
}

func OpenMirror(a *app.App, entry drive.Entry) (Mirror, error) {
	s, err := store.Open(entry.Store())
	if err != nil {
		return Mirror{}, err
	}
	return Mirror{ledger.CLI{Runner: a.Runner, Repo: entry.Checkout}, s, entry.Ledger, a.Now}, nil
}

func (g Graded) Line(now time.Time) string {
	line := fmt.Sprintf("%s %s", g.State, g.Ask.Line())
	if g.Newest != nil {
		line += fmt.Sprintf(" | progress %dm: %s", int(now.Sub(g.Newest.At).Minutes()), g.Newest.Note)
	}
	return line
}

func Command(a *app.App) *cobra.Command {
	var driveName string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "asks",
		Short: "Every open owner ask, what is landed but not live first, from the store",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			mirror, err := OpenMirror(a, entry)
			if err != nil {
				return err
			}
			defer mirror.Store.Close()
			book, err := mirror.Book(cmd.Context())
			if err != nil {
				return err
			}
			now := a.Now()
			open := book.Open(now)
			if !asJSON {
				for _, graded := range open {
					fmt.Fprintln(cmd.OutOrStdout(), graded.Line(now))
				}
				return nil
			}
			out := make([]map[string]string, len(open))
			for i, graded := range open {
				out[i] = map[string]string{"key": graded.Key, "lane": graded.Lane, "state": string(graded.State), "text": graded.Text, "line": graded.Line(now)}
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(out)
		},
	}
	cmd.PersistentFlags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print a JSON list")
	cmd.AddCommand(&cobra.Command{
		Use:   "sync",
		Short: "Refresh the store's copy of the drive's ledger",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			mirror, err := OpenMirror(a, entry)
			if err != nil {
				return err
			}
			defer mirror.Store.Close()
			count, err := mirror.Sync(cmd.Context())
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%d rows of %s\n", count, entry.Ledger)
			return nil
		},
	})
	return cmd
}
