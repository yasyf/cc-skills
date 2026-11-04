package supervisor

import (
	"context"
	"errors"
	"log/slog"
	"reflect"
	"regexp"
	"slices"
	"sync"
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
	TickEvery     = 30 * time.Second
	PassEvery     = 3 * time.Minute
	checkDeadline = 15 * time.Minute
	checkStopWait = 30 * time.Second
	checkStagger  = time.Second
	passedName    = "pass"
	moverLanes    = "lanes"

	unreadSubject    = "STATUS-READ-FAILED"
	unwrittenSubject = "REVIEW-ROWS-UNWRITTEN"
	rowsDesk         = pass.RowsDesk
)

var (
	prMovers = []string{"landing", "grading", "items"}
	digits   = regexp.MustCompile(`\d+`)
)

type Deps struct {
	Movers    []string
	Clock     func() time.Time
	Sleep     func(time.Duration)
	Lanes     func(now time.Time, generation int64) (lanes.Outcome, error)
	Checks    []lanes.Check
	Stagger   time.Duration
	Followers []func(context.Context)
	Snapshot  func(ctx context.Context) ([]machine.Facts, error)
	WriteRows func(ctx context.Context, snapshot []machine.Facts) error
	Post      pass.Post
	Queue     landing.Queue
	Restacker landing.Restacker
	Trunk     string
	Admit     func(ctx context.Context, acts []pass.Act, snapshot []machine.Facts) []pass.Act
	DropStale func(ctx context.Context, snapshot []machine.Facts) error
	Unstick   func(ctx context.Context, snapshot []machine.Facts) error
	SweepGos  func(ctx context.Context, snapshot []machine.Facts) error
	Pool      grading.Pool
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
	if !runsPass(d.Movers) {
		return report, nil
	}
	return runPass(ctx, s, drive, generation, d, now, report)
}

