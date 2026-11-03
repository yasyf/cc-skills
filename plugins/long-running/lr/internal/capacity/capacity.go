package capacity

import (
	"math"
	"time"
)

const (
	Target              = 10 * time.Minute
	IdleBeforeScaleDown = 60 * time.Minute
)

type Pool struct {
	Executors   int
	Concurrency int
	Limit       int
	IdleSince   time.Time
}

func Desired(queuedMinutes float64, concurrency, limit int) int {
	return min(limit, int(math.Ceil(queuedMinutes/Target.Minutes()/float64(concurrency))))
}

func Plan(pool Pool, queuedMinutes float64, now time.Time) Pool {
	want := Desired(queuedMinutes, pool.Concurrency, pool.Limit)
	if want > pool.Executors {
		pool.Executors, pool.IdleSince = want, time.Time{}
		return pool
	}
	if queuedMinutes > 0 {
		pool.IdleSince = time.Time{}
		return pool
	}
	if pool.IdleSince.IsZero() {
		pool.IdleSince = now
	}
	if now.Sub(pool.IdleSince) >= IdleBeforeScaleDown {
		pool.Executors = 0
	}
	return pool
}
