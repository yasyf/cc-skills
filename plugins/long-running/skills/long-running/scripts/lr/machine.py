"""The pure state function: one PR's facts in, its state, mover, action and deadline out.

Nothing here reads a network or a clock. The supervisor collects :class:`Facts` once per
pass and calls :func:`classify` for every open PR; the returned :class:`Verdict` names the
one mover that acts and how long it has before the wait escalates. No state is called
stuck: stuck is a work item past its deadline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class State(StrEnum):
    PUSHED = "pushed"
    GRADING = "grading"
    NEEDS_VERDICT = "needs-verdict"
    NEEDS_RESTACK = "needs-restack"
    RED = "red"
    AWAITING_TAP = "awaiting-tap"
    READY = "ready"
    QUEUED = "queued"
    EVICTED = "evicted"
    LANDED = "landed"
    CLOSED = "closed"


class Mover(StrEnum):
    SUPERVISOR = "supervisor"
    POOL = "pool"
    LANE = "lane"
    OWNER = "owner"
    ROOT = "root"
    NOBODY = "nobody"


class Action(StrEnum):
    GRADE = "grade"
    WAIT_CI = "wait-ci"
    RESTACK = "restack"
    VERDICT = "verdict"
    FIX_RED = "fix-red"
    TAP = "tap"
    LIFT = "lift"
    ENQUEUE = "enqueue"
    DEQUEUE = "dequeue"
    WATCH_QUEUE = "watch-queue"
    NONE = "none"


SUPERVISOR_CLOCK = timedelta(minutes=10)
GRADE_CLOCK = timedelta(minutes=10)
LANE_CLOCK = timedelta(minutes=20)
QUEUE_CLOCK = timedelta(minutes=30)
CI_CLOCK = timedelta(minutes=120)
OWNER_CLOCK = timedelta(hours=12)
TERMINAL = frozenset({State.LANDED, State.CLOSED})


@dataclass(frozen=True)
class Facts:
    """Everything one pass knows about one PR, read once from GitHub, the queue, CI and the store's fact rows."""

    repo: str
    number: int
    head: str
    base: str
    lane: str
    open: bool = True
    merged: bool = False
    ci: str = "pending"
    mergeable: str = "unknown"
    queue: str = "none"
    queued_head: str | None = None
    resubmitted: int = 0
    finder: str | None = None
    fidelity: str | None = None
    verdict: bool = False
    tap: str | None = None
    exempt: bool = False
    hold: str | None = None
    carries: tuple[int, ...] = ()

    @property
    def green(self) -> bool:
        return self.ci == "success"

    @property
    def graded(self) -> bool:
        return self.finder is not None and self.fidelity is not None

    @property
    def cleared(self) -> bool:
        return self.graded and ((self.finder == "clear" and self.fidelity == "pass") or self.verdict)

    @property
    def tapped(self) -> bool:
        return self.tap == "tapped" or self.exempt

    @property
    def restackable(self) -> bool:
        return self.mergeable in ("conflict", "behind") or bool(self.carries)

    @property
    def landable(self) -> bool:
        return self.green and self.mergeable == "clean" and not self.carries and self.cleared and self.tapped and self.hold is None


@dataclass(frozen=True)
class Verdict:
    state: State
    mover: Mover
    action: Action
    deadline: timedelta

    @property
    def opens_item(self) -> bool:
        return self.action is not Action.NONE


def classify(f: Facts) -> Verdict:
    if f.merged:
        return Verdict(State.LANDED, Mover.NOBODY, Action.NONE, timedelta(0))
    if not f.open:
        return Verdict(State.CLOSED, Mover.NOBODY, Action.NONE, timedelta(0))
    if f.ci == "failure":
        return Verdict(State.RED, Mover.LANE, Action.FIX_RED, LANE_CLOCK)
    if f.queue == "evicted" and f.landable:
        if f.resubmitted >= 1:
            return Verdict(State.RED, Mover.LANE, Action.FIX_RED, LANE_CLOCK)
        return Verdict(State.EVICTED, Mover.SUPERVISOR, Action.ENQUEUE, SUPERVISOR_CLOCK)
    if f.queue == "queued":
        if f.queued_head != f.head:
            return Verdict(State.AWAITING_TAP, Mover.SUPERVISOR, Action.DEQUEUE, SUPERVISOR_CLOCK)
        return Verdict(State.QUEUED, Mover.SUPERVISOR, Action.WATCH_QUEUE, QUEUE_CLOCK)
    if f.restackable:
        return Verdict(State.NEEDS_RESTACK, Mover.SUPERVISOR, Action.RESTACK, SUPERVISOR_CLOCK)
    if not f.graded:
        state = State.PUSHED if f.finder is None and f.fidelity is None and not f.green else State.GRADING
        return Verdict(state, Mover.POOL, Action.GRADE, GRADE_CLOCK)
    if not f.cleared:
        return Verdict(State.NEEDS_VERDICT, Mover.LANE, Action.VERDICT, LANE_CLOCK)
    if not f.green or f.mergeable != "clean":
        return Verdict(State.GRADING, Mover.SUPERVISOR, Action.WAIT_CI, CI_CLOCK)
    if f.hold is not None:
        return Verdict(State.AWAITING_TAP, Mover(f.hold), Action.LIFT, OWNER_CLOCK)
    if not f.tapped:
        return Verdict(State.AWAITING_TAP, Mover.OWNER, Action.TAP, OWNER_CLOCK)
    return Verdict(State.READY, Mover.SUPERVISOR, Action.ENQUEUE, SUPERVISOR_CLOCK)
