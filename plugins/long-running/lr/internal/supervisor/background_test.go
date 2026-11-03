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

func TestABackgroundPassRunsOnceAPeriod(t *testing.T) {
	passes := 0
	b := &background{name: "p", every: time.Hour, run: func(context.Context) error {
		passes++
		return nil
	}}

	for _, at := range []time.Time{t0, t0.Add(time.Minute), t0.Add(time.Hour)} {
		if err := b.Check(context.Background(), at); err != nil {
			t.Fatal(err)
		}
	}

	equal(t, passes, 2)
}
