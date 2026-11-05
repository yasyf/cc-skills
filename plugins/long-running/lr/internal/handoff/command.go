package handoff

import (
	"cmp"
	"context"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/livetasks"
)

const (
	rootLane    = "root"
	handoffLane = "lr-handoff"
	laneEnv     = "CLAUDE_LONG_RUNNING_LANE"
	blockerKind = "blocker"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "handoff",
		Short: "Generate and lint a drive's compaction handoff",
		Args:  cobra.NoArgs,
	}
	cmd.AddCommand(generateCommand(a), lintCommand(a), standingCommand(a), compactCommand(a), liveTasksCommand(a))
	return cmd
}

func liveTasksCommand(a *app.App) *cobra.Command {
	var transcript string
	cmd := &cobra.Command{
		Use:   "live-tasks",
		Short: "List the Monitors and background tasks a session's transcript shows started and never ended, each with the call that starts it again",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			tasks, err := livetasks.Read(transcript, a.Now())
			if err != nil {
				return err
			}
			for _, task := range tasks {
				fmt.Fprintln(cmd.OutOrStdout(), task.Line())
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&transcript, "transcript", "", "the session's transcript")
	_ = cmd.MarkFlagRequired("transcript")
	return cmd
}

func depsOf(a *app.App, cmd *cobra.Command) (*deps, error) {
	claude, err := configDir()
	if err != nil {
		return nil, err
	}
	return &deps{
		runner:  a.Runner,
		bin:     filepath.Join(claude, "long-running", "current", "bin"),
		ledger:  a.Ledger,
		records: tailRecords,
		blocker: postingBlockers(a.Log),
		now:     a.Now,
		stdin:   cmd.InOrStdin(),
		stdout:  cmd.OutOrStdout(),
	}, nil
}

func tailRecords(ctx context.Context, q recordQuery) ([]busRecord, error) {
	rows, err := client.Tail(ctx, client.Query{Drive: q.drive, Since: q.since, Kinds: q.kinds})
	if err != nil {
		return nil, err
	}
	records := make([]busRecord, 0, len(rows))
	for _, row := range rows {
		records = append(records, busRecord{
			Seq:   row.Seq,
			Drive: row.Drive,
			Lane:  row.Lane,
			Kind:  string(row.Kind),
			At:    row.At,
			Text:  row.Text,
			Topic: row.Topic,
			Re:    row.Re,
			CCN:   row.Refs.CCN,
		})
	}
	return records, nil
}

func postingBlockers(log *slog.Logger) blockerPoster {
	return func(ctx context.Context, drive, id, text string) {
		draft := client.Draft{Drive: drive, Lane: cmp.Or(os.Getenv(laneEnv), handoffLane), Kind: blockerKind, ID: id, To: []string{rootLane}, Text: text}
		if _, err := client.Post(ctx, draft); err != nil {
			log.Warn("the handoff blocker did not post", "drive", drive, "id", id, "error", err)
		}
	}
}

func exitWith(code int, err error) error {
	if err != nil {
		return err
	}
	if code != 0 {
		os.Exit(code)
	}
	return nil
}

func generateCommand(a *app.App) *cobra.Command {
	var opts generateOptions
	cmd := &cobra.Command{
		Use:   "generate",
		Short: "Write the generated progress doc and print its id, file, and restore digest",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			d, err := depsOf(a, cmd)
			if err != nil {
				return err
			}
			return exitWith(d.generate(cmd.Context(), opts))
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&opts.program, "program", "", "the drive's program slug")
	flags.StringVar(&opts.plan, "plan", "", "the plan's path")
	flags.StringVar(&opts.inboxDir, "inbox-dir", "", "the inbox directory (default $LONG_RUNNING_SCRATCH_ROOT or ~/.claude/scratch, then <program>/inbox)")
	flags.StringVar(&opts.ledger, "ledger", "", "the ledger id (default the drive registry's ledger for the session)")
	flags.StringVar(&opts.session, "session", "", "the hook's JSON, - for stdin: session_id, tasks, background")
	flags.BoolVar(&opts.strict, "strict", false, "write nothing when the narrative fails the lint")
	flags.BoolVar(&opts.folder, "folder", false, "no cc-notes: write the progress file alone")
	flags.StringVar(&opts.repo, "repo", ".", "the repository holding the drive's notes")
	mustRequire(cmd, "program", "plan")
	return cmd
}

func lintCommand(a *app.App) *cobra.Command {
	var opts lintOptions
	cmd := &cobra.Command{
		Use:   "lint",
		Short: "Print standing-rule and owner-gate findings for a handoff and its plan",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			d, err := depsOf(a, cmd)
			if err != nil {
				return err
			}
			return exitWith(d.lint(cmd.Context(), opts))
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&opts.doc, "doc", "", "the progress doc to lint")
	flags.StringVar(&opts.file, "file", "", "the progress file to lint")
	flags.StringVar(&opts.previousDoc, "previous-doc", "", "the previous handoff's doc")
	flags.StringVar(&opts.previousFile, "previous-file", "", "the previous handoff's file")
	flags.StringVar(&opts.program, "program", "", "the drive's program slug")
	flags.StringVar(&opts.plan, "plan", "", "the plan's path")
	flags.StringVar(&opts.repo, "repo", ".", "the repository holding the drive's notes")
	mustRequire(cmd, "program")
	cmd.MarkFlagsOneRequired("doc", "file")
	cmd.MarkFlagsMutuallyExclusive("doc", "file")
	cmd.MarkFlagsMutuallyExclusive("previous-doc", "previous-file")
	return cmd
}

func standingCommand(a *app.App) *cobra.Command {
	var drive string
	cmd := &cobra.Command{
		Use:   "standing",
		Short: "Print the live standing rules on a cci drive",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			d, err := depsOf(a, cmd)
			if err != nil {
				return err
			}
			return d.standing(cmd.Context(), drive)
		},
	}
	cmd.Flags().StringVar(&drive, "drive", "", "the cci drive")
	mustRequire(cmd, "drive")
	return cmd
}

func compactCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "compact <terminal> <text> <transcript>",
		Short: "Send text to a terminal at its first idle prompt, unless the session compacts first",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			return compactJob{runner: a.Runner, now: a.Now, sleep: time.Sleep}.run(cmd.Context(), args[0], args[1], args[2])
		},
	}
}

func mustRequire(cmd *cobra.Command, names ...string) {
	for _, name := range names {
		if err := cmd.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
}
