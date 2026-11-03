package coordinator

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	defaultEvery = 5 * time.Minute
	longestWait  = 570 * time.Second
	settle       = 2 * time.Second
	killGrace    = 5 * time.Second
	maxRecord    = 16 << 20
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
	Seq   int64     `json:"seq"`
	Lane  string    `json:"lane"`
	Kind  string    `json:"kind"`
	At    time.Time `json:"at"`
	Text  string    `json:"text"`
	Topic string    `json:"topic"`
	To    []string  `json:"to"`
	Re    int64     `json:"re"`
	Refs  struct {
		Path string `json:"path"`
	} `json:"refs"`
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

The batch closes every --every. Anything from the communicator, an incident, a mechanism, a fix-live or an urgent closes it at once. A stall-watch urgent waits for the window, and repeats of one stall-watch alarm print as one line. A window with no records keeps waiting, for at most 570 s, so one Bash call of 590000 ms always covers it.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			since, err := readCursor(cursor)
			if err != nil {
				return err
			}
			ctx, stop := context.WithCancel(cmd.Context())
			defer stop()
			records, failure, err := follow(ctx, driveName, since)
			if err != nil {
				return err
			}
			batch := collect(ctx, records, every, longestWait, settle)
			stop()
			printBatch(cmd.OutOrStdout(), batch)
			if err := saveCursor(cursor, batch); err != nil {
				return err
			}
			return <-failure
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", "", "the drive whose reader root this session is")
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

func follow(ctx context.Context, driveName string, since int64) (<-chan record, <-chan error, error) {
	argv := []string{"watch", "--drive", driveName, "--reader", lanes.Root, "--kind", "incident", "--json", "--for", (longestWait + time.Minute).String()}
	if since > 0 {
		argv = append(argv, "--since", strconv.FormatInt(since, 10))
	}
	cmd := exec.CommandContext(ctx, "cci", argv...)
	cmd.Stderr = os.Stderr
	cmd.WaitDelay = killGrace
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, nil, err
	}
	records := make(chan record)
	failure := make(chan error, 1)
	go func() {
		bad := read(ctx, stdout, records)
		err := cmd.Wait()
		if ctx.Err() != nil {
			err = nil
		}
		failure <- errors.Join(bad, err)
		close(records)
	}()
	return records, failure, nil
}

func read(ctx context.Context, stdout io.Reader, records chan<- record) error {
	lines := bufio.NewScanner(stdout)
	lines.Buffer(nil, maxRecord)
	var bad []error
	for lines.Scan() {
		var r record
		if err := json.Unmarshal(lines.Bytes(), &r); err != nil {
			bad = append(bad, fmt.Errorf("cci watch printed %q: %w", lines.Text(), err))
			continue
		}
		select {
		case records <- r:
		case <-ctx.Done():
		}
	}
	return errors.Join(append(bad, lines.Err())...)
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
	for _, l := range fold(batch) {
		_, _ = fmt.Fprintln(w, l.render())
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
	fmt.Fprintf(&out, ": %s", l.Text)
	if l.Refs.Path != "" {
		fmt.Fprintf(&out, " (body %s)", l.Refs.Path)
	}
	if l.seen > 1 {
		fmt.Fprintf(&out, " (seen %d times)", l.seen)
	}
	return out.String()
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
