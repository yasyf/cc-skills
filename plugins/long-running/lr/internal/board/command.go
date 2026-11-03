package board

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	lrfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	nothingWaits   = "Nothing waits on your tap or pick."
	boardWatch     = "board-watch"
	boardTopic     = "board-rows"
	snapshotWindow = 24 * time.Hour
	configFile     = "lr.json"
)

func Snapshots(ctx context.Context, bus string, since time.Time) ([]Snapshot, error) {
	found, err := client.Tail(ctx, client.Query{Drive: bus, Since: since, Kinds: []string{"note"}})
	if err != nil {
		return nil, err
	}
	var snapshots []Snapshot
	for _, record := range found {
		if record.Lane == boardWatch && record.Topic == boardTopic && record.Fields.Attrs["board"] != "" {
			snapshots = append(snapshots, Snapshot{record.Seq, record.At, record.Fields.Attrs["board"]})
		}
	}
	return snapshots, nil
}

func closedRecords(ctx context.Context, bus string, seqs []string) (map[string]bool, error) {
	oldest := int64(0)
	for _, text := range seqs {
		seq, err := strconv.ParseInt(text, 10, 64)
		if err != nil {
			return nil, err
		}
		if oldest == 0 || seq < oldest {
			oldest = seq
		}
	}
	found, err := client.Tail(ctx, client.Query{Drive: bus, After: oldest - 1})
	if err != nil {
		return nil, err
	}
	closed := map[string]bool{}
	for _, record := range found {
		text := strconv.FormatInt(record.Seq, 10)
		if slices.Contains(seqs, text) {
			closed[text] = record.Status == "closed"
		}
	}
	return closed, nil
}

func Command(a *app.App) *cobra.Command {
	var driveName, session string
	cmd := &cobra.Command{Use: "board", Short: "The owner's board: what waits on him and when each card ends"}
	cmd.PersistentFlags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	cmd.PersistentFlags().StringVar(&session, "session", "", "the board's session (default: the drive's root)")
	opened := func() (drive.Entry, string, error) {
		entry, err := drive.Named(driveName)
		if err != nil {
			return drive.Entry{}, "", err
		}
		if session != "" {
			return entry, session, nil
		}
		return entry, entry.Root(), nil
	}
	cards := func() (Cards, error) {
		entry, board, err := opened()
		if err != nil {
			return Cards{}, err
		}
		return Cards{
			Runner: a.Runner, Ledger: ledger.CLI{Runner: a.Runner, Repo: entry.Checkout}, CCX: a.CCX,
			Bus: entry.Bus(), Checkout: entry.Checkout, Session: board, StateDir: entry.StateDir, Now: a.Now,
			Closed: func(ctx context.Context, seqs []string) (map[string]bool, error) {
				return closedRecords(ctx, entry.Bus(), seqs)
			},
			Withdraw: func(ctx context.Context, lane, card, text string) error {
				_, err := client.Post(ctx, client.Draft{Drive: entry.Bus(), Lane: lane, Kind: "withdraw", Topic: card, To: []string{"root"}, Text: text})
				return err
			},
		}, nil
	}
	cmd.AddCommand(waitsCommand(a, opened), cardsCommand(cards), passCommand(a, opened))
	return cmd
}

func snapshotOf(ctx context.Context, a *app.App, entry drive.Entry, s *store.Store, warn io.Writer) (string, map[int]machine.Facts, error) {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), warn)
	if err != nil {
		return "", nil, err
	}
	found, err := lrfacts.New(a, cfg, s).Snapshot(ctx)
	if err != nil {
		return "", nil, err
	}
	byNumber := make(map[int]machine.Facts, len(found))
	for _, row := range found {
		byNumber[row.Number] = row
	}
	return cfg.Repo, byNumber, nil
}

func explainPRs(c *cobra.Command, a *app.App, entry drive.Entry, live Live, prs []string, asJSON bool) error {
	s, err := store.Open(entry.Store())
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	_, snapshot, err := snapshotOf(c.Context(), a, entry, s, c.ErrOrStderr())
	if err != nil {
		return err
	}
	found := make([]PRWait, len(prs))
	for i, named := range prs {
		pr, err := strconv.Atoi(strings.TrimPrefix(named, "#"))
		if err != nil {
			return fmt.Errorf("--pr %q: %w", named, err)
		}
		found[i] = live.Explain(pr, snapshot)
	}
	if asJSON {
		return json.NewEncoder(c.OutOrStdout()).Encode(found)
	}
	for _, wait := range found {
		fmt.Fprintln(c.OutOrStdout(), wait.Line)
	}
	return nil
}

