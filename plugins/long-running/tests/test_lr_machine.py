"""Tonight's cases as a table over the pure state function: every row is a wait that had no mover."""

from __future__ import annotations

from datetime import timedelta

import pytest
from lr.machine import Action, Facts, Mover, State, Verdict, classify

HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
OLD_HEAD = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"


def facts(**fields) -> Facts:
    base = dict(repo="org/repo", number=45711, head=HEAD, base="dev", lane="lane-7", ci="success", mergeable="clean", finder="clear", fidelity="pass", tap="tapped")
    return Facts(**{**base, **fields})


LANDABLE = facts()

CASES = {
    "evicted and green again gets one enqueue the same pass": (facts(queue="evicted"), Verdict(State.EVICTED, Mover.SUPERVISOR, Action.ENQUEUE, timedelta(minutes=10))),
    "evicted a second time on a green head is red for its lane": (facts(queue="evicted", resubmitted=1), Verdict(State.RED, Mover.LANE, Action.FIX_RED, timedelta(minutes=20))),
    "evicted for its own red check is red for its lane": (facts(queue="evicted", ci="failure"), Verdict(State.RED, Mover.LANE, Action.FIX_RED, timedelta(minutes=20))),
    "evicted while the tap is missing waits on the owner, not the queue": (facts(queue="evicted", tap=None), Verdict(State.AWAITING_TAP, Mover.OWNER, Action.TAP, timedelta(hours=12))),
    "landable base with no row and no queue entry is enqueued": (LANDABLE, Verdict(State.READY, Mover.SUPERVISOR, Action.ENQUEUE, timedelta(minutes=10))),
    "landable base without a tap opens the owner's row": (facts(tap=None), Verdict(State.AWAITING_TAP, Mover.OWNER, Action.TAP, timedelta(hours=12))),
    "a withdrawn tap reopens the owner's row": (facts(tap="withdrawn"), Verdict(State.AWAITING_TAP, Mover.OWNER, Action.TAP, timedelta(hours=12))),
    "an exempt change lands without a tap": (facts(tap=None, exempt=True), Verdict(State.READY, Mover.SUPERVISOR, Action.ENQUEUE, timedelta(minutes=10))),
    "base in conflict with dev gets a restack order": (facts(mergeable="conflict"), Verdict(State.NEEDS_RESTACK, Mover.SUPERVISOR, Action.RESTACK, timedelta(minutes=10))),
    "base behind dev gets a restack order": (facts(mergeable="behind"), Verdict(State.NEEDS_RESTACK, Mover.SUPERVISOR, Action.RESTACK, timedelta(minutes=10))),
    "a branch carrying other open PRs' commits is restacked, never landed": (facts(carries=(45501, 45583)), Verdict(State.NEEDS_RESTACK, Mover.SUPERVISOR, Action.RESTACK, timedelta(minutes=10))),
    "a bot finding with no verdict is the lane's with a 20-minute clock": (facts(finder="ask"), Verdict(State.NEEDS_VERDICT, Mover.LANE, Action.VERDICT, timedelta(minutes=20))),
    "a failed plan check with no verdict is the lane's": (facts(fidelity="fail"), Verdict(State.NEEDS_VERDICT, Mover.LANE, Action.VERDICT, timedelta(minutes=20))),
    "a verdict on the finding clears it": (facts(finder="ask", verdict=True), Verdict(State.READY, Mover.SUPERVISOR, Action.ENQUEUE, timedelta(minutes=10))),
    "red CI is the lane's to fix": (facts(ci="failure"), Verdict(State.RED, Mover.LANE, Action.FIX_RED, timedelta(minutes=20))),
    "a fresh head with nothing graded is pushed and goes to the pool": (facts(ci="pending", finder=None, fidelity=None), Verdict(State.PUSHED, Mover.POOL, Action.GRADE, timedelta(minutes=10))),
    "a head missing one grade is grading on the pool": (facts(fidelity=None), Verdict(State.GRADING, Mover.POOL, Action.GRADE, timedelta(minutes=10))),
    "graded and cleared but CI still running waits on CI, never on the owner": (facts(ci="pending"), Verdict(State.GRADING, Mover.SUPERVISOR, Action.WAIT_CI, timedelta(minutes=120))),
    "mergeability not yet computed is not ready": (facts(mergeable="unknown"), Verdict(State.GRADING, Mover.SUPERVISOR, Action.WAIT_CI, timedelta(minutes=120))),
    "a root hold is root's to lift": (facts(hold="root"), Verdict(State.AWAITING_TAP, Mover.ROOT, Action.LIFT, timedelta(hours=12))),
    "a hold cleared by its holder lands": (facts(hold=None), Verdict(State.READY, Mover.SUPERVISOR, Action.ENQUEUE, timedelta(minutes=10))),
    "queued on this head is watched for thirty minutes": (facts(queue="queued", queued_head=HEAD), Verdict(State.QUEUED, Mover.SUPERVISOR, Action.WATCH_QUEUE, timedelta(minutes=30))),
    "queued on an older head is dequeued the same pass": (facts(queue="queued", queued_head=OLD_HEAD), Verdict(State.AWAITING_TAP, Mover.SUPERVISOR, Action.DEQUEUE, timedelta(minutes=10))),
    "merged is landed and nobody's": (facts(merged=True, open=False), Verdict(State.LANDED, Mover.NOBODY, Action.NONE, timedelta(0))),
    "closed without a merge is closed and nobody's": (facts(open=False), Verdict(State.CLOSED, Mover.NOBODY, Action.NONE, timedelta(0))),
}


@pytest.mark.parametrize("case", CASES, ids=list(CASES))
def test_every_wait_has_one_mover_and_a_deadline(case):
    given, expected = CASES[case]

    assert classify(given) == expected


@pytest.mark.parametrize("case", CASES, ids=list(CASES))
def test_a_wait_that_opens_an_item_is_bounded(case):
    verdict = classify(CASES[case][0])

    if verdict.opens_item:
        assert verdict.mover is not Mover.NOBODY
        assert verdict.deadline > timedelta(0)
    else:
        assert verdict.state in {State.LANDED, State.CLOSED}


def test_every_state_is_reachable():
    assert {classify(given).state for given, _ in CASES.values()} == set(State)


def test_landable_reads_every_gate():
    assert LANDABLE.landable
    for broken in (dict(ci="pending"), dict(mergeable="conflict"), dict(finder=None), dict(fidelity="fail"), dict(tap=None), dict(hold="owner"), dict(carries=(1,))):
        assert not facts(**broken).landable, broken


def test_a_conditional_tap_waits_on_the_owner_like_no_tap():
    assert classify(facts(tap="conditional", condition="only if covered")) == Verdict(State.AWAITING_TAP, Mover.OWNER, Action.TAP, timedelta(hours=12))
