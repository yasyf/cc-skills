package board

import (
	"fmt"
	"io"
	"maps"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	redKind   = "red"
	redWindow = 7 * 24 * time.Hour
	unclassed = "unclassed"
)

type Red struct {
	Number int
	Head   string
	Checks string
	Class  string
	Owner  string
	Author string
	Action string
	Since  time.Time
}

func (r Red) Line() string {
	since := "an unread time"
	if !r.Since.IsZero() {
		since = pacific(r.Since)
	}
	action := r.Action
	if action == "" {
		action = "no action recorded"
	}
	owner := "owner " + r.Owner
	if r.Author != "" {
		owner += ", by " + r.Author
	}
	return fmt.Sprintf("#%d %s | %s | %s | %s | red since %s | %s", r.Number, r.Head[:min(prrow.ShortestRedHead, len(r.Head))], r.Checks, r.Class, owner, since, action)
}

func Reds(snapshot map[int]machine.Facts, classes []client.Record, since func(machine.Facts) time.Time) []Red {
	var reds []Red
	for _, number := range slices.Sorted(maps.Keys(snapshot)) {
		f := snapshot[number]
		if f.CI != machine.CIFailure || f.Foreign() {
			continue
		}
		red := Red{Number: number, Head: f.Head, Checks: strings.Join(f.Failing, ", "), Class: unclassed, Owner: f.Lane, Author: f.ForeignAuthor, Since: since(f)}
		if red.Checks == "" {
			red.Checks = "a required check"
		}
		for _, record := range slices.Backward(classes) {
			class, ok := prrow.ParseRedClass(record.Text)
			if !ok || !strings.HasPrefix(f.Head, class.Head) {
				continue
			}
			red.Class, red.Action = class.Class, class.Action
			if class.Owner != "" {
				red.Owner = class.Owner
			}
			break
		}
		reds = append(reds, red)
	}
	return reds
}

func redsCommand(a *app.App, opened func() (drive.Entry, string, error)) *cobra.Command {
	return &cobra.Command{
		Use:   "reds",
		Short: "One line per open PR whose build reads red: head, failing checks, class, owner, red since, last action",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
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
			classes, err := client.Tail(c.Context(), client.Query{Drive: entry.Bus(), Since: a.Now().Add(-redWindow), Kinds: []string{redKind}})
			if err != nil {
				return err
			}
			var unread error
			reds := Reds(snapshot, classes, func(f machine.Facts) time.Time {
				items, err := s.OpenItems(repository, f.Number)
				if err != nil {
					unread = err
					return time.Time{}
				}
				for _, item := range items {
					if item.Kind == string(machine.ActionFixRed) && item.Head == f.Head {
						return item.Created
					}
				}
				return time.Time{}
			})
			if unread != nil {
				return unread
			}
			return printReds(c.OutOrStdout(), reds, a.Now())
		},
	}
}

func printReds(out io.Writer, reds []Red, now time.Time) error {
	open := 0
	for _, red := range reds {
		if red.Class == unclassed {
			open++
		}
	}
	if _, err := fmt.Fprintf(out, "reds at %s: %d open PRs read red, %d unclassed\n", pacific(now), len(reds), open); err != nil {
		return err
	}
	for _, red := range reds {
		if _, err := fmt.Fprintln(out, red.Line()); err != nil {
			return err
		}
	}
	return nil
}
