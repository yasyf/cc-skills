"""Install refuses before the swap, announces before and after, and the lease is the handoff."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from lr import install
from lr.store import Store

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)


class World:
    def __init__(self, store, preflight=(), takes_lease=True):
        self.store, self.failures, self.takes_lease = store, list(preflight), takes_lease
        self.now, self.installed, self.started, self.posts = T0, [], [], []

    def steps(self):
        return install.Steps(preflight=lambda: self.failures, install=self.installed.append, start=self.start, post=self.post, clock=lambda: self.now, sleep=self.sleep)

    def start(self, version):
        self.started.append(version)
        return 4242

    def post(self, kind, to=(), **body):
        self.posts.append((kind, tuple(to), body))

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)
        if self.takes_lease and self.started:
            self.store.acquire_lease("d", self.started[-1], 4242, self.now)


@pytest.fixture
def store():
    store = Store.memory()
    store.acquire_lease("d", "0.8.0", 101, T0)
    return store


def test_a_failed_preflight_refuses_before_anything_is_swapped(store):
    world = World(store, preflight=["GitHub token missing", "sprite credentials expired"])

    with pytest.raises(install.Refused, match="install 0.8.1 refused before the swap: GitHub token missing; sprite credentials expired"):
        install.install(store, "d", "0.8.1", world.steps())

    assert world.installed == [] and world.started == [] and world.posts == []
    assert store.lease("d").generation == 1


def test_install_announces_before_and_after_and_returns_the_new_generation(store):
    world = World(store)

    generation = install.install(store, "d", "0.8.1", world.steps())

    assert generation == 2
    assert world.installed == ["0.8.1"] and world.started == ["0.8.1"]
    assert world.posts == [
        ("state", ("supervisor",), dict(lane="install", text="installing 0.8.1")),
        ("state", ("supervisor",), dict(lane="install", text="serving 0.8.1 on generation 2")),
    ]


def test_a_successor_that_never_takes_the_lease_escalates_within_three_minutes(store):
    world = World(store, takes_lease=False)

    with pytest.raises(RuntimeError, match="did not take the lease within 3 minutes; generation 1 still serves"):
        install.install(store, "d", "0.8.1", world.steps())

    assert world.now == T0 + timedelta(minutes=3)
    assert world.posts[-1] == ("escalation", ("root",), dict(subject="install:0.8.1", cause="no successor within 3 minutes"))
    assert [row["cause"] for row in store.escalations("install:0.8.1")] == ["no successor within 3 minutes"]
