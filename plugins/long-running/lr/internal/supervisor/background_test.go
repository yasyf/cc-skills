package supervisor

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
)

func TestAFailedBackgroundPassReachesRootOncePerError(t *testing.T) {
	s := memory(t)
	var told []bus.Body
	failure := errors.New("cci tail exited 1")
	b := &background{
		name:  "recurrence-count",
		run:   func(context.Context) error { return failure },
		store: s,
		post: func(_ []string, body bus.Body) error {
			told = append(told, body)
			return nil
		},
		log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}

	b.pass(context.Background(), t0)
	b.pass(context.Background(), t0.Add(recurEvery))
	equal(t, len(told), 1)

	failure = errors.New("a fixture class is gone")
	b.pass(context.Background(), t0.Add(2*recurEvery))
	equal(t, len(told), 2)

	failure = nil
	b.pass(context.Background(), t0.Add(3*recurEvery))
	equal(t, len(told), 2)
}

func TestAFailedPassWithItsOwnLaneTellsThatLaneNotRoot(t *testing.T) {
	var to []string
	var told bus.Body
	b := &background{
		name:  "waste-report",
		run:   func(context.Context) error { return errors.New("cc-transcript exited 1") },
		store: memory(t),
		post: func(lanes []string, body bus.Body) error {
			to, told = lanes, body
			return nil
		},
		log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		to:  "meta",
	}

	b.pass(context.Background(), t0)

	equal(t, to, []string{"meta"})
	equal(t, told.Kind(), "note")
}

func TestABackgroundPassRunsOnceAPeriodAndNeverOverlapsItself(t *testing.T) {
	started := make(chan struct{}, 3)
	release := make(chan struct{})
	b := &background{name: "p", every: time.Hour, run: func(context.Context) error {
		started <- struct{}{}
		<-release
		return nil
	}}

	check := func(at time.Time) {
		t.Helper()
		if err := b.Check(context.Background(), at); err != nil {
			t.Fatal(err)
		}
	}
	check(t0)
	<-started
	check(t0.Add(time.Minute))
	check(t0.Add(2 * time.Hour))
	equal(t, len(started), 0)

	release <- struct{}{}
	for b.busy.Load() {
		time.Sleep(time.Millisecond)
	}
	check(t0.Add(3 * time.Hour))
	<-started
	release <- struct{}{}
}
