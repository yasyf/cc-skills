"""The pool is sized by the queue in the same pass, never by a hand edit of a concurrency number."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lr.capacity import Pool, desired, plan

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)


def test_forty_heads_of_grading_ask_for_executors_in_the_same_pass():
    assert desired(queued_minutes=40 * 2 * 6, concurrency=4, cap=20) == 12


def test_an_empty_queue_asks_for_nothing():
    assert desired(0, concurrency=4, cap=20) == 0


def test_the_cap_holds():
    assert desired(10_000, concurrency=1, cap=8) == 8


def test_a_burst_grows_the_pool_at_once():
    grown = plan(Pool(2, 4, 20), queued_minutes=400, now=T0)

    assert (grown.executors, grown.idle_since) == (10, None)


def test_a_shrinking_queue_keeps_the_pool_until_it_is_empty():
    assert plan(Pool(10, 4, 20), queued_minutes=5, now=T0).executors == 10


def test_an_idle_pool_scales_down_only_after_sixty_minutes():
    idle = plan(Pool(10, 4, 20), queued_minutes=0, now=T0)
    still = plan(idle, queued_minutes=0, now=T0 + timedelta(minutes=59))
    gone = plan(still, queued_minutes=0, now=T0 + timedelta(minutes=60))

    assert (idle.executors, idle.idle_since) == (10, T0)
    assert still.executors == 10
    assert gone.executors == 0


def test_work_arriving_while_idle_resets_the_idle_clock():
    idle = plan(Pool(10, 4, 20), queued_minutes=0, now=T0)
    busy = plan(idle, queued_minutes=30, now=T0 + timedelta(minutes=30))

    assert busy.idle_since is None
