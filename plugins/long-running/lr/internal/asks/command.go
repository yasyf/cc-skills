package asks

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"slices"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	mirrorFresh = 10 * time.Minute
	ownerWindow = 2 * time.Hour
)

var (
	notOwners  = []string{"root", "main", "all", ""}
	endedKinds = []string{"stand-down", "handoff", "stopped", "done"}
)

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

func (g Graded) Status(now time.Time) string {
	line := fmt.Sprintf("%s %s", g.State, g.Line())
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
					fmt.Fprintln(cmd.OutOrStdout(), graded.Status(now))
				}
				return nil
			}
			out := make([]map[string]string, len(open))
			for i, graded := range open {
				out[i] = map[string]string{"key": graded.Key, "lane": graded.Lane, "state": string(graded.State), "text": graded.Text, "line": graded.Status(now)}
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
	cmd.AddCommand(writeCommands(a, &driveName)...)
	return cmd
}

func refusal(now time.Time, bus string) func(ctx context.Context, lane string) (string, error) {
	return func(ctx context.Context, lane string) (string, error) {
		if slices.Contains(notOwners, lane) {
			return fmt.Sprintf("%s is the coordinator, not a worker; name the worker that builds it, or ask the coordinator to spawn one", lane), nil
		}
		found, err := client.Tail(ctx, client.Query{Drive: bus, Since: now.Add(-ownerWindow)})
		if err != nil {
			return "", err
		}
		kind := ""
		posted := false
		for _, record := range found {
			if record.Lane == lane {
				posted, kind = true, string(record.Kind)
			}
		}
		switch {
		case !posted:
			return fmt.Sprintf("%s has posted nothing on %s in 2h; name a live worker, or ask the coordinator to spawn one", lane, bus), nil
		case slices.Contains(endedKinds, kind):
			return fmt.Sprintf("%s has posted nothing on %s in 2h since its %s; name a live worker, or ask the coordinator to spawn one", lane, bus, kind), nil
		}
		return "", nil
	}
}

func writeCommands(a *app.App, driveName *string) []*cobra.Command {
	opened := func(run func(cmd *cobra.Command, entry drive.Entry, mirror Mirror) error) func(*cobra.Command, []string) error {
		return func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			mirror, err := OpenMirror(a, entry)
			if err != nil {
				return err
			}
			defer mirror.Store.Close()
			return run(cmd, entry, mirror)
		}
	}
	var add NewAsk
	addCmd := &cobra.Command{
		Use:   "add",
		Short: "Record an owner ask verbatim with its live owner lane, acceptance check and due time",
		Args:  cobra.NoArgs,
		RunE: opened(func(cmd *cobra.Command, entry drive.Entry, m Mirror) error {
			line, held, err := m.Add(cmd.Context(), add, refusal(a.Now(), entry.Bus()))
			if err != nil {
				return err
			}
			if held != "" {
				fmt.Fprintf(cmd.ErrOrStderr(), "%s already holds this ask; the repeat is a progress entry on it, not a new row\n", held)
				return nil
			}
			fmt.Fprintln(cmd.OutOrStdout(), line)
			return nil
		}),
	}
	addCmd.Flags().StringVar(&add.Text, "text", "", "the owner's words, verbatim")
	addCmd.Flags().StringVar(&add.Lane, "lane", "", "the live lane that delivers it")
	addCmd.Flags().StringVar(&add.Accept, "accept", "", "the check that says it is done")
	addCmd.Flags().StringVar(&add.Due, "due", "", "an ISO 8601 time or a span such as 4h")
	addCmd.Flags().IntVar(&add.Standing, "standing", 0, "an ask that is never done: in progress while its newest entry is under this many hours old")
	required(addCmd, "text", "lane", "accept", "due")

	var ask, state, note, reason, text, at string
	var artifacts []string
	var hours int
	keyed := func(run func(cmd *cobra.Command, m Mirror, key string) error) func(*cobra.Command, []string) error {
		return opened(func(cmd *cobra.Command, _ drive.Entry, m Mirror) error {
			key, err := Key(ask)
			if err != nil {
				return err
			}
			return run(cmd, m, key)
		})
	}
	stateCmd := &cobra.Command{Use: "state", Short: "Override an ask's computed state; open restores it", Args: cobra.NoArgs, RunE: keyed(func(cmd *cobra.Command, m Mirror, key string) error {
		return m.SetState(cmd.Context(), key, state, note)
	})}
	stateCmd.Flags().StringVar(&state, "state", "", "backlog, live, lost or open")
	stateCmd.Flags().StringVar(&note, "note", "", "why")
	standingCmd := &cobra.Command{Use: "standing", Short: "Make an ask standing", Args: cobra.NoArgs, RunE: keyed(func(cmd *cobra.Command, m Mirror, key string) error {
		return m.SetStanding(cmd.Context(), key, hours)
	})}
	standingCmd.Flags().IntVar(&hours, "hours", StandingDefault, "hours since its newest entry before it reads stale")
	progressCmd := &cobra.Command{Use: "progress", Short: "Append a progress entry, restarting its 30-minute lost clock", Args: cobra.NoArgs, RunE: keyed(func(cmd *cobra.Command, m Mirror, key string) error {
		words, err := m.Progress(cmd.Context(), key, note, artifacts)
		if err != nil {
			return err
		}
		fmt.Fprintf(cmd.OutOrStdout(), "%s progress: %s\n", key, words)
		return nil
	})}
	progressCmd.Flags().StringVar(&note, "note", "", "what moved")
	progressCmd.Flags().StringArrayVar(&artifacts, "artifact", nil, "a permalink or id the progress produced, repeatable")
	dropCmd := &cobra.Command{Use: "drop", Short: "Terminal: the owner withdrew this ask", Args: cobra.NoArgs, RunE: keyed(func(cmd *cobra.Command, m Mirror, key string) error {
		return m.Drop(cmd.Context(), key, reason)
	})}
	dropCmd.Flags().StringVar(&reason, "reason", "", "his words")
	answerCmd := &cobra.Command{Use: "answer", Short: "Terminal: this ask was a question, now answered", Args: cobra.NoArgs, RunE: keyed(func(cmd *cobra.Command, m Mirror, key string) error {
		return m.Answer(cmd.Context(), key, text)
	})}
	answerCmd.Flags().StringVar(&text, "text", "", "the answer")
	for _, command := range []*cobra.Command{stateCmd, standingCmd, progressCmd, dropCmd, answerCmd} {
		command.Flags().StringVar(&ask, "ask", "", "the ask's id")
		required(command, "ask")
	}
	required(stateCmd, "state")
	required(progressCmd, "note")
	required(dropCmd, "reason")
	required(answerCmd, "text")
	liveCmd := &cobra.Command{Use: "live", Short: "Record when the pipeline that ships our lanes' PRs last ran, so a delivered ask can grade live", Args: cobra.NoArgs, RunE: opened(func(cmd *cobra.Command, _ drive.Entry, m Mirror) error {
		return m.Live(cmd.Context(), at, text)
	})}
	liveCmd.Flags().StringVar(&at, "at", "", "an RFC 3339 time")
	liveCmd.Flags().StringVar(&text, "text", "", "what ran")
	required(liveCmd, "at")
	return []*cobra.Command{addCmd, stateCmd, standingCmd, progressCmd, dropCmd, answerCmd, liveCmd}
}

func required(cmd *cobra.Command, names ...string) {
	for _, name := range names {
		if err := cmd.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
}
