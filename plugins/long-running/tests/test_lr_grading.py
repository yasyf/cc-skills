"""Every ungraded head is graded once; a dead job comes back, at once when its executor is gone; a same-patch rebase is not graded twice."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lr.machine import Facts
from lr.movers import grading
from lr.store import Store
from lr.supervisor import run_pass

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
REPO = "org/repo"
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
NEW = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"


def facts(number=45489, head=HEAD, **fields):
    base = dict(repo=REPO, number=number, head=head, base="dev", lane="lane-7", ci="success", mergeable="clean", finder=None, fidelity=None, tap="tapped")
    return Facts(**{**base, **fields})


class FakePool:
    def __init__(self):
        self.jobs = []
        self.box = "sprite-1"
        self.gone = set()

    def dispatch(self, grader, repo, number, head):
        self.jobs.append((grader, number, head))
        return f"{self.box}/{grader}/{number}"

    def alive(self, worker):
        return worker not in self.gone


def pass_items(store, snapshot, now=T0):
    run_pass(store, snapshot, now, lambda *a, **k: None)
    return store.open_items(REPO), {f.number: f for f in snapshot}


def test_an_ungraded_head_gets_both_graders_once():
    store, pool = Store.memory(), FakePool()
    items, snapshot = pass_items(store, [facts()])

    first = grading.act(store, pool, items, snapshot, T0, 1)
    second = grading.act(store, pool, store.open_items(REPO), snapshot, T0 + timedelta(minutes=2), 1)

    assert first.dispatched == [("finder", 45489, HEAD), ("fidelity", 45489, HEAD)]
    assert second.dispatched == []
    assert pool.jobs == [("finder", 45489, HEAD), ("fidelity", 45489, HEAD)]
    assert [row["name"] for row in store.workers()] == ["sprite-1/fidelity/45489", "sprite-1/finder/45489"]


def test_only_the_missing_grader_is_dispatched():
    store, pool = Store.memory(), FakePool()
    items, snapshot = pass_items(store, [facts(finder="clear")])

    grading.act(store, pool, items, snapshot, T0, 1)

    assert pool.jobs == [("fidelity", 45489, HEAD)]


def test_a_job_that_died_is_dispatched_again_after_the_deadline():
    store, pool = Store.memory(), FakePool()
    items, snapshot = pass_items(store, [facts()])
    grading.act(store, pool, items, snapshot, T0, 1)

    quiet = grading.act(store, pool, store.open_items(REPO), snapshot, T0 + timedelta(minutes=9), 1)
    late = grading.act(store, pool, store.open_items(REPO), snapshot, T0 + timedelta(minutes=10), 1)

    assert quiet.dispatched == []
    assert [grader for grader, _, _ in late.dispatched] == ["finder", "fidelity"]


def test_a_job_whose_executor_is_gone_is_dispatched_again_in_the_same_pass():
    store, pool = Store.memory(), FakePool()
    items, snapshot = pass_items(store, [facts()])
    grading.act(store, pool, items, snapshot, T0, 1)
    pool.gone = {"sprite-1/finder/45489", "sprite-1/fidelity/45489"}
    pool.box = "sprite-2"

    again = grading.act(store, pool, store.open_items(REPO), snapshot, T0 + timedelta(minutes=1), 1)

    assert [grader for grader, _, _ in again.dispatched] == ["finder", "fidelity"]
    assert store.open_items(REPO)[0].worker == "sprite-2/fidelity/45489"
    assert {row["name"] for row in store.workers()} == {"sprite-2/finder/45489", "sprite-2/fidelity/45489"}


def test_a_live_job_before_its_deadline_is_left_alone():
    store, pool = Store.memory(), FakePool()
    items, snapshot = pass_items(store, [facts()])
    grading.act(store, pool, items, snapshot, T0, 1)

    again = grading.act(store, pool, store.open_items(REPO), snapshot, T0 + timedelta(minutes=1), 1)

    assert again.dispatched == []


def test_a_stale_job_releases_the_item_and_the_next_pass_dispatches_again():
    store, pool = Store.memory(), FakePool()
    items, snapshot = pass_items(store, [facts()])
    grading.act(store, pool, items, snapshot, T0, 1)

    items, snapshot = pass_items(store, [facts()], T0 + timedelta(minutes=16))
    again = grading.act(store, pool, items, snapshot, T0 + timedelta(minutes=16), 1)

    assert store.open_items(REPO)[0].worker == "sprite-1/fidelity/45489"
    assert len(again.dispatched) == 2


def test_an_item_for_an_older_head_dispatches_nothing():
    store, pool = Store.memory(), FakePool()
    items, _ = pass_items(store, [facts()])

    grading.act(store, pool, items, {45489: facts(head=NEW)}, T0, 1)

    assert pool.jobs == []


def test_a_same_patch_rebase_carries_the_grades_and_the_verdict():
    store = Store.memory()
    for kind, value in {"finder": "ask", "fidelity": "pass", "verdict": "accept", "tap": "tapped"}.items():
        store.put_fact(REPO, 45489, HEAD, kind, value, T0)

    carried = grading.carry_grades(store, REPO, 45489, HEAD, NEW, {HEAD: "p1", NEW: "p1"}, T0)

    assert carried == ["fidelity", "finder", "verdict"]
    assert store.facts(REPO, 45489, NEW) == {"finder": "ask", "fidelity": "pass", "verdict": "accept"}


def test_a_changed_patch_carries_nothing_and_is_graded_again():
    store, pool = Store.memory(), FakePool()
    store.put_fact(REPO, 45489, HEAD, "finder", "clear", T0)
    store.put_fact(REPO, 45489, HEAD, "fidelity", "pass", T0)

    assert grading.carry_grades(store, REPO, 45489, HEAD, NEW, {HEAD: "p1", NEW: "p2"}, T0) == []
    items, snapshot = pass_items(store, [facts(head=NEW)])
    grading.act(store, pool, items, snapshot, T0, 1)
    assert [grader for grader, _, _ in pool.jobs] == ["finder", "fidelity"]