func runPass(ctx context.Context, s *store.Store, drive string, generation int64, d Deps, now time.Time, report Report) (Report, error) {
	last, err := s.Cursor(passedName)
	if err != nil {
		return report, err
	}
	if now.Unix()-last < int64(PassEvery.Seconds()) {
		return report, nil
	}
	if err := s.Advance(passedName, now.Unix()); err != nil {
		return report, err
	}
	snapshot, err := d.Snapshot(ctx)
	if err != nil {
		d.Log.Warn("pass skipped", "err", err)
		if err := tellUnread(s, d.Post, err, now); err != nil {
			d.Log.Warn("a skipped pass could not say so", "err", err)
		}
		return report, nil
	}
	report.PRs = len(snapshot)
	if d.WriteRows != nil {
		if err := d.WriteRows(ctx, snapshot); err != nil {
			d.Log.Warn("the review tab's rows were not written", "err", err)
			if err := tellUnwritten(s, d.Post, err, now); err != nil {
				d.Log.Warn("unwritten review rows could not say so", "err", err)
			}
		}
	}
	route, err := watch.Desks(s)
	if err != nil {
		return report, err
	}
	out, err := pass.Run(s, snapshot, now, d.Post, pass.Live(d.Movers), route, func(lane string) (string, error) { return lanes.Running(s, drive, lane) })
	if err != nil {
		return report, err
	}
	for _, unposted := range out.Unposted {
		d.Log.Warn("a pass post failed", "err", unposted)
	}
	report.Escalated = len(out.Escalated)
	if slices.Contains(d.Movers, "landing") {
		if err := landing.Announce(ctx, s, drive, d.Trunk, out.Landed, route); err != nil {
			d.Log.Warn("a landed record went unposted", "err", err)
		}
		if err := landing.CarryTaps(ctx, drive, out.Carried); err != nil {
			d.Log.Warn("a tap did not carry", "err", err)
		}
		if d.DropStale != nil {
			if err := d.DropStale(ctx, snapshot); err != nil {
				d.Log.Warn("a stale tap stayed on the board", "err", err)
			}
		}
		acts := landing.Clear(out.Acts, snapshot, d.Trunk)
		if d.Admit != nil {
			acts = d.Admit(ctx, acts, snapshot)
		}
		landed, err := landing.Act(ctx, s, d.Queue, d.Restacker, acts, now)
		if err != nil {
			d.Log.Warn("landing stopped mid-pass", "err", err)
		}
		if err := landing.Settle(ctx, s, drive, landed.Said); err != nil {
			d.Log.Warn("a queue answer went unposted", "err", err)
		}
		report.Enqueued = len(landed.Enqueued) + len(landed.Resubmitted)
		if d.Unstick != nil {
			if err := d.Unstick(ctx, snapshot); err != nil {
				d.Log.Warn("a PR stuck on an unknown mergeability was not retried", "err", err)
			}
		}
		if d.SweepGos != nil {
			if err := d.SweepGos(ctx, snapshot); err != nil {
				d.Log.Warn("the go sweep stopped", "err", err)
			}
		}
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

func tellUnwritten(s *store.Store, post pass.Post, unwritten error, now time.Time) error {
	cause := "the landing facts did not reach the review tab's rows, so the tab shows the last pass until the next one writes: " + unwritten.Error()
	raised, err := s.Escalate(unwrittenSubject, digits.ReplaceAllString(cause, "N"), rowsDesk, now)
	if err != nil || !raised {
		return err
	}
	return post([]string{rowsDesk}, bus.Escalation{Subject: unwrittenSubject, Cause: cause})
}

func tellUnread(s *store.Store, post pass.Post, unread error, now time.Time) error {
	cause := "the PR read failed, so this pass moved nothing and the next one reads again: " + unread.Error()
	raised, err := s.Escalate(unreadSubject, digits.ReplaceAllString(cause, "N"), lanes.Root, now)
	if err != nil || !raised {
		return err
	}
	return post([]string{lanes.Root}, bus.Escalation{Subject: unreadSubject, Cause: cause})
}

type Ended string

const (
	EndedGeneration Ended = "generation"
	EndedRelease    Ended = "release"
	EndedTicks      Ended = "ticks"
)

func startChecks(ctx context.Context, d Deps) func() {
	ctx, cancel := context.WithCancel(ctx)
	var running sync.WaitGroup
	for _, follow := range d.Followers {
		running.Go(func() { follow(ctx) })
	}
	for i, check := range d.Checks {
		running.Go(func() {
			select {
			case <-ctx.Done():
			case <-time.After(time.Duration(i) * d.Stagger):
			}
			for ctx.Err() == nil {
				limit, done := context.WithTimeout(ctx, checkDeadline)
				if err := check(limit, d.Clock().UTC()); err != nil {
					d.Log.Warn("check skipped", "err", err)
				}
				done()
				select {
				case <-ctx.Done():
				case <-time.After(TickEvery):
				}
			}
		})
	}
	return func() {
		cancel()
		finished := make(chan struct{})
		go func() {
			running.Wait()
			close(finished)
		}()
		select {
		case <-finished:
		case <-time.After(checkStopWait):
			d.Log.Warn("a check outlived its stop")
		}
	}
}

func Run(ctx context.Context, s *store.Store, drive, version string, pid int, d Deps, ticks int) ([]Report, Ended, error) {
	lease, err := s.AcquireLease(drive, version, pid, d.Clock())
	if err != nil {
		return nil, "", err
	}
	stop := startChecks(ctx, d)
	defer stop()
	var done []Report
	for ticks == 0 || len(done) < ticks {
		report, err := Tick(ctx, s, drive, lease.Generation, d)
		if errors.Is(err, store.ErrStaleGeneration) {
			return done, EndedGeneration, nil
		}
		if err != nil {
			return done, "", err
		}
		if ticks == 0 {
			done = done[:0]
		}
		done = append(done, report)
		if stale, err := d.stale(ctx); err != nil || stale {
			return done, endedBy(stale), err
		}
		d.Sleep(TickEvery)
		if stale, err := d.stale(ctx); err != nil || stale {
			return done, endedBy(stale), err
		}
	}
	return done, EndedTicks, nil
}

func (d Deps) stale(ctx context.Context) (bool, error) {
	if d.Stale == nil {
		return false, nil
	}
	return d.Stale(ctx)
}

func endedBy(stale bool) Ended {
	if stale {
		return EndedRelease
	}
	return ""
}