func postHooks(ctx context.Context, busName, lane, repository string) Hooks {
	posts := bus.Bus{Drive: busName, Lane: lane}
	return Hooks{
		Tap: func(tap TapRecord) error {
			_, err := posts.Post(ctx, nil, bus.Tap{PR: tap.PR, Head: tap.Head, By: tap.By, Value: string(tap.Value)})
			return err
		},
		Hold: func(pr int, sha string) error {
			_, err := client.Post(ctx, client.Draft{Drive: busName, Lane: byOwner, Kind: "hold", PRs: []string{fmt.Sprintf("%s#%d", repository, pr)}, Text: fmt.Sprintf("hold #%d at %s: the owner rejected this head on the board", pr, short(sha))})
			return err
		},
		Note: func(to, note string) error {
			_, err := client.Post(ctx, client.Draft{Drive: busName, Lane: lane, Kind: "note", To: []string{to}, Text: note})
			return err
		},
	}
}

func passCommand(a *app.App, opened func() (drive.Entry, string, error)) *cobra.Command {
	var lane string
	pass := &cobra.Command{
		Use:   "pass",
		Short: "Apply each answered merge row, redraw what still asks him and tell each lane that owes words",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, session, err := opened()
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			repository, snapshot, err := snapshotOf(c.Context(), a, entry, s, c.ErrOrStderr())
			if err != nil {
				return err
			}
			hooks := postHooks(c.Context(), entry.Bus(), lane, repository)
			block, err := Pass{s, repository, a.Runner, session, hooks, a.Now}.Run(c.Context(), snapshot)
			if err != nil {
				return err
			}
			asked := 0
			for _, group := range block.Groups {
				asked += len(group.PRs)
			}
			fmt.Fprintf(c.OutOrStdout(), "%d rows ask the owner\n", asked)
			return nil
		},
	}
	pass.Flags().StringVar(&lane, "lane", "", "the lane the tap and note records post as")
	if err := pass.MarkFlagRequired("lane"); err != nil {
		panic(err)
	}
	return pass
}

func waitsCommand(a *app.App, opened func() (drive.Entry, string, error)) *cobra.Command {
	var asJSON bool
	var prs []string
	waits := &cobra.Command{
		Use:   "waits",
		Short: "One line per board row awaiting his tap and each pick he has not answered, or with --pr what each PR waits on",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, session, err := opened()
			if err != nil {
				return err
			}
			outcomes, err := ReadOutcomes(c.Context(), a.Runner, session)
			if err != nil {
				return err
			}
			snapshots, err := Snapshots(c.Context(), entry.Bus(), a.Now().Add(-snapshotWindow))
			if err != nil {
				return err
			}
			live, err := LiveOf(outcomes, snapshots)
			if err != nil {
				return err
			}
			if len(prs) > 0 {
				return explainPRs(c, a, entry, live, prs, asJSON)
			}
			found := live.Waits()
			if asJSON {
				type line struct {
					Wait
					Line string `json:"line"`
				}
				out := make([]line, len(found))
				for i, wait := range found {
					out[i] = line{wait, wait.Line()}
				}
				return json.NewEncoder(c.OutOrStdout()).Encode(out)
			}
			if len(found) == 0 {
				fmt.Fprintln(c.OutOrStdout(), nothingWaits)
				return nil
			}
			for _, wait := range found {
				fmt.Fprintln(c.OutOrStdout(), wait.Line())
			}
			return nil
		},
	}
	waits.Flags().BoolVar(&asJSON, "json", false, "print a JSON list")
	waits.Flags().StringArrayVar(&prs, "pr", nil, "say what this open PR waits on, repeatable")
	return waits
}

func cardsCommand(cards func() (Cards, error)) *cobra.Command {
	var until, lane string
	cmd := &cobra.Command{Use: "cards", Short: "The event that ends each question card on the owner's board"}
	record := &cobra.Command{
		Use:   "until CARD",
		Short: "Record the event that ends a card, refusing a missing, malformed or past condition",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			board, err := cards()
			if err != nil {
				return err
			}
			end, err := board.Until(c.Context(), args[0], until)
			if err != nil {
				return err
			}
			fmt.Fprintf(c.OutOrStdout(), "%s ends at %s\n", args[0], end)
			return nil
		},
	}
	record.Flags().StringVar(&until, "until", "", "at:TIME, pr:N, release:STACK@ENV:COMMIT or record:SEQ")
	sweep := &cobra.Command{
		Use:   "sweep",
		Short: "Withdraw every card whose end has happened and name each card with none",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			board, err := cards()
			if err != nil {
				return err
			}
			return board.Sweep(c.Context(), lane, c.OutOrStdout())
		},
	}
	sweep.Flags().StringVar(&lane, "lane", "", "the lane the withdraw records post as")
	if err := sweep.MarkFlagRequired("lane"); err != nil {
		panic(err)
	}
	cmd.AddCommand(record, &cobra.Command{
		Use:   "missing [CARD...]",
		Short: "Print each named card, or each card on the board, with no recorded end",
		RunE: func(c *cobra.Command, args []string) error {
			board, err := cards()
			if err != nil {
				return err
			}
			missing, err := board.Missing(c.Context(), args)
			if err != nil {
				return err
			}
			if len(missing) > 0 {
				fmt.Fprintln(c.OutOrStdout(), strings.Join(missing, "\n"))
			}
			return nil
		},
	}, sweep)
	return cmd
}
