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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	nothingWaits   = "Nothing waits on your tap or pick."
	boardWatch     = "board-watch"
	boardTopic     = "board-rows"
	snapshotWindow = 24 * time.Hour
	configFile     = "lr.json"
	judgeTimeout   = 2 * time.Minute
	rulingsShown   = 3
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
	cmd.PersistentFlags().StringVar(&session, "session", "", "the board's session (default: the drive's coordinator)")
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
	cards := func() (Cards, func(), error) {
		entry, board, err := opened()
		if err != nil {
			return Cards{}, nil, err
		}
		s, err := store.Open(entry.Store())
		if err != nil {
			return Cards{}, nil, err
		}
		return CardsOf(a, entry, board, s), func() { _ = s.Close() }, nil
	}
	cmd.AddCommand(waitsCommand(a, opened), redsCommand(a, opened), cardsCommand(cards, packJudge(a.Runner)), passCommand(a, opened), classifyCommand(), slackCardCommand(a), linkCommand(a, opened), drawCommand(a, opened, packJudge(a.Runner)))
	return cmd
}

func CardsOf(a *app.App, entry drive.Entry, session string, s *store.Store) Cards {
	program := entry.Program
	if program == "" {
		program = filepath.Base(entry.StateDir)
	}
	return Cards{
		Runner: a.Runner, Ledger: ledger.CLI{Runner: a.Runner, Repo: entry.Checkout}, CCX: a.CCX,
		Bus: entry.Bus(), Checkout: entry.Checkout, Session: session, StateDir: entry.StateDir, Program: program, Store: s, Now: a.Now,
		Closed: func(ctx context.Context, seqs []string) (map[string]bool, error) {
			return closedRecords(ctx, entry.Bus(), seqs)
		},
		Withdraw: func(ctx context.Context, lane, card, text string) error {
			_, err := client.Post(ctx, client.Draft{Drive: entry.Bus(), Lane: lane, Kind: "withdraw", Topic: card, To: []string{"root"}, Text: text})
			return err
		},
		Report: func(ctx context.Context, lane string, to []string, topic, text string) error {
			_, err := client.Post(ctx, client.Draft{Drive: entry.Bus(), Lane: lane, Kind: "report", Topic: topic, To: to, Text: text})
			return err
		},
	}
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
	s, err := store.Memory()
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
			block, err := Pass{s, repository, a.Runner, a.CCX, session, hooks, a.Now}.Run(c.Context(), snapshot)
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

func cardsCommand(cards func() (Cards, func(), error), judge Judge) *cobra.Command {
	var until, owner, lane, pick, said string
	var on []string
	cmd := &cobra.Command{Use: "cards", Short: "The event that ends each question card on the owner's board, and what each card depends on"}
	record := &cobra.Command{
		Use:   "until CARD",
		Short: "Record the event that ends a card, refusing a missing, malformed or past condition, and the PRs and objects the card depends on",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			end, err := board.Until(c.Context(), args[0], until)
			if err != nil {
				return err
			}
			deps, err := board.Depend(c.Context(), args[0], owner, on)
			if err != nil {
				return err
			}
			fmt.Fprintf(c.OutOrStdout(), "%s ends at %s and depends on %s\n", args[0], end, strings.Join(deps, ", "))
			return nil
		},
	}
	record.Flags().StringVar(&until, "until", "", "at:TIME, pr:N, release:STACK@ENV:COMMIT or record:SEQ")
	record.Flags().StringVar(&owner, "lane", os.Getenv("CLAUDE_LONG_RUNNING_LANE"), "the lane that owns the card, told when a ruling may change it")
	record.Flags().StringArrayVar(&on, "on", nil, "an object or card:ID the card rests on beyond the PRs and register names it cites, repeatable")
	recheck := &cobra.Command{
		Use:   "recheck [RULING...]",
		Short: "Re-judge each open card resting on what these register rows or --pick just landed on: redraw it on the new premise, withdraw it, or flag it to its lane, and tell the communicator",
		RunE: func(c *cobra.Command, args []string) error {
			if (pick == "") == (len(args) == 0) {
				return fmt.Errorf("name the register rows that landed or --pick CARD, not both")
			}
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			r := Recheck{Cards: board, Lane: lane, Judge: judge}
			var moved []Moved
			if pick != "" {
				moved, err = r.Pick(c.Context(), pick, said)
			} else {
				moved, err = r.Ruling(c.Context(), args)
			}
			for _, one := range moved {
				fmt.Fprintln(c.OutOrStdout(), one.Line())
			}
			return err
		},
	}
	recheck.Flags().StringVar(&lane, "lane", "", "the lane the records post as")
	recheck.Flags().StringVar(&pick, "pick", "", "the card the owner just answered")
	recheck.Flags().StringVar(&said, "said", "", "what he picked or wrote on it")
	if err := recheck.MarkFlagRequired("lane"); err != nil {
		panic(err)
	}
	sweep := &cobra.Command{
		Use:   "sweep",
		Short: "Withdraw every card whose end has happened or that his ccn answers settle, judging only cards and answers newer than a card's last check, and name each card with no end",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			if err := board.Sweep(c.Context(), lane, c.OutOrStdout()); err != nil {
				return err
			}
			return board.Unask(c.Context(), lane, judge, c.OutOrStdout())
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
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			missing, err := board.Missing(c.Context(), args)
			if err != nil {
				return err
			}
			if len(missing) > 0 {
				fmt.Fprintln(c.OutOrStdout(), strings.Join(missing, "\n"))
			}
			return nil
		},
	}, &cobra.Command{
		Use:   "drawn CARD...",
		Short: "Print when each card was first drawn, stamping now for a card never drawn or drawn again after its withdrawal",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			drawn, err := board.Drawn(c.Context(), args)
			if err != nil {
				return err
			}
			for _, card := range args {
				fmt.Fprintln(c.OutOrStdout(), card, stamp(drawn[card]))
			}
			return nil
		},
	}, &cobra.Command{
		Use:   "ruled CARD_FILE",
		Short: "Print each rule this card asks him to lift or restate whose end has passed, each answer or Slack line of his on its question, then each order of his that removes something and shares words with its title, newest first, then a WARNING line for each source that was not read",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			board, done, err := cards()
			if err != nil {
				return nil
			}
			defer done()
			data, err := os.ReadFile(args[0])
			if err != nil {
				return nil
			}
			var card struct {
				Title string `json:"title"`
			}
			if json.Unmarshal(data, &card) != nil {
				return nil
			}
			rulings, unread := board.Ruled(c.Context(), card.Title, string(data))
			for _, ruling := range rulings[:min(len(rulings), rulingsShown)] {
				fmt.Fprintln(c.OutOrStdout(), ruling.Line())
			}
			if unread != nil {
				fmt.Fprintf(c.OutOrStdout(), "WARNING %s; the card draws unchecked against it\n", strings.ReplaceAll(unread.Error(), "\n", "; "))
			}
			return nil
		},
	}, &cobra.Command{
		Use:   "check CARD_FILE|-",
		Short: "Refuse a card that asks what a card on his board already asks, asks him to grade a reading of his words, or that one of his ccn answers settles, naming that answer; print a WARNING and pass it when a check could not run",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			var data []byte
			if args[0] == "-" {
				data, err = io.ReadAll(c.InOrStdin())
			} else {
				data, err = os.ReadFile(args[0])
			}
			if err != nil {
				return err
			}
			refused, err := board.Check(c.Context(), judge, data)
			if refused != nil {
				return refused
			}
			if err != nil {
				fmt.Fprintf(c.OutOrStdout(), "WARNING the card draws unchecked against his answers: %s\n", strings.ReplaceAll(err.Error(), "\n", "; "))
			}
			return nil
		},
	}, sweep, recheck)
	return cmd
}

func packJudge(runner shell.Runner) Judge {
	return func(ctx context.Context, kind, evidence, artifact string) (*Judgment, error) {
		dir, err := os.MkdirTemp("", "lr-judge-")
		if err != nil {
			return nil, err
		}
		defer func() { _ = os.RemoveAll(dir) }()
		job, err := json.Marshal(map[string]string{"kind": kind, "evidence": evidence, "artifact": artifact})
		if err != nil {
			return nil, err
		}
		if err := os.WriteFile(filepath.Join(dir, "job.json"), job, 0o600); err != nil {
			return nil, err
		}
		config, err := drive.ConfigDir()
		if err != nil {
			return nil, err
		}
		if _, err := runner.Run(ctx, shell.Command{Argv: []string{filepath.Join(config, "long-running", "current", "bin", "judge_job.py"), dir}, Timeout: judgeTimeout}); err != nil {
			return nil, err
		}
		data, err := os.ReadFile(filepath.Join(dir, "result.json"))
		if err != nil {
			return nil, err
		}
		var result struct {
			Verdict *Judgment `json:"verdict"`
		}
		if err := json.Unmarshal(data, &result); err != nil {
			return nil, fmt.Errorf("the judge's result: %w", err)
		}
		return result.Verdict, nil
	}
}
