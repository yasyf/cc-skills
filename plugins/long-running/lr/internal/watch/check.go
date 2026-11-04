package watch

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Lane       = "watch"
	stallEvery = 5 * time.Minute
)

var numberedLane = regexp.MustCompile(`^(.+)-(\d{1,2})$`)

func Desks(s *store.Store) (pass.Route, error) {
	live, err := s.Lanes(store.LaneLaunching, store.LaneDesired)
	if err != nil {
		return nil, err
	}
	return func(desk string) string {
		owner, best := root, -1
		for _, lane := range live {
			number := 1
			if found := numberedLane.FindStringSubmatch(lane.Name); found != nil && found[1] == desk {
				parsed, err := strconv.Atoi(found[2])
				if err != nil {
					panic(err)
				}
				number = parsed
			} else if lane.Name != desk {
				continue
			}
			if number > best {
				owner, best = lane.Name, number
			}
		}
		return owner
	}, nil
}

func Sweep(s *store.Store, records, asks []client.Record, unfinished []Wait, now time.Time, post pass.Post) ([]pass.Escalated, error) {
	escalations, err := s.Escalations("")
	if err != nil {
		return nil, err
	}
	watched := slices.DeleteFunc(slices.Clone(records), func(record client.Record) bool { return !slices.Contains(Kinds, string(record.Kind)) })
	standing := slices.Concat(unfinished, OpenAsks(asks, watched, now), LandedWaits(watched), Unplanned(records, now), Repeats(escalations, now), PortCensusWaits(records, now))
	return Apply(s, Namespaces, standing, now, post)
}

func Check(ctx context.Context, s *store.Store, entry drive.Entry, runner shell.Runner, now time.Time, post pass.Post) ([]pass.Escalated, error) {
	records, err := client.Tail(ctx, client.Query{Drive: entry.Bus(), Since: now.Add(-Window)})
	if err != nil {
		return nil, err
	}
	open, err := client.Open(ctx, entry.Bus(), "ask")
	if err != nil {
		return nil, err
	}
	ledgers, err := ReadLedgers(ctx, s, entry, runner, now)
	if err != nil {
		return nil, err
	}
	unfinished, err := Unfinished(ledgers, now)
	if err != nil {
		return nil, err
	}
	escalated, swept := Sweep(s, records, open, unfinished, now, post)
	items, written, err := ReadMeta(entry.StateDir)
	if err != nil {
		return escalated, errors.Join(swept, err)
	}
	paged, err := Meta(s, items, written, now, post)
	return append(escalated, paged...), errors.Join(swept, err)
}

type Stalls struct {
	Store  *store.Store
	Drive  string
	Runner shell.Runner
	Post   pass.Post
	Log    *slog.Logger
	read   time.Time
}

func (w *Stalls) Check(ctx context.Context, now time.Time) error {
	if !w.read.IsZero() && now.Sub(w.read) < stallEvery {
		return nil
	}
	w.read = now
	entry, err := drive.Named(w.Drive)
	if err != nil {
		w.Log.Warn("stall watch has no drive entry", "err", err)
		return nil
	}
	if _, err := Check(ctx, w.Store, entry, w.Runner, now, w.Post); err != nil {
		w.Log.Warn("stall watch pass left work undone", "err", err)
	}
	return nil
}

func deadlinesCommand(a *app.App) *cobra.Command {
	var dryRun bool
	cmd := &cobra.Command{
		Use:   "deadlines",
		Short: "Open an item for each wait no PR state carries, and tell the lane that owns each one past its deadline",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cfg, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			s, err := store.Open(filepath.Join(state, lanes.StoreFile))
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			entry, err := drive.Named(cfg.Drive)
			if err != nil {
				return err
			}
			sender := bus.Bus{Drive: entry.Bus(), Lane: Lane, Bodies: filepath.Join(state, bus.BodiesDir)}
			post := func(to []string, body bus.Body) error {
				if dryRun {
					return nil
				}
				_, err := sender.Post(cmd.Context(), to, body)
				return err
			}
			escalated, err := Check(cmd.Context(), s, entry, a.Runner, a.Now(), post)
			if err != nil {
				return err
			}
			for _, each := range escalated {
				if _, err := fmt.Fprintf(cmd.OutOrStdout(), "%s -> %s: %s\n", each.Subject, each.To, each.Cause); err != nil {
					return err
				}
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "print what is overdue and post nothing; the store still records it, so point --config at a copy")
	return cmd
}
