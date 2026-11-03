package supervisor

import (
	"context"
	"errors"
	"log/slog"
	"reflect"
	"slices"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
)

const (
	TickEvery   = 30 * time.Second
	PassEvery   = 3 * time.Minute
	passedName  = "pass"
	moverLanes  = "lanes"
	moverShadow = "shadow"
)

var prMovers = []string{moverShadow, "landing", "grading", "items"}

type Deps struct {
	Movers    []string
	Clock     func() time.Time
	Sleep     func(time.Duration)
	Lanes     func(now time.Time, generation int64) (lanes.Outcome, error)
	Checks    []lanes.Check
	Snapshot  func(ctx context.Context) ([]machine.Facts, error)
	Post      pass.Post
	Queue     landing.Queue
	Restacker landing.Restacker
	Trunk     string
	Pool      grading.Pool
	Shadow    *store.Store
	Stale     func(ctx context.Context) (bool, error)
	Log       *slog.Logger
}

type Report struct {
	At         time.Time
	PRs        int
	Enqueued   int
	Dispatched int
	Escalated  int
	Relaunched int
}

func runsPass(movers []string) bool {
	return slices.ContainsFunc(movers, func(mover string) bool { return slices.Contains(prMovers, mover) })
}

func Tick(ctx context.Context, s *store.Store, drive string, generation int64, d Deps) (Report, error) {
	now := d.Clock().UTC()
	report := Report{At: now}
	if err := s.RenewLease(drive, generation, now); err != nil {
		return report, err
	}
	if err := s.Heartbeat(store.Worker{Name: lanes.Self, Generation: generation, Heartbeat: now}); err != nil {
		return report, err
	}
	if slices.Contains(d.Movers, moverLanes) {
		moved, err := d.Lanes(now, generation)
		switch {
		case errors.Is(err, lanes.ErrUnread):
			d.Log.Warn("lanes tick skipped", "err", err)
		case err != nil:
			return report, err
		default:
			report.Relaunched = len(moved.Relaunched)
			if !reflect.DeepEqual(moved, lanes.Outcome{}) {
				d.Log.Info("lanes acted", "relaunched", moved.Relaunched, "asked", moved.Asked, "held", moved.Held, "adopted", moved.Adopted, "withdrawn", moved.Withdrawn, "woken", moved.Woken)
			}
		}
	}
	for _, check := range d.Checks {
		switch err := check(ctx, now); {
		case errors.Is(err, lanes.ErrUnread):
			d.Log.Warn("check skipped", "err", err)
		case err != nil:
			return report, err
		}
	}
	if !runsPass(d.Movers) {
		return report, nil
	}
	return runPass(ctx, s, generation, d, now, report)
}

func runPass(ctx context.Context, s *store.Store, generation int64, d Deps, now time.Time, report Report) (Report, error) {
	passing := s
	shadow := slices.Contains(d.Movers, moverShadow)
	if shadow {
		passing = d.Shadow
	}
	last, err := passing.Cursor(passedName)
	if err != nil {
		return report, err
	}
	if now.Unix()-last < int64(PassEvery.Seconds()) {
		return report, nil
	}
	if err := passing.Advance(passedName, now.Unix()); err != nil {
		return report, err
	}
	snapshot, err := d.Snapshot(ctx)
	if err != nil {
		d.Log.Warn("pass skipped", "err", err)
		return report, nil
	}
	report.PRs = len(snapshot)
	if shadow {
		_, err := pass.Shadow(passing, snapshot, now, d.Post)
		return report, err
	}
	route, err := watch.Desks(s)
	if err != nil {
		return report, err
	}
	out, err := pass.Run(s, snapshot, now, d.Post, pass.Live(d.Movers), route)
	if err != nil && !errors.As(err, new(*bus.Refused)) {
		return report, err
	}
	if err != nil {
		d.Log.Warn("a pass post was refused", "err", err)
	}
	report.Escalated = len(out.Escalated)
	if slices.Contains(d.Movers, "landing") {
		landed, err := landing.Act(ctx, s, d.Queue, d.Restacker, landing.Clear(out.Acts, snapshot, d.Trunk), now)
		if err != nil {
			d.Log.Warn("landing stopped mid-pass", "err", err)
		}
		report.Enqueued = len(landed.Enqueued) + len(landed.Resubmitted)
	}
	if slices.Contains(d.Movers, "grading") {
		items, err := s.OpenItems("", 0)
		if err != nil {
			return report, err
		}
		byNumber := make(map[int]machine.Facts, len(snapshot))
		for _, facts := range snapshot {
			byNumber[facts.Number] = facts
		}
		dispatched, err := grading.Act(ctx, s, d.Pool, items, byNumber, now, generation)
		if err != nil {
			d.Log.Warn("movers stopped mid-pass", "err", err)
			return report, nil
		}
		report.Dispatched = len(dispatched)
	}
	return report, nil
}

type Ended string

const (
	EndedGeneration Ended = "generation"
	EndedRelease    Ended = "release"
	EndedTicks      Ended = "ticks"
)

func Run(ctx context.Context, s *store.Store, drive, version string, pid int, d Deps, ticks int) ([]Report, Ended, error) {
	lease, err := s.AcquireLease(drive, version, pid, d.Clock())
	if err != nil {
		return nil, "", err
	}
	var done []Report
	for ticks == 0 || len(done) < ticks {
		report, err := Tick(ctx, s, drive, lease.Generation, d)
		if errors.Is(err, store.ErrStaleGeneration) {
			return done, EndedGeneration, nil
		}
		if err != nil {
			return done, "", err
		}
		done = append(done, report)
		if d.Stale != nil {
			stale, err := d.Stale(ctx)
			if err != nil {
				return done, "", err
			}
			if stale {
				return done, EndedRelease, nil
			}
		}
		d.Sleep(TickEvery)
	}
	return done, EndedTicks, nil
}
