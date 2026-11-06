package grading

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"time"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	lrfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	PassNone    = "none"
	PassRunning = "running"
	PassReader  = "reader"
	PassSettled = "settled"
	PassSkipped = "skipped"
	PassFailed  = "failed"
	PassLost    = "lost"

	shardEven    = "even"
	shardOdd     = "odd"
	runnerConfig = "runner.json"
	driveConfig  = "lr.json"
	queueRead    = "grade queue"
)

type QueueRow struct {
	Number   int        `json:"number"`
	Head     string     `json:"head"`
	RowHead  string     `json:"row_head,omitempty"`
	HeadAt   string     `json:"head_at,omitempty"`
	CI       machine.CI `json:"ci"`
	Pipeline string     `json:"pipeline"`
	Owed     bool       `json:"owed"`
	Held     bool       `json:"held"`
	Lane     string     `json:"lane"`
	Title    string     `json:"title"`
}

func (p *JobPool) Queue(ctx context.Context, grading string, items []store.Item, pushed map[int]lrfacts.Pushed, now time.Time) ([]QueueRow, error) {
	d, on := p.desks[grading]
	if !on {
		return nil, fmt.Errorf("runner.json has no %s desk", grading)
	}
	snapshot := make(map[int]machine.Facts, len(pushed))
	for number, row := range pushed {
		snapshot[number] = row.Facts
	}
	if err := p.readFirsts(ctx, now); err != nil {
		return nil, err
	}
	ranked, err := p.sorted(owedItems(grading, items, snapshot, p.firsts), snapshot)
	if err != nil {
		return nil, err
	}
	commands, err := p.commands(ctx)
	if err != nil {
		return nil, err
	}
	rows := make([]QueueRow, 0, len(ranked))
	for _, item := range ranked {
		row := pushed[item.Number]
		pass := p.passAt(d, p.passBase(grading, row.Facts), commands)
		rows = append(rows, QueueRow{
			Number: item.Number, Head: row.Facts.Head, RowHead: row.RowHead, HeadAt: row.HeadAt, CI: row.Facts.CI, Pipeline: pass,
			Owed: pass == PassReader || row.Facts.GradeStopped != "",
			Held: row.Facts.Hold != "", Lane: row.Facts.Lane, Title: row.Facts.Title,
		})
	}
	return rows, nil
}

func owedItems(grading string, items []store.Item, snapshot map[int]machine.Facts, firsts map[int]int64) []store.Item {
	owed := func(number int) bool {
		f, known := snapshot[number]
		graders := Missing(f)
		if firsts[number] > 0 {
			graders = Owed(f)
		}
		return known && !f.Undriven() && slices.Contains(graders, grading)
	}
	listed := map[int]bool{}
	kept := make([]store.Item, 0, len(snapshot))
	for _, item := range items {
		if item.Kind == ItemKind && !listed[item.Number] && owed(item.Number) {
			listed[item.Number] = true
			kept = append(kept, item)
		}
	}
	numbers := make([]int, 0, len(snapshot))
	for number := range snapshot {
		if !listed[number] && owed(number) {
			numbers = append(numbers, number)
		}
	}
	slices.Sort(numbers)
	for _, number := range numbers {
		kept = append(kept, store.Item{Kind: ItemKind, Number: number})
	}
	return kept
}

func (p *JobPool) passAt(d desk, base string, commands map[int]string) string {
	tried := p.triedAttempts(d, base)
	if tried == 0 {
		return PassNone
	}
	directory := filepath.Join(p.root, d.lane, attemptBase(base, tried))
	switch stateOf(directory, commands) {
	case jobRunning:
		return PassRunning
	case jobSettled:
		if handedToReader(directory) {
			return PassReader
		}
		return PassSettled
	case jobSkipped:
		return PassSkipped
	case jobFailed:
		return PassFailed
	default:
		return PassLost
	}
}

func handedToReader(directory string) bool {
	data, err := os.ReadFile(filepath.Join(directory, grader.ResultFile))
	if err != nil {
		return false
	}
	var outcome struct {
		Verdict string `json:"verdict"`
	}
	return json.Unmarshal(data, &outcome) == nil && outcome.Verdict == readerVerdict
}

func inShard(number int, shard string) bool {
	switch shard {
	case shardEven:
		return number%2 == 0
	case shardOdd:
		return number%2 == 1
	}
	return true
}

type queueQuery struct {
	grader, drive, shard string
	owed, json           bool
}

