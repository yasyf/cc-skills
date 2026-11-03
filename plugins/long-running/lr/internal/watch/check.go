package watch

import (
	"context"
	"fmt"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Lane          = "watch"
	statusTimeout = 2 * time.Minute
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

func Sweep(s *store.Store, records, asks []client.Record, status func(int) (Gate, error), now time.Time, post pass.Post) ([]pass.Escalated, error) {
	gates, err := ReleaseGates(s, records, status, now)
	if err != nil {
		return nil, err
	}
	escalations, err := s.Escalations("")
	if err != nil {
		return nil, err
	}
	standing := slices.Concat(gates, OpenAsks(asks, records, now), LandedWaits(records), Repeats(escalations, now))
	return Apply(s, Namespaces, standing, now, post)
}

func releaseStatus(ctx context.Context, runner shell.Runner, checkout string, now time.Time) func(int) (Gate, error) {
	return func(id int) (Gate, error) {
		result, err := runner.Run(ctx, shell.Command{Argv: []string{"tools/ci", "release", "status", strconv.Itoa(id)}, Dir: checkout, Timeout: statusTimeout})
		if err != nil {
			return Gate{}, fmt.Errorf("release status %d: %w", id, err)
		}
		first, _, _ := strings.Cut(result.Stdout, "\n")
		return ParseGate(first, now)
	}
}

func Check(ctx context.Context, s *store.Store, drive, checkout string, runner shell.Runner, now time.Time) ([]pass.Escalated, error) {
	records, err := client.Tail(ctx, client.Query{Drive: drive, Since: now.Add(-Window), Kinds: Kinds})
	if err != nil {
		return nil, err
	}
	asks, err := client.Open(ctx, drive, "ask")
	if err != nil {
		return nil, err
	}
	sender := bus.Bus{Drive: drive, Lane: Lane}
	post := func(to []string, body bus.Body) error {
		_, err := sender.Post(ctx, to, body)
		return err
	}
	return Sweep(s, records, asks, releaseStatus(ctx, runner, checkout, now), now, post)
}

func deadlinesCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
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
			escalated, err := Check(cmd.Context(), s, cfg.Drive, cfg.Checkout, a.Runner, a.Now())
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
}
