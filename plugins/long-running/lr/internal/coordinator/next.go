package coordinator

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	defaultEvery = 5 * time.Minute
	longestWait  = 570 * time.Second
	settle       = 2 * time.Second
	bodyShown    = 4000
	cutMark      = "…"
	stallWatch   = "stall-watch"
	cursorFile   = "next.cursor"
	kindHold     = "hold"
	kindLanded   = "landed"
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
	Num   string
	Lane  string
	Kind  string
	At    time.Time
	Text  string
	Topic string
	To    []string
	Re    int64
	ReNum string
	PRs   []int
	Refs  struct {
		Path string
	}
}

func (r record) Name() string {
	return client.Record{Seq: r.Seq, Num: r.Num}.Name()
}

func (r record) ReName() string {
	return client.Record{Re: r.Re, ReNum: r.ReNum}.ReName()
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "coordinator",
		Short: "What the drive's coordinator session runs instead of waking on every record",
		Args:  cobra.NoArgs,
	}
	cmd.AddCommand(nextCommand(a), muteCommand())
	return cmd
}

func nextCommand(a *app.App) *cobra.Command {
	var driveName, cursor string
	var every time.Duration
	var shape view
	cmd := &cobra.Command{
		Use:   "next",
		Short: "Block until the coordinator's next batch of records is due, print it, and advance its cursor",
		Long: `Block until the coordinator's next batch of records is due, print it, and advance its cursor.

Every kind addressed to the coordinator prints, holds first. The batch closes every --every. Anything from the communicator, an incident, a mechanism, a fix-live or an urgent closes it at once. A stall-watch urgent waits for the window, and repeats of one stall-watch alarm print as one line. A window with no records keeps waiting, for at most 570 s, so one Bash call of 590000 ms always covers it.

The drive defaults to this session's and the cursor to <state dir>/root/next.cursor. The flags shape the batch for one read: --width cuts a record at that many bytes and names the read that shows the rest, --lines stops at that many lines with holds and what cannot wait first, --full prints whole bodies of a kind up to 32000 bytes, and --watch closes with each pull request's queue state and its LANDED and OVERRIDE-WARNING records. Records lr coordinator mute hides are counted in a muted line and do not end a window by themselves. Whatever is muted or cut stays in batch-last.txt beside the cursor, and the batch names that file.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if driveName == "" || cursor == "" || len(shape.watch) > 0 {
				entry, err := drive.Named(driveName)
				if err != nil {
					return err
				}
				driveName = cmp.Or(driveName, entry.Bus())
				cursor = cmp.Or(cursor, filepath.Join(entry.StateDir, lanes.Root, cursorFile))
				a.ConfigPath = cmp.Or(a.ConfigPath, entry.Config())
			}
			kept := filepath.Dir(cursor)
			since, err := readCursor(cmd.Context(), driveName, cursor)
			if err != nil {
				return err
			}
			if shape.mutes, err = loadMutes(filepath.Join(kept, mutesFile)); err != nil {
				return err
			}
			ctx, stop := context.WithCancel(cmd.Context())
			defer stop()
			records, failure := follow(ctx, driveName, since)
			batch := collect(ctx, records, every, longestWait, settle, shape.wakes)
			stop()
			statuses, unread := watched(cmd.Context(), a, shape.watch, batch)
			printing := shape.print(cmd.OutOrStdout(), batch, statuses, filepath.Join(kept, wholeBatchFile), a.Now())
			return errors.Join(printing, saveCursor(cursor, batch), unread, <-failure)
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", "", "the drive whose reader coordinator this session is; default this session's drive")
	cmd.Flags().StringVar(&cursor, "cursor", "", "file holding the name and time of the last record printed, created on the first batch; default <state dir>/root/next.cursor")
	cmd.Flags().DurationVar(&every, "every", defaultEvery, "how long one batch stays open")
	cmd.Flags().IntSliceVar(&shape.watch, "watch", nil, "pull requests whose queue state and LANDED and OVERRIDE-WARNING records close the batch")
	cmd.Flags().StringSliceVar(&shape.full, "full", nil, "record kinds whose bodies print whole, up to 32000 bytes")
	cmd.Flags().IntVar(&shape.width, "width", 0, "cut each record at this many bytes, naming the read that shows the rest")
	cmd.Flags().IntVar(&shape.lines, "lines", 0, "stop after this many lines, naming how many records were cut")
	return cmd
}

func watched(ctx context.Context, a *app.App, numbers []int, batch []record) ([]ccx.PRStatus, error) {
	if len(numbers) == 0 || len(batch) == 0 {
		return nil, nil
	}
	cfg, err := a.Config()
	if err != nil {
		return nil, err
	}
	return a.CCXFrom(cfg.Checkout).PRStatus(ctx, cfg.Repo, numbers)
}

func readCursor(ctx context.Context, driveName, path string) (int64, error) {
	raw, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	name, stamp, _ := strings.Cut(strings.TrimSpace(string(raw)), " ")
	printedAt, err := time.Parse(time.RFC3339Nano, stamp)
	if err != nil {
		return 0, fmt.Errorf("cursor %s holds %q, not the name and time of the record it last printed; delete it to start at the newest record", path, strings.TrimSpace(string(raw)))
	}
	since, err := client.Tail(ctx, client.Query{Drive: driveName, Since: printedAt})
	if err != nil {
		return 0, err
	}
	for _, r := range since {
		if r.Name() == name {
			return r.Seq, nil
		}
	}
	return 0, fmt.Errorf("this node's store has not taken %s yet, the record cursor %s last printed; it is still seeding or syncing, so run lr coordinator next again once cci relay status shows it caught up", name, path)
}

func saveCursor(path string, batch []record) error {
	if len(batch) == 0 {
		return nil
	}
	last := batch[len(batch)-1]
	return os.WriteFile(path, []byte(last.Name()+" "+last.At.Format(time.RFC3339Nano)+"\n"), 0o600)
}

func follow(ctx context.Context, driveName string, since int64) (<-chan record, <-chan error) {
	records := make(chan record)
	failure := make(chan error, 1)
	go func() {
		defer close(records)
		err := client.Follow(ctx, client.Watch{Drive: driveName, Reader: lanes.Root, After: since, Kinds: []string{"incident"}}, func(r client.Record) error {
			next := record{Seq: r.Seq, Num: r.Num, Lane: r.Lane, Kind: string(r.Kind), At: r.At, Text: r.Text, Topic: r.Topic, To: r.To, Re: r.Re, ReNum: r.ReNum}
			for _, pr := range r.Refs.PRs {
				next.PRs = append(next.PRs, pr.Number)
			}
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

func collect(ctx context.Context, records <-chan record, every, limit, soon time.Duration, wakes func(record) bool) []record {
	var batch []record
	woken := false
	due := time.After(every)
	giveUp := time.After(limit)
	for {
		select {
		case r, open := <-records:
			if !open {
				return batch
			}
			batch = append(batch, r)
			if !wakes(r) {
				continue
			}
			woken = true
			if due == nil || closesAtOnce(r) {
				due = time.After(soon)
			}
		case <-due:
			if woken {
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

func (l line) render() string {
	return l.head() + ": " + l.whole() + l.repeats()
}

func (l line) head() string {
	var out strings.Builder
	fmt.Fprintf(&out, "%s %s %s %s", l.Name(), l.At.In(pacific).Format("3:04 PM"), strings.ToUpper(l.Kind), l.Lane)
	if l.Re != 0 {
		fmt.Fprintf(&out, " re %s", l.ReName())
	}
	if others := l.alsoTo(); len(others) > 0 {
		fmt.Fprintf(&out, " -> %s", strings.Join(others, ","))
	}
	return out.String()
}

func (l line) repeats() string {
	if l.seen > 1 {
		return fmt.Sprintf(" (seen %d times)", l.seen)
	}
	return ""
}

func (r record) whole() string {
	return r.upTo(bodyShown)
}

func (r record) upTo(limit int) string {
	if r.Refs.Path == "" {
		return r.Text
	}
	raw, err := os.ReadFile(r.Refs.Path)
	if err != nil {
		return fmt.Sprintf("%s (body %s)", r.Text, r.Refs.Path)
	}
	if len(raw) > limit {
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
