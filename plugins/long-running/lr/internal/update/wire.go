package update

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func Wire(a *app.App, entry drive.Entry, s *store.Store) Sources {
	mirror := asks.Mirror{Source: ledger.CLI{Runner: a.Runner, Repo: entry.Checkout}, Store: s, Ledger: entry.Ledger, Now: a.Now}
	ccn := func(ctx context.Context, args ...string) (string, error) {
		result, err := a.Runner.Run(ctx, shell.Command{Argv: append([]string{"ccn", "-R", entry.Checkout}, args...)})
		return result.Stdout, err
	}
	return Sources{
		Lanes: func(context.Context) ([]Lane, error) { return registry(s) },
		Records: func(ctx context.Context, since time.Time, kinds ...string) ([]Record, error) {
			return busRecords(ctx, entry.Bus(), since, kinds)
		},
		Plans: func(ctx context.Context) ([]*plans.Plan, error) {
			progress, err := plans.Read(ctx, ccn, entry.Program)
			if err != nil {
				return nil, err
			}
			return progress.Plans, nil
		},
		Book: mirror.Book,
		Board: func(ctx context.Context, snapshots []board.Snapshot) (board.Live, error) {
			outcomes, err := board.ReadOutcomes(ctx, a.Runner, entry.Root())
			if err != nil {
				return board.Live{}, err
			}
			return board.LiveOf(outcomes, snapshots)
		},
		Tasks: func() ([]ClaudeTask, error) {
			dir, err := drive.ConfigDir()
			if err != nil {
				return nil, err
			}
			return ReadTasks(dir, entry.Root())
		},
		Before: func() (Snapshot, error) { return ReadSnapshot(entry.StateDir) },
		Retro: func(ctx context.Context) ([]Retro, error) {
			return ReadRetro(ctx, mirror.Source, entry.StateDir)
		},
		Owed: func(ctx context.Context) ([]deletions.Owed, error) {
			cfg, err := config.Load(filepath.Join(entry.StateDir, "lr.json"), a.Stderr)
			if errors.Is(err, fs.ErrNotExist) {
				return nil, nil
			}
			if err != nil {
				return nil, err
			}
			var labels []string
			if entry.Program != "" {
				labels = []string{"program:" + entry.Program}
			}
			trunk := deletions.Of(a.Runner, cfg)
			listed, err := trunk.List(ctx, labels...)
			if err != nil {
				return nil, err
			}
			return trunk.Present(ctx, listed)
		},
		Programs: func() ([]Program, error) { return ReadPrograms(filepath.Join(entry.StateDir, toolingList)) },
		Streams:  func() ([]Workstream, error) { return ReadWorkstreams(filepath.Join(entry.StateDir, workstreamList)) },
		Rulings: func(ctx context.Context) ([]PlanRuling, error) {
			return ReadRulings(ctx, ccn, mirror.Source, entry.Program)
		},
		Overdue: s.Overdue,
		Orders: func(ctx context.Context) ([]owner.Row, error) {
			return lanes.OpenOrders(ctx, lanes.CCI{}, s, entry.Bus(), entry.Repo, entry.StateDir)
		},
		Pins: entry.Pinned,
		TapQueue: func() (string, error) {
			cfg, err := config.Load(filepath.Join(entry.StateDir, "lr.json"), a.Stderr)
			return cfg.TapQueueURL, err
		},
		Where: func(ctx context.Context, shas map[int]string) map[int]string {
			read, err := lanes.NewReleaseRead(a, entry)
			if err != nil {
				a.Log.Warn("the update's rows read no release", "err", err)
				return nil
			}
			view := read.View(ctx)
			lines := make(map[int]string, len(shas))
			for number, sha := range shas {
				found := read.Locate(ctx, view, sha)
				if len(found.Unknown) > 0 {
					a.Log.Warn("a row did not read the release carrying its PR", "pr", number, "unknown", found.Unknown)
					continue
				}
				lines[number] = found.Line()
			}
			return lines
		},
	}
}

func registry(s *store.Store) ([]Lane, error) {
	rows, err := lanes.LivenessRows(s)
	if err != nil {
		return nil, err
	}
	out := make([]Lane, len(rows))
	for i, row := range rows {
		lane, err := laneOf(row)
		if err != nil {
			return nil, err
		}
		out[i] = lane
	}
	return out, nil
}

func laneOf(row lanes.Liveness) (Lane, error) {
	lane := Lane{Name: row.Lane, State: row.State, PRs: row.PRs, Step: row.Step, Standing: row.Standing, Deliverable: row.Deliverable, Brief: deref(row.Brief), RetiredBy: deref(row.RetiredBy), Predecessor: deref(row.Predecessor)}
	if row.Heard == nil {
		return lane, nil
	}
	heard, err := store.Parse(*row.Heard)
	if err != nil {
		return Lane{}, fmt.Errorf("lane %s heard: %w", row.Lane, err)
	}
	lane.Heard = heard
	return lane, nil
}

func deref(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func busRecords(ctx context.Context, bus string, since time.Time, kinds []string) ([]Record, error) {
	found, err := client.Tail(ctx, client.Query{Drive: bus, Since: since, Kinds: kinds})
	if err != nil {
		return nil, err
	}
	out := make([]Record, len(found))
	for i, raw := range found {
		prs := make([]string, len(raw.Refs.PRs))
		for j, pr := range raw.Refs.PRs {
			prs[j] = fmt.Sprint(pr)
		}
		out[i] = Record{Seq: raw.Seq, Drive: raw.Drive, Lane: raw.Lane, Kind: string(raw.Kind), Text: raw.Text, Status: raw.Status, At: raw.At, To: raw.To, PRs: prs, Topic: raw.Topic, Board: raw.Fields.Attrs["board"], Attrs: raw.Fields.Attrs, Re: raw.Re, Resolves: raw.Closes()}
	}
	return out, nil
}
