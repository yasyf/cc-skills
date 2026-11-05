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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
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
	reportsRead    = 24 * time.Hour
	reportStamp    = "2006-01-02T15:04:05.000Z"
	reportBytes    = 4096
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

func laneReports(ctx context.Context, bus string, now time.Time) ([]ownerAnswer, error) {
	found, err := client.Tail(ctx, client.Query{Drive: bus, Since: now.Add(-reportsRead), Kinds: []string{"done", "landed", "report"}})
	if err != nil {
		return nil, err
	}
	reports := make([]ownerAnswer, 0, len(found))
	for _, record := range found {
		reports = append(reports, ownerAnswer{ID: strconv.FormatInt(record.Seq, 10), Title: fmt.Sprintf("%s record from %s", record.Kind, record.Lane), Body: reportBody(record.Text, record.Refs.Path), Updated: record.At.UTC().Format(reportStamp)})
	}
	return reports, nil
}

func reportBody(text, path string) string {
	file, err := os.Open(path)
	if err != nil {
		return text
	}
	defer func() { _ = file.Close() }()
	head, err := io.ReadAll(io.LimitReader(file, reportBytes))
	if err != nil {
		return text
	}
	return string(head)
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
	unstored := func() (Cards, error) {
		entry, board, err := opened()
		return CardsOf(a, entry, board, nil), err
	}
	cmd.AddCommand(wakeCommand(a, cards), waitsCommand(a, opened), redsCommand(a, opened), cardsCommand(cards, unstored, PackJudge(a.Runner)), rowsCommand(opened), portsCommand(opened), backfillCommand(a, opened), classifyCommand(), slackCardCommand(a), linkCommand(a, opened, PackJudge(a.Runner)), picksCommand(opened))
	return cmd
}

