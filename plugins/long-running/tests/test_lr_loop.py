"""Two supervisors, one acts; a tick is one pass; a stale generation exits on its next tick."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from lr import loop
from lr.machine import Facts
from lr.store import StaleGeneration, Store

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
REPO = "org/repo"
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class Queue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, number, head):
        self.enqueued.append(number)

    def dequeue(self, number):
        pass


class Pool:
    def __init__(self):
        self.jobs = []

    def dispatch(self, grader, repo, number, head):
        self.jobs.append((grader, number))
        return f"job/{grader}/{number}"


class Restacker:
    def restack(self, number, head, base):
        return ""


def deps(clock, snapshot, queue=None, pool=None):
    return loop.Deps(snapshot=lambda: snapshot, post=lambda *a, **k: None, queue=queue or Queue(), restacker=Restacker(), pool=pool or Pool(), clock=clock, sleep=clock.sleep)


def facts(number, **fields):
    base = dict(repo=REPO, number=number, head=HEAD, base="dev", lane="lane-7", ci="success", mergeable="clean", finder="clear", fidelity="pass", tap="tapped")
    return Facts(**{**base, **fields})


def test_a_tick_is_one_pass_that_lands_and_grades():
    store, clock, queue, pool = Store.memory(), Clock(), Queue(), Pool()

    [tick] = loop.run(store, "d", "0.8.0", 1, deps(clock, [facts(1), facts(2, finder=None, fidelity=None)], queue, pool), ticks=1)

    assert (tick.prs, tick.enqueued, tick.dispatched, tick.escalated) == (2, 1, 2, 0)
    assert queue.enqueued == [1] and [n for _, n in pool.jobs] == [2, 2]
    assert store.lease("d").generation == 1
    assert [row["name"] for row in store.workers()] == ["job/fidelity/2", "job/finder/2", "supervisor"]


def test_ticks_are_thirty_seconds_apart_and_renew_the_lease():
    store, clock = Store.memory(), Clock()

    ticks = loop.run(store, "d", "0.8.0", 1, deps(clock, []), ticks=3)

    assert [t.at for t in ticks] == [T0, T0 + timedelta(seconds=30), T0 + timedelta(seconds=60)]
    assert store.lease("d").renewed == T0 + timedelta(seconds=60)


def test_a_second_supervisor_takes_the_lease_and_the_first_exits_on_its_next_tick():
    store, clock = Store.memory(), Clock()
    first = store.acquire_lease("d", "0.8.0", 101, T0)
    d = deps(clock, [facts(1)])

    loop.tick(store, "d", first.generation, d)
    second = loop.run(store, "d", "0.8.1", 202, d, ticks=1)

    assert len(second) == 1 and store.lease("d").pid == 202
    with pytest.raises(StaleGeneration):
        loop.tick(store, "d", first.generation, d)


def test_run_ends_by_itself_when_its_generation_is_taken():
    store, clock = Store.memory(), Clock()
    d = deps(clock, [])
    stolen = {"done": False}

    def snapshot():
        if not stolen["done"]:
            stolen["done"] = True
            store.acquire_lease("d", "0.8.1", 202, clock())
        return []

    ticks = loop.run(store, "d", "0.8.0", 101, loop.Deps(snapshot=snapshot, post=d.post, queue=d.queue, restacker=d.restacker, pool=d.pool, clock=clock, sleep=clock.sleep))

    assert len(ticks) == 1
