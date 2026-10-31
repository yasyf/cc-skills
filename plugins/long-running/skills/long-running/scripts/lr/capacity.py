"""The grader pool's size follows the grade queue: enough executors to grade every open head within ten minutes.

Desired executors are `ceil(queued_grade_minutes / target_minutes / per_executor_concurrency)`,
never above the cap. The pool grows in the pass that sees the queue grow and shrinks only after
sixty idle minutes, so a burst of heads is met the same pass and a lull does not thrash.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

TARGET = timedelta(minutes=10)
IDLE_BEFORE_SCALE_DOWN = timedelta(minutes=60)


@dataclass(frozen=True)
class Pool:
    executors: int
    concurrency: int
    cap: int
    idle_since: datetime | None = None


def desired(queued_minutes: float, concurrency: int, cap: int) -> int:
    return min(cap, math.ceil(queued_minutes / (TARGET.total_seconds() / 60) / concurrency))


def plan(pool: Pool, queued_minutes: float, now: datetime) -> Pool:
    want = desired(queued_minutes, pool.concurrency, pool.cap)
    if want > pool.executors:
        return Pool(want, pool.concurrency, pool.cap, None)
    if queued_minutes > 0:
        return Pool(pool.executors, pool.concurrency, pool.cap, None)
    idle_since = pool.idle_since or now
    if now - idle_since >= IDLE_BEFORE_SCALE_DOWN:
        return Pool(0, pool.concurrency, pool.cap, idle_since)
    return Pool(pool.executors, pool.concurrency, pool.cap, idle_since)