func CardsOf(a *app.App, entry drive.Entry, session string, s *store.Store) Cards {
	program := entry.Program
	if program == "" {
		program = filepath.Base(entry.StateDir)
	}
	return Cards{
		Runner: a.Runner, Ledger: ledger.CLI{Runner: a.Runner, Repo: entry.Checkout}, CCX: a.CCXFrom(entry.Checkout),
		Bus: entry.Bus(), Checkout: entry.Checkout, Session: session, StateDir: entry.StateDir, Program: program, Store: s, Now: a.Now,
		Closed: func(ctx context.Context, seqs []string) (map[string]bool, error) {
			return closedRecords(ctx, entry.Bus(), seqs)
		},
		Reports: func(ctx context.Context) ([]ownerAnswer, error) {
			return laneReports(ctx, entry.Bus(), a.Now())
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

func explainPRs(c *cobra.Command, live Live, stored []store.ReviewRow, prs []string, asJSON bool) error {
	rows := make(map[int]store.ReviewRow, len(stored))
	for _, row := range stored {
		rows[row.Number] = row
	}
	found := make([]PRWait, len(prs))
	for i, named := range prs {
		pr, err := strconv.Atoi(strings.TrimPrefix(named, "#"))
		if err != nil {
			return fmt.Errorf("--pr %q: %w", named, err)
		}
		found[i] = live.Explain(pr, rows)
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
			return posts.PostCardTap(ctx, repository, bus.Tap{PR: tap.PR, Head: tap.Head, By: tap.By, Value: string(tap.Value)})
		},
		Refuse: func(tap TapRecord, reason string) error {
			return posts.PostRefusal(ctx, repository, bus.Tap{PR: tap.PR, Head: tap.Head, By: tap.By, Value: string(tap.Value)}, reason)
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

func waitsCommand(a *app.App, opened func() (drive.Entry, string, error)) *cobra.Command {
	var asJSON bool
	var prs []string
	waits := &cobra.Command{
		Use:   "waits",
		Short: "One line per PR row ready for his tap and each pick he has not answered, or with --pr what each PR waits on",
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
			record, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = record.Close() }()
			rows, err := record.ReviewRows(entry.Repo)
			if err != nil {
				return err
			}
			live, err := LiveOf(outcomes, snapshots, rows)
			if err != nil {
				return err
			}
			if len(prs) > 0 {
				return explainPRs(c, live, rows, prs, asJSON)
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

func cardsCommand(cards func() (Cards, func(), error), unstored func() (Cards, error), judge Judge) *cobra.Command {
	var until, owner, lane, pick, said string
	var on, paths []string
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
			end, err := board.Until(c.Context(), args[0], until, owner, paths...)
			if err != nil {
				return err
			}
			if DeskLane.MatchString(owner) {
				fmt.Fprintf(c.ErrOrStderr(), "WARNING %s is a desk: pass --lane with the lane whose ask drew %s, so his answer wakes it\n", owner, args[0])
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
	record.Flags().StringVar(&owner, "lane", os.Getenv("CLAUDE_LONG_RUNNING_LANE"), "the lane whose ask drew the card, woken by his answer and told when a ruling may change it; a desk drawing for another lane names that lane")
	record.Flags().StringArrayVar(&on, "on", nil, "an object or card:ID the card rests on beyond the PRs and register names it cites, repeatable")
	record.Flags().StringArrayVar(&paths, "path", nil, "a repo-relative path prefix of the subsystem a design card decides, where a drive lane's PR waits for his pick, repeatable")
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
		Short: "Withdraw every card whose end has happened or that his tap, numbered reply or a relay's record of his words on it answers, once the board's hold on a card he touched has passed, tell the drawing lane of any other answer or report that reads as settling a card, judging only cards and answers newer than a card's last check, and name each card with no end",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			return board.Pass(c.Context(), lane, judge, c.OutOrStdout())
		},
	}
	sweep.Flags().StringVar(&lane, "lane", "", "the lane the withdraw records post as")
	if err := sweep.MarkFlagRequired("lane"); err != nil {
		panic(err)
	}
	cmd.AddCommand(record, &cobra.Command{
		Use:   "paths",
		Short: "Print as JSON each open card in the drive's card ledger with its design paths, the prefixes where a drive lane's PR waits for his pick",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			board, err := unstored()
			if err != nil {
				return err
			}
			gated, err := board.Paths(c.Context())
			if err != nil {
				return err
			}
			return json.NewEncoder(c.OutOrStdout()).Encode(gated)
		},
	}, &cobra.Command{
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
		Short: "Refuse a card whose background or Explain more is only a link, that asks what a card on his board already asks, asks him to grade a reading of his words, or that one of his ccn answers settles, naming that answer, where a card showing an image settles only on his answer to that card; print a WARNING and pass it when a check could not run or an option acts on a set it does not name",
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
			warnUnnamedProcess(c.OutOrStdout(), data)
			if unnamed := board.UnnamedSet(c.Context(), judge, data); unnamed != "" {
				fmt.Fprintf(c.OutOrStdout(), "WARNING %s\n", unnamed)
			}
			if steps := cardtext.StepsForHim(data); steps != "" {
				fmt.Fprintf(c.OutOrStdout(), "WARNING this card hands him steps (%q). An agent does sign-ins and browser steps itself with cookiesync, through the agent-browser-with-cookies skill: his login, one Touch ID tap. Do them, and card only what only he can decide.\n", steps)
			}
			return nil
		},
	}, sweep, recheck)
	return cmd
}

func warnUnnamedProcess(w io.Writer, card []byte) {
	if asked := cardtext.EndsAnUnnamedProcess(card); asked != "" {
		fmt.Fprintf(w, "WARNING this card asks to end or restart a process (%q) and names no pid or script. Put \"pid <number>\" or the script's file name, and the lane that ends it, in the card's words: his tap lifts the process guard only when they name them, and otherwise he has to type it himself.\n", asked)
	}
}

func PackJudge(runner shell.Runner) Judge {
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
