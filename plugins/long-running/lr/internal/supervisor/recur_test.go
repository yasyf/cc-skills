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

func TestAFailedRecurrencePassReachesRootOncePerError(t *testing.T) {
	s := memory(t)
	var told []bus.Body
	failure := errors.New("cci tail exited 1")
	r := &recurrences{
		count: func(context.Context) error { return failure },
		store: s,
		post: func(_ []string, body bus.Body) error {
			told = append(told, body)
			return nil
		},
		log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}

	r.pass(context.Background(), t0)
	r.pass(context.Background(), t0.Add(recurEvery))
	equal(t, len(told), 1)

	failure = errors.New("a fixture class is gone")
	r.pass(context.Background(), t0.Add(2*recurEvery))
	equal(t, len(told), 2)

	failure = nil
	r.pass(context.Background(), t0.Add(3*recurEvery))
	equal(t, len(told), 2)
}

func TestTheRecurrencePassRunsOnceAnHour(t *testing.T) {
	counted := make(chan struct{}, 2)
	r := &recurrences{count: func(context.Context) error {
		counted <- struct{}{}
		return nil
	}}

	for _, at := range []time.Time{t0, t0.Add(recurEvery - time.Minute), t0.Add(recurEvery)} {
		if err := r.Check(context.Background(), at); err != nil {
			t.Fatal(err)
		}
	}

	<-counted
	<-counted
	equal(t, len(counted), 0)
}