func (q *queueQuery) flags(fs *pflag.FlagSet) {
	fs.StringVar(&q.drive, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	fs.StringVar(&q.shard, "shard", "", "only even or only odd PR numbers, for desks that split the queue")
	fs.BoolVar(&q.owed, "owed", false, "only heads a desk must read now: the pipeline handed them over or stopped")
	fs.BoolVar(&q.json, "json", false, "print the rows as one JSON array")
}

func (q queueQuery) ask() string {
	read := queueRead + " " + q.grader
	if q.shard != "" {
		read += " --shard " + q.shard
	}
	if q.owed {
		read += " --owed"
	}
	if q.json {
		read += " --json"
	}
	return read
}

func queueCommand(a *app.App) *cobra.Command {
	var q queueQuery
	cmd := &cobra.Command{
		Use:   "queue GRADER",
		Short: "A grading desk's queue in the pool's own order, one line per pushed head owing this grade: whether a reader owes it, CI, and the pipeline pass there. Changes nothing. `lr grade first N` moves one PR to its front.",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			q.grader = args[0]
			return printQueue(c.Context(), a, q, c.OutOrStdout(), c.ErrOrStderr())
		},
	}
	q.flags(cmd.Flags())
	return cmd
}

func QueueRead(a *app.App, drive string) askdesk.Serve {
	return func(ctx context.Context, args []string, out, errs io.Writer) error {
		var q queueQuery
		parsed := pflag.NewFlagSet(queueRead, pflag.ContinueOnError)
		parsed.SetOutput(io.Discard)
		q.flags(parsed)
		if err := parsed.Parse(args); err != nil {
			return err
		}
		if parsed.NArg() != 1 {
			return errors.New(queueRead + " takes one GRADER")
		}
		q.grader, q.drive = parsed.Arg(0), drive
		return printQueue(ctx, a, q, out, errs)
	}
}

func printQueue(ctx context.Context, a *app.App, q queueQuery, out, errs io.Writer) error {
	if q.shard != "" && q.shard != shardEven && q.shard != shardOdd {
		return errors.New("--shard is even or odd")
	}
	entry, err := drive.Named(q.drive)
	if err != nil {
		return err
	}
	if entry.OnSprite() {
		return askdesk.AskMac(ctx, askdesk.LiveCCI{}, entry.Bus(), os.Getenv(lanes.LaneEnv), q.ask(), out)
	}
	cfg, err := config.Load(filepath.Join(entry.StateDir, driveConfig), errs)
	if err != nil {
		return err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	pool, err := readingPool(a, entry.StateDir, s)
	if err != nil {
		return err
	}
	pushed, err := lrfacts.New(a, cfg, s).AtPushedHeads(ctx)
	if err != nil {
		return err
	}
	items, err := s.OpenItems("", 0)
	if err != nil {
		return err
	}
	now := a.Now()
	rows, err := pool.Queue(ctx, q.grader, items, pushed, now)
	if err != nil {
		return err
	}
	rows = slices.DeleteFunc(rows, func(row QueueRow) bool {
		return !inShard(row.Number, q.shard) || q.owed && !row.Owed
	})
	if q.json {
		return json.NewEncoder(out).Encode(rows)
	}
	for _, row := range rows {
		fmt.Fprintln(out, row.line(now))
	}
	return nil
}

func readingPool(a *app.App, state string, s *store.Store) (*JobPool, error) {
	source, err := filepath.EvalSymlinks(filepath.Join(state, runnerConfig))
	if err != nil {
		return nil, err
	}
	runner, err := config.LoadRunner(source)
	if err != nil {
		return nil, err
	}
	return NewJobPool(runner, PoolDeps{Runner: a.Runner, Ledger: s.LedgerRows, Log: a.Log, Now: a.Now})
}

func (r QueueRow) line(now time.Time) string {
	owed := "waiting"
	if r.Owed {
		owed = "owed"
	}
	line := fmt.Sprintf("#%d %s %s ci=%s pipeline=%s age=%s lane=%s", r.Number, short(r.Head), owed, r.CI, r.Pipeline, r.age(now), r.Lane)
	if r.Held {
		line += " held"
	}
	if r.RowHead != "" {
		line += " row-at=" + short(r.RowHead)
	}
	return line + " | " + r.Title
}

func (r QueueRow) age(now time.Time) string {
	at, err := store.Parse(r.HeadAt)
	if err != nil {
		return "new"
	}
	return strconv.Itoa(int(now.Sub(at).Minutes())) + "m"
}
