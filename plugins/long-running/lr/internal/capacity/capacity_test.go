package capacity

import (
	"testing"
	"time"
)

var t0 = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)

func TestDesiredSizesThePoolFromTheQueue(t *testing.T) {
	cases := []struct {
		name               string
		queued             float64
		concurrency, limit int
		want               int
	}{
		{"forty heads of grading ask for executors in the same pass", 40 * 2 * 6, 4, 20, 12},
		{"an empty queue asks for nothing", 0, 4, 20, 0},
		{"the cap holds", 10_000, 1, 8, 8},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := Desired(c.queued, c.concurrency, c.limit); got != c.want {
				t.Fatalf("Desired = %d, want %d", got, c.want)
			}
		})
	}
}

func TestABurstGrowsThePoolAtOnce(t *testing.T) {
	grown := Plan(Pool{Executors: 2, Concurrency: 4, Limit: 20}, 400, t0)
	if grown.Executors != 10 || !grown.IdleSince.IsZero() {
		t.Fatalf("grown = %+v, want 10 executors and no idle clock", grown)
	}
}

func TestAShrinkingQueueKeepsThePoolUntilItIsEmpty(t *testing.T) {
	if got := Plan(Pool{Executors: 10, Concurrency: 4, Limit: 20}, 5, t0).Executors; got != 10 {
		t.Fatalf("executors = %d, want 10", got)
	}
}

func TestAnIdlePoolScalesDownOnlyAfterSixtyMinutes(t *testing.T) {
	idle := Plan(Pool{Executors: 10, Concurrency: 4, Limit: 20}, 0, t0)
	still := Plan(idle, 0, t0.Add(59*time.Minute))
	gone := Plan(still, 0, t0.Add(60*time.Minute))

	if idle.Executors != 10 || !idle.IdleSince.Equal(t0) {
		t.Fatalf("idle = %+v, want 10 executors idle since %v", idle, t0)
	}
	if still.Executors != 10 {
		t.Fatalf("still = %d executors, want 10", still.Executors)
	}
	if gone.Executors != 0 {
		t.Fatalf("gone = %d executors, want 0", gone.Executors)
	}
}

func TestWorkArrivingWhileIdleResetsTheIdleClock(t *testing.T) {
	idle := Plan(Pool{Executors: 10, Concurrency: 4, Limit: 20}, 0, t0)
	if busy := Plan(idle, 30, t0.Add(30*time.Minute)); !busy.IdleSince.IsZero() {
		t.Fatalf("busy idle clock = %v, want reset", busy.IdleSince)
	}
}
