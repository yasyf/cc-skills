"""A pass over recorded facts: every wait gets its item, every overdue item escalates once, every silent worker is released."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from lr.machine import Facts, Mover
from lr.store import Store
from lr.supervisor import run_pass

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
REPO = "org/repo"


def facts(**fields) -> Facts:
    base = dict(repo=REPO, number=45583, head=HEAD, base="dev", lane="lane-7", ci="success", mergeable="clean", finder="clear", fidelity="pass", tap="tapped")
    return Facts(**{**base, **fields})


class Posts(list):
    def __call__(self, kind, to=(), **body):
        self.append((kind, tuple(to), body))


@pytest.fixture
def store():
    return Store.memory()


def test_a_landable_pr_gets_one_enqueue_item_the_supervisor_acts_on_itself(store):
    posts = Posts()
    out = run_pass(store, [facts()], T0, posts)

    [item] = store.open_items(REPO, 45583)
    assert (item.kind, item.mover, item.deadline) == ("enqueue", "supervisor", T0 + timedelta(minutes=10))
    assert [(f.number, v.action) for f, v in out.acts] == [(45583, "enqueue")]
    assert posts == []


def test_a_finding_without_a_verdict_is_posted_to_the_owning_lane_with_its_deadline(store):
    posts = Posts()
    run_pass(store, [facts(number=45470, finder="ask")], T0, posts)

    [item] = store.open_items(REPO, 45470)
    assert posts == [("item", ("lane-7",), dict(item=item.id, kind="verdict", pr=45470, head=HEAD, mover="lane", deadline="2026-10-30T21:20:00Z"))]


def test_a_missing_tap_is_the_owners_item(store):
    posts = Posts()
    run_pass(store, [facts(tap=None)], T0, posts)

    assert [(kind, to, body["kind"]) for kind, to, body in posts] == [("item", ("owner",), "tap")]


def test_the_same_facts_twice_open_nothing_and_post_nothing(store):
    posts = Posts()
    run_pass(store, [facts(number=45470, finder="ask")], T0, posts)
    second = run_pass(store, [facts(number=45470, finder="ask")], T0 + timedelta(minutes=5), posts)

    assert second.opened == []
    assert len(posts) == 1
    assert len(store.open_items(REPO, 45470)) == 1


def test_a_state_change_closes_the_old_item_and_opens_the_new_one(store):
    posts = Posts()
    run_pass(store, [facts(fidelity=None)], T0, posts)
    run_pass(store, [facts()], T0 + timedelta(minutes=4), posts)

    [item] = store.open_items(REPO, 45583)
    assert item.kind == "enqueue"
    assert [row["kind"] for row in store.db.execute("SELECT kind FROM items WHERE closed IS NOT NULL")] == ["grade"]


def test_landed_closes_every_open_item(store):
    run_pass(store, [facts(tap=None)], T0, Posts())
    run_pass(store, [facts(merged=True, open=False)], T0 + timedelta(hours=1), Posts())

    assert store.open_items(REPO, 45583) == []
    assert store.prs(REPO)[0]["state"] == "landed"


def test_an_overdue_lane_item_escalates_to_root_once(store):
    posts = Posts()
    given = [facts(number=45470, finder="ask")]
    run_pass(store, given, T0, posts)
    late = run_pass(store, given, T0 + timedelta(minutes=20), posts)
    later = run_pass(store, given, T0 + timedelta(minutes=40), posts)

    assert late.escalated == [("org/repo#45470", "verdict overdue", Mover.ROOT)]
    assert later.escalated == []
    assert [(kind, to, body) for kind, to, body in posts if kind == "escalation"] == [("escalation", ("root",), dict(subject="org/repo#45470", cause="verdict overdue"))]


def test_an_overdue_grade_escalates_to_the_supervisor_and_an_overdue_tap_to_the_owner(store):
    posts = Posts()
    given = [facts(number=1, fidelity=None), facts(number=2, tap=None)]
    run_pass(store, given, T0, posts)
    out = run_pass(store, given, T0 + timedelta(hours=12), posts)

    assert sorted(out.escalated) == [("org/repo#1", "grade overdue", Mover.SUPERVISOR), ("org/repo#2", "tap overdue", Mover.OWNER)]


def test_a_worker_silent_fifteen_minutes_loses_its_items_and_root_hears_once(store):
    posts = Posts()
    given = [facts(number=45651, ci="failure")]
    run_pass(store, given, T0, posts)
    [item] = store.open_items(REPO, 45651)
    store.heartbeat("lane-7", 1, T0 + timedelta(minutes=1), item=item.id)

    quiet = run_pass(store, given, T0 + timedelta(minutes=10), posts)
    silent = run_pass(store, given, T0 + timedelta(minutes=16), posts)
    again = run_pass(store, given, T0 + timedelta(minutes=18), posts)

    assert quiet.released == {}
    assert [held.id for held in silent.released["lane-7"]] == [item.id]
    assert store.open_items(REPO, 45651)[0].worker is None
    assert again.released == {}
    assert [body for kind, to, body in posts if kind == "escalation"] == [dict(subject="worker:lane-7", cause="silent 15 minutes")]


def test_a_grading_job_whose_wrapper_died_frees_its_item_for_the_next_dispatch(store):
    posts = Posts()
    given = [facts(number=46052, finder=None)]
    run_pass(store, given, T0, posts)
    [grade] = store.open_items(REPO, 46052)
    store.heartbeat("sprite-3/finder/46052", 1, T0, item=grade.id)

    out = run_pass(store, given, T0 + timedelta(minutes=15), posts)

    assert [held.kind for held in out.released["sprite-3/finder/46052"]] == ["grade"]
    assert store.open_items(REPO, 46052)[0].worker is None
    assert [row["name"] for row in store.workers()] == []
