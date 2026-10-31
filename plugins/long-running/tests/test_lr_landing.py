"""The landing mover does the one thing each state needs, once, through adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lr.machine import Facts, classify
from lr.movers import landing
from lr.store import Store
from lr.supervisor import run_pass

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
REPO = "org/repo"
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
NEW = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"


def facts(number=45583, **fields):
    base = dict(repo=REPO, number=number, head=HEAD, base="dev", lane="lane-7", ci="success", mergeable="clean", finder="clear", fidelity="pass", tap="tapped")
    return Facts(**{**base, **fields})


class FakeQueue:
    def __init__(self):
        self.enqueued, self.dequeued = [], []

    def enqueue(self, number, head):
        self.enqueued.append((number, head))

    def dequeue(self, number):
        self.dequeued.append(number)


class FakeRestacker:
    def __init__(self, result=NEW):
        self.result, self.calls = result, []

    def restack(self, number, head, base):
        self.calls.append((number, head, base))
        return self.result


def acts_for(store, snapshot, now=T0):
    return run_pass(store, snapshot, now, lambda *a, **k: None).acts


def test_a_landable_head_is_enqueued_the_same_pass():
    store, queue = Store.memory(), FakeQueue()

    done = landing.act(store, queue, FakeRestacker(), acts_for(store, [facts()]), T0)

    assert queue.enqueued == [(45583, HEAD)] and done.enqueued == [(45583, HEAD)]


def test_an_evicted_green_head_is_resubmitted_and_counted():
    store, queue = Store.memory(), FakeQueue()

    done = landing.act(store, queue, FakeRestacker(), acts_for(store, [facts(number=45711, queue="evicted")]), T0)

    assert queue.enqueued == [(45711, HEAD)] and done.resubmitted == [(45711, HEAD)]
    assert store.facts(REPO, 45711, HEAD)[landing.RESUBMITTED] == "1"


def test_a_second_eviction_is_not_resubmitted_but_goes_red_to_the_lane():
    store = Store.memory()
    store.put_fact(REPO, 45711, HEAD, landing.RESUBMITTED, "1", T0)
    evicted = facts(number=45711, queue="evicted", resubmitted=1)

    assert classify(evicted).state == "red"
    assert landing.act(store, FakeQueue(), FakeRestacker(), acts_for(store, [evicted]), T0).resubmitted == []


def test_a_queued_pr_whose_head_moved_is_dequeued():
    store, queue = Store.memory(), FakeQueue()

    done = landing.act(store, queue, FakeRestacker(), acts_for(store, [facts(number=45744, queue="queued", queued_head=NEW)]), T0)

    assert queue.dequeued == [45744] and done.dequeued == [45744]
    assert queue.enqueued == []


def test_a_conflict_is_restacked_mechanically_and_the_new_head_recorded():
    store, restacker = Store.memory(), FakeRestacker(NEW)

    done = landing.act(store, FakeQueue(), restacker, acts_for(store, [facts(number=45410, mergeable="conflict")]), T0)

    assert restacker.calls == [(45410, HEAD, "dev")]
    assert done.restacked == [(45410, HEAD, NEW)]
    assert store.facts(REPO, 45410, HEAD)[landing.RESTACKED] == NEW


def test_a_semantic_conflict_is_tried_once_then_left_to_the_lane():
    store, restacker = Store.memory(), FakeRestacker("")
    snapshot = [facts(number=45580, mergeable="conflict")]

    first = landing.act(store, FakeQueue(), restacker, acts_for(store, snapshot), T0)
    second = landing.act(store, FakeQueue(), restacker, acts_for(store, snapshot, T0 + timedelta(minutes=5)), T0 + timedelta(minutes=5))

    assert first.conflicts == [45580] and second.conflicts == []
    assert len(restacker.calls) == 1
    assert store.facts(REPO, 45580, HEAD)[landing.RESTACKED] == "conflict"


def test_an_untapped_or_red_head_is_never_enqueued():
    store, queue = Store.memory(), FakeQueue()

    landing.act(store, queue, FakeRestacker(), acts_for(store, [facts(number=1, tap=None), facts(number=2, ci="failure"), facts(number=3, finder="ask")]), T0)

    assert queue.enqueued == []


def test_one_action_per_pr_per_pass_and_nothing_twice_while_the_facts_hold():
    store, queue = Store.memory(), FakeQueue()

    landing.act(store, queue, FakeRestacker(), acts_for(store, [facts()]), T0)
    landing.act(store, queue, FakeRestacker(), acts_for(store, [facts(queue="queued", queued_head=HEAD)], T0 + timedelta(minutes=5)), T0 + timedelta(minutes=5))

    assert queue.enqueued == [(45583, HEAD)]
    assert queue.dequeued == []
