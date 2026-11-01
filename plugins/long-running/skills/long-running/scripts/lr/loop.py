"""The supervisor's loop: one leased process per drive, one pass per tick, exit when the lease moves.

Every tick renews the lease under the generation it was started with, beats as a worker,
runs the lanes mover, then reads the facts, runs the pass, hands the pass's actions to the
landing mover and its grade items to the grading mover, and sleeps. Only the movers the
config names run, so each one cuts over on its own. A renewal that finds a newer generation ends the
loop on that tick, so two supervisors never act at once and an install that starts the
successor is all it takes to retire the predecessor. A tick that ends with a newer release
installed ends the loop too, so the process can re-execute into that release.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from lr import supervisor
from lr.machine import Facts
from lr.movers import grading, landing, lanes
from lr.store import StaleGeneration, Store, stamp

TICK = timedelta(seconds=30)
SELF = "supervisor"


@dataclass(frozen=True)
class Deps:
    movers: frozenset[str]
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]
    snapshot: Callable[[], Iterable[Facts]] | None = None
    post: Callable[..., object] | None = None
    queue: landing.Queue | None = None
    restacker: landing.Restacker | None = None
    pool: grading.Pool | None = None
    lane_deps: lanes.Deps | None = None
    stale: Callable[[], bool] = lambda: False


@dataclass
class Tick:
    at: datetime
    prs: int
    enqueued: int
    dispatched: int
    escalated: int
    relaunched: int = 0


def tick(store: Store, drive: str, generation: int, deps: Deps) -> Tick:
    now = deps.clock()
    store.renew_lease(drive, generation, now)
    store.heartbeat(SELF, generation, now)
    moved = lanes.tick(store, deps.lane_deps, now, generation) if "lanes" in deps.movers else lanes.Outcome()
    if acted := "; ".join(f"{name} {', '.join(map(str, values))}" for name, values in vars(moved).items() if values):
        print(f"{stamp(now)} {acted}", flush=True)
    relaunched = len(moved.relaunched)
    if not deps.movers & {"landing", "grading"}:
        return Tick(now, 0, 0, 0, 0, relaunched)
    snapshot = list(deps.snapshot())
    out = supervisor.run_pass(store, snapshot, now, deps.post)
    landed = landing.act(store, deps.queue, deps.restacker, out.acts, now) if "landing" in deps.movers else landing.Done()
    graded = grading.act(store, deps.pool, store.open_items(), {f.number: f for f in snapshot}, now, generation) if "grading" in deps.movers else grading.Done()
    return Tick(now, len(snapshot), len(landed.enqueued) + len(landed.resubmitted), len(graded.dispatched), len(out.escalated), relaunched)


def run(store: Store, drive: str, version: str, pid: int, deps: Deps, ticks: int | None = None) -> list[Tick]:
    """Hold the lease and tick until a newer generation takes it or a newer release is installed, or for `ticks` ticks in a test."""
    lease = store.acquire_lease(drive, version, pid, deps.clock())
    done: list[Tick] = []
    while ticks is None or len(done) < ticks:
        try:
            done.append(tick(store, drive, lease.generation, deps))
        except StaleGeneration:
            break
        if deps.stale():
            break
        deps.sleep(TICK.total_seconds())
    return done
