package board

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	lrfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const rowsRead = "board rows"

type rowsQuery struct {
	json, all, outside bool
}

func (q *rowsQuery) flags(fs *pflag.FlagSet) {
	fs.BoolVar(&q.json, "json", false, "print the rows as one JSON array")
	fs.BoolVar(&q.all, "all", false, "include landed, closed and superseded rows; an old-tree row whose port is owed is listed without it")
	fs.BoolVar(&q.outside, "outside", false, "list the old-tree PRs no drive lane opened in place of the drive's rows, each with its port; without --all, only those whose port is owed")
}

func (q rowsQuery) ask() string {
	read := []string{rowsRead}
	for _, flag := range []struct {
		name string
		on   bool
	}{{"--json", q.json}, {"--all", q.all}, {"--outside", q.outside}} {
		if flag.on {
			read = append(read, flag.name)
		}
	}
	return strings.Join(read, " ")
}

func rowsCommand(opened func() (drive.Entry, string, error)) *cobra.Command {
	var q rowsQuery
	cmd := &cobra.Command{
		Use:   "rows",
		Short: "Every drive PR from the landing store with all its fields and what it waits on: the one record the review tab, status lines and asks read",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
			if err != nil {
				return err
			}
			if entry.OnSprite() {
				return askdesk.AskMac(c.Context(), askdesk.LiveCCI{}, entry.Bus(), os.Getenv(lanes.LaneEnv), q.ask(), c.OutOrStdout())
			}
			return printRows(entry, q, c.OutOrStdout(), c.ErrOrStderr())
		},
	}
	q.flags(cmd.Flags())
	return cmd
}

func RowsRead(driveName string) askdesk.Serve {
	return func(_ context.Context, args []string, out, errs io.Writer) error {
		var q rowsQuery
		parsed := pflag.NewFlagSet(rowsRead, pflag.ContinueOnError)
		parsed.SetOutput(io.Discard)
		q.flags(parsed)
		if err := parsed.Parse(args); err != nil {
			return err
		}
		if parsed.NArg() != 0 {
			return errors.New(rowsRead + " takes no arguments")
		}
		entry, err := drive.Named(driveName)
		if err != nil {
			return err
		}
		return printRows(entry, q, out, errs)
	}
}

func printRows(entry drive.Entry, q rowsQuery, out, errs io.Writer) error {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), errs)
	if err != nil {
		return err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	if q.outside {
		return printOutside(out, s, cfg.Repo, q.json, q.all)
	}
	rows, err := s.ReviewRows(cfg.Repo)
	if err != nil {
		return err
	}
	if !q.all {
		rows = slices.DeleteFunc(rows, func(row store.ReviewRow) bool {
			return (prrow.Terminal(row.State) || row.State == prrow.StateSuperseded) && !strings.HasPrefix(row.Port, prrow.PortOwed)
		})
	}
	if q.json {
		return json.NewEncoder(out).Encode(rows)
	}
	for _, row := range rows {
		port := ""
		if row.Port != prrow.None {
			port = "; port: " + row.Port
		}
		fmt.Fprintf(out, "#%d at %s (%s, %s): %s: %s%s\n", row.Number, abbreviate(row.Head), row.Lane, row.State, row.Title, row.WaitsOn, port)
	}
	return nil
}

func printOutside(out io.Writer, s *store.Store, repo string, asJSON, all bool) error {
	admitted, err := s.OutsidePRs(repo)
	if err != nil {
		return err
	}
	prs := slices.SortedFunc(maps.Values(admitted), func(a, b store.OutsidePR) int { return a.Number - b.Number })
	if !all {
		prs = slices.DeleteFunc(prs, func(pr store.OutsidePR) bool { return !strings.HasPrefix(pr.Port, prrow.PortOwed) })
	}
	if asJSON {
		return json.NewEncoder(out).Encode(prs)
	}
	for _, pr := range prs {
		fmt.Fprintf(out, "#%d at %s (%s): %s; port: %s\n", pr.Number, abbreviate(pr.Head), pr.State, pr.Title, pr.Port)
	}
	return nil
}

func portsCommand(opened func() (drive.Entry, string, error)) *cobra.Command {
	return &cobra.Command{
		Use:   "ports PR...",
		Short: "Print one JSON object from each named drive PR that is a forward port to the old-tree PR it copies, as its head records say",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			numbers := make([]int, len(args))
			for at, arg := range args {
				number, err := strconv.Atoi(arg)
				if err != nil {
					return err
				}
				numbers[at] = number
			}
			entry, _, err := opened()
			if err != nil {
				return err
			}
			_, ports, err := Ports(entry, numbers)
			if err != nil {
				return err
			}
			return json.NewEncoder(c.OutOrStdout()).Encode(ports)
		},
	}
}

func backfillCommand(a *app.App, opened func() (drive.Entry, string, error)) *cobra.Command {
	return &cobra.Command{
		Use:   "backfill",
		Short: "Post one opened record, from its PR body, for each drive PR open now that a lane owns or a record opened; run it once",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
			if err != nil {
				return err
			}
			cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), c.ErrOrStderr())
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			posted, err := lrfacts.New(a, cfg, s).Backfill(c.Context())
			if err != nil {
				return err
			}
			fmt.Fprintf(c.OutOrStdout(), "posted %d opened records\n", posted)
			return nil
		},
	}
}

func readCommand(a *app.App, opened func() (drive.Entry, string, error)) *cobra.Command {
	return &cobra.Command{
		Use:   "read [PR...]",
		Short: "Read GitHub for every open drive PR, or only the PRs named, in one query per 25 and post each read the rows fold, from any host and beside a running landing host; it opens, queues and merges nothing",
		RunE: func(c *cobra.Command, args []string) error {
			named := make([]int, len(args))
			for at, arg := range args {
				number, err := strconv.Atoi(strings.TrimPrefix(arg, "#"))
				if err != nil {
					return err
				}
				named[at] = number
			}
			entry, _, err := opened()
			if err != nil {
				return err
			}
			cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), c.ErrOrStderr())
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			read, err := lrfacts.New(a, cfg, s).ReadOnce(c.Context(), named)
			if err != nil {
				return err
			}
			fmt.Fprintf(c.OutOrStdout(), "read %d open PRs\n", read)
			return nil
		},
	}
}
