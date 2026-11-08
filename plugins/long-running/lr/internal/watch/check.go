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
	registered, err := s.AllLanes()
	if err != nil {
		return nil, err
	}
	watched := slices.DeleteFunc(slices.Clone(records), func(record client.Record) bool { return !slices.Contains(Kinds, string(record.Kind)) })
	standing := slices.Concat(unfinished, OpenAsks(asks, watched, now), LandedWaits(watched), Unplanned(records, now), MissedSteps(records, registered, now), Repeats(escalations, now), PortCensusWaits(records, now))
	return Apply(s, Namespaces, standing, now, post)
}

func Check(ctx context.Context, s *store.Store, entry drive.Entry, runner shell.Runner, now time.Time, post pass.Post) ([]pass.Escalated, time.Time, error) {
	records, err := client.Tail(ctx, client.Query{Drive: entry.Bus(), Since: now.Add(-Window)})
	if err != nil {
		return nil, time.Time{}, err
	}
	next := NextStep(records, now)
	owed, err := client.Open(ctx, entry.Bus(), askKind, "blocker")
	if err != nil {
		return nil, next, err
	}
	owed = append(owed, RootQuestions(records)...)
	if err := wake(ctx, s, entry.Bus(), WakeRoot(records, owed, now), now); err != nil {
		return nil, next, err
	}
	open := slices.DeleteFunc(owed, func(record client.Record) bool { return string(record.Kind) == "blocker" })
	ledgers, err := ReadLedgers(ctx, s, entry, runner, now)
	if err != nil {
		return nil, next, err
	}
	unfinished, err := Unfinished(ledgers, now)
	if err != nil {
		return nil, next, err
	}
	escalated, swept := Sweep(s, records, open, unfinished, now, post)
	items, written, err := ReadMeta(entry.StateDir)
	if err != nil {
		return escalated, next, errors.Join(swept, err)
	}
	paged, err := Meta(s, items, written, now, post)
	return append(escalated, paged...), next, errors.Join(swept, err)
}

func wake(ctx context.Context, s *store.Store, bus string, due *RootWake, now time.Time) error {
	if due == nil {
		return nil
	}
	raised, err := s.Escalate(due.Subject, due.Line, root, now)
	if err != nil || !raised {
		return err
	}
	_, err = client.Post(ctx, client.Draft{Drive: bus, Lane: lanes.Self, Kind: "go", To: []string{wakeDesk}, Topic: due.Subject, Text: due.Line})
	return err
}

type Stalls struct {
	Store  *store.Store
	Drive  string
	Runner shell.Runner
	Post   pass.Post
	Log    *slog.Logger
	read   time.Time
	step   time.Time
}

func (w *Stalls) Check(ctx context.Context, now time.Time) error {
	if !w.read.IsZero() && now.Sub(w.read) < stallEvery && (w.step.IsZero() || now.Before(w.step)) {
		return nil
	}
	w.read = now
	entry, err := drive.Named(w.Drive)
	if err != nil {
		w.Log.Warn("stall watch has no drive entry", "err", err)
		return nil
	}
	_, next, err := Check(ctx, w.Store, entry, w.Runner, now, w.Post)
	w.step = next
	if err != nil {
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
			escalated, _, err := Check(cmd.Context(), s, entry, a.Runner, a.Now(), post)
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
