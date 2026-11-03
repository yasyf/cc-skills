package coordinator

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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	defaultEvery = 5 * time.Minute
	longestWait  = 570 * time.Second
	settle       = 2 * time.Second
	bodyShown    = 4000
	cutMark      = "…"
	stallWatch   = "stall-watch"
)

var pacific = mustZone("America/Los_Angeles")

func mustZone(name string) *time.Location {
	zone, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return zone
}

type record struct {
	Seq   int64
	Lane  string
	Kind  string
	At    time.Time
	Text  string
	Topic string
	To    []string
	Re    int64
	Refs  struct {
		Path string
	}
}

func Command(*app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "coordinator",
		Short: "What the drive's coordinator session runs instead of waking on every record",
		Args:  cobra.NoArgs,
	}
	cmd.AddCommand(nextCommand())
	return cmd
}

func nextCommand() *cobra.Command {
	var driveName, cursor string
	var every time.Duration
	cmd := &cobra.Command{
		Use:   "next",
		Short: "Block until the coordinator's next batch of records is due, print it, and advance its cursor",
		Long: `Block until the coordinator's next batch of records is due, print it, and advance its cursor.

Every kind addressed to the coordinator prints, holds first. The batch closes every --every. Anything from the communicator, an incident, a mechanism, a fix-live or an urgent closes it at once. A stall-watch urgent waits for the window, and repeats of one stall-watch alarm print as one line. A window with no records keeps waiting, for at most 570 s, so one Bash call of 590000 ms always covers it.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			since, err := readCursor(cursor)
			if err != nil {
				return err
			}
			ctx, stop := context.WithCancel(cmd.Context())
			defer stop()
			records, failure := follow(ctx, driveName, since)
			batch := collect(ctx, records, every, longestWait, settle)
			stop()
			printBatch(cmd.OutOrStdout(), batch)
			if err := saveCursor(cursor, batch); err != nil {
				return err
			}
			return <-failure
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", "", "the drive whose reader coordinator this session is")
	cmd.Flags().StringVar(&cursor, "cursor", "", "file holding the last seq printed; created on the first batch")
	cmd.Flags().DurationVar(&every, "every", defaultEvery, "how long one batch stays open")
	_ = cmd.MarkFlagRequired("drive")
	_ = cmd.MarkFlagRequired("cursor")
	return cmd
}

func readCursor(path string) (int64, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	return strconv.ParseInt(strings.TrimSpace(string(raw)), 10, 64)
}

func saveCursor(path string, batch []record) error {
	if len(batch) == 0 {
		return nil
	}
	return os.WriteFile(path, []byte(strconv.FormatInt(batch[len(batch)-1].Seq, 10)+"\n"), 0o600)
}

func follow(ctx context.Context, driveName string, since int64) (<-chan record, <-chan error) {
	records := make(chan record)
	failure := make(chan error, 1)
	go func() {
		defer close(records)
		err := client.Follow(ctx, client.Watch{Drive: driveName, Reader: lanes.Root, After: since, Kinds: []string{"incident"}}, func(r client.Record) error {
			next := record{Seq: r.Seq, Lane: r.Lane, Kind: string(r.Kind), At: r.At, Text: r.Text, Topic: r.Topic, To: r.To, Re: r.Re}
			next.Refs.Path = r.Refs.Path
			select {
			case records <- next:
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
	return records, failure
}

func collect(ctx context.Context, records <-chan record, every, limit, soon time.Duration) []record {
	var batch []record
	due := time.After(every)
	giveUp := time.After(limit)
	for {
		select {
		case r, open := <-records:
			if !open {
				return batch
			}
			batch = append(batch, r)
			if due == nil || closesAtOnce(r) {
				due = time.After(soon)
			}
		case <-due:
			if len(batch) > 0 {
				return batch
			}
			due = nil
		case <-giveUp:
			return batch
		case <-ctx.Done():
			return batch
		}
	}
}

func closesAtOnce(r record) bool {
	if r.Lane == lanes.OwnerRelay || r.Lane == lanes.Root {
		return true
	}
	switch r.Kind {
	case "incident", "mechanism", "fix-live":
		return true
	case "urgent":
		return r.Lane != stallWatch
	}
	return false
}

type alarm struct {
	kind, topic string
}

type line struct {
	record
	seen int
}

func fold(batch []record) []line {
	lines := make([]line, 0, len(batch))
	at := map[alarm]int{}
	for _, r := range batch {
		if r.Lane != stallWatch || r.Topic == "" {
			lines = append(lines, line{r, 1})
			continue
		}
		key := alarm{r.Kind, r.Topic}
		if i, seen := at[key]; seen {
			lines[i] = line{r, lines[i].seen + 1}
			continue
		}
		at[key] = len(lines)
		lines = append(lines, line{r, 1})
	}
	return lines
}

func printBatch(w io.Writer, batch []record) {
	if len(batch) == 0 {
		_, _ = fmt.Fprintln(w, "no records; run lr coordinator next again")
		return
	}
	lines := fold(batch)
	for _, holds := range []bool{true, false} {
		for _, l := range lines {
			if (l.Kind == "hold") == holds {
				_, _ = fmt.Fprintln(w, l.render())
			}
		}
	}
}

func (l line) render() string {
	var out strings.Builder
	fmt.Fprintf(&out, "#%d %s %s %s", l.Seq, l.At.In(pacific).Format("3:04 PM"), strings.ToUpper(l.Kind), l.Lane)
	if l.Re != 0 {
		fmt.Fprintf(&out, " re #%d", l.Re)
	}
	if others := l.alsoTo(); len(others) > 0 {
		fmt.Fprintf(&out, " -> %s", strings.Join(others, ","))
	}
	fmt.Fprintf(&out, ": %s", l.whole())
	if l.seen > 1 {
		fmt.Fprintf(&out, " (seen %d times)", l.seen)
	}
	return out.String()
}

func (r record) whole() string {
	if r.Refs.Path == "" {
		return r.Text
	}
	raw, err := os.ReadFile(r.Refs.Path)
	if err != nil {
		return fmt.Sprintf("%s (body %s)", r.Text, r.Refs.Path)
	}
	if len(raw) > bodyShown {
		return fmt.Sprintf("%s (body %s, %d bytes)", r.Text, r.Refs.Path, len(raw))
	}
	body := strings.TrimSpace(string(raw))
	if strings.HasPrefix(body, strings.TrimSuffix(r.Text, cutMark)) {
		return body
	}
	return r.Text + "\n" + body
}

func (r record) alsoTo() []string {
	var others []string
	for _, to := range r.To {
		if name, _, _ := strings.Cut(to, "@"); name != lanes.Root {
			others = append(others, to)
		}
	}
	return others
}
