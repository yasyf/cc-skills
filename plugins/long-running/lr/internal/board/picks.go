package board

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	picksEvery  = time.Minute
	picksFor    = 30 * time.Minute
	picksSettle = 2 * time.Second
	ownerKind   = "owner"
	submitEvent = "submit"
)

func picksCommand(opened func() (drive.Entry, string, error)) *cobra.Command {
	var cursor string
	var every, lasting time.Duration
	cmd := &cobra.Command{
		Use:   "picks",
		Short: "Follow the owner's taps on the board and print them as one batch a minute, for a desk that starts work on each wake",
		Long: `Follow the owner's taps on the board and print them as one batch a minute, for a desk that starts work on each wake.

A pick after a quiet minute prints within two seconds, so the rows of one tap stay together; picks after it wait for the minute to end and print as one batch. It reads board-watch's owner records, so every other reader of those records still gets each tap the moment it arrives. It exits 0 after --for, printing what it holds, and --cursor resumes the next run from the last seq printed; with no cursor file it starts at the newest record.`,
		Args: cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, board, err := opened()
			if err != nil {
				return err
			}
			after, err := picksCursor(cursor)
			if err != nil {
				return err
			}
			ctx, stop := context.WithTimeout(c.Context(), lasting)
			defer stop()
			records := make(chan client.Record)
			failure := make(chan error, 1)
			go func() {
				defer close(records)
				err := client.Follow(ctx, client.Watch{Drive: entry.Bus(), After: after, Kinds: []string{ownerKind}}, func(record client.Record) error {
					if !boardPick(record, board) {
						return nil
					}
					select {
					case records <- record:
						return nil
					case <-ctx.Done():
						return ctx.Err()
					}
				})
				if ctx.Err() != nil {
					err = nil
				}
				failure <- err
			}()
			err = batchPicks(ctx, records, every, func(batch []client.Record) error {
				if _, err := io.WriteString(c.OutOrStdout(), picksText(batch)); err != nil {
					return err
				}
				return os.WriteFile(cursor, []byte(strconv.FormatInt(batch[len(batch)-1].Seq, 10)+"\n"), 0o600)
			})
			stop()
			return errors.Join(err, <-failure)
		},
	}
	cmd.Flags().StringVar(&cursor, "cursor", "", "file holding the last seq printed; created by the first batch")
	cmd.Flags().DurationVar(&every, "every", picksEvery, "the least time between two batches")
	cmd.Flags().DurationVar(&lasting, "for", picksFor, "how long to follow before exiting 0")
	_ = cmd.MarkFlagRequired("cursor")
	return cmd
}

func picksCursor(path string) (int64, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	return strconv.ParseInt(strings.TrimSpace(string(raw)), 10, 64)
}

func boardPick(record client.Record, session string) bool {
	attrs := record.Fields.Attrs
	return record.Lane == boardWatch && attrs["session"] == session && attrs["event"] != submitEvent
}

func batchPicks(ctx context.Context, records <-chan client.Record, every time.Duration, emit func([]client.Record) error) error {
	var held []client.Record
	var due <-chan time.Time
	flush := func() error {
		if len(held) == 0 {
			return nil
		}
		defer func() { held = nil }()
		return emit(held)
	}
	for {
		select {
		case record, open := <-records:
			if !open {
				return flush()
			}
			held = append(held, record)
			if due == nil {
				due = time.After(picksSettle)
			}
		case <-due:
			if len(held) == 0 {
				due = nil
				continue
			}
			if err := flush(); err != nil {
				return err
			}
			due = time.After(every)
		case <-ctx.Done():
			return flush()
		}
	}
}

func picksText(batch []client.Record) string {
	var out strings.Builder
	fmt.Fprintf(&out, "BOARD PICKS %d\n", len(batch))
	for _, record := range batch {
		fmt.Fprintf(&out, "%s %s %s\n", record.Name(), pacific(record.At), strings.Join(strings.Fields(record.Text), " "))
	}
	return out.String()
}
