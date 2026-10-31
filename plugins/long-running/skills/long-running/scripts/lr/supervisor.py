"""One pass of the supervisor over a snapshot of facts.

A pass classifies every PR, opens the one work item its state needs and closes the rest,
escalates each overdue item once per cause to the next mover up, and releases the items of
every worker silent past fifteen minutes. It reads no network: the facts arrive as
:class:`lr.machine.Facts`, the clock is an argument, and every bus write goes through the
`post` callable it is given, so a test replays a pass over recorded facts and reads the
records it would have written.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime

from lr.machine import TERMINAL, Action, Facts, Mover, Verdict, classify
from lr.store import Item, Store, parse, stamp

Post = Callable[..., object]

ESCALATES_TO = {Mover.POOL: Mover.SUPERVISOR, Mover.LANE: Mover.ROOT, Mover.SUPERVISOR: Mover.ROOT, Mover.ROOT: Mover.OWNER, Mover.OWNER: Mover.OWNER}
SAME_PASS = frozenset({Action.ENQUEUE, Action.DEQUEUE, Action.RESTACK, Action.WAIT_CI, Action.WATCH_QUEUE})


@dataclass
class Outcome:
    verdicts: dict[int, Verdict] = field(default_factory=dict)
    opened: list[Item] = field(default_factory=list)
    acts: list[tuple[Facts, Verdict]] = field(default_factory=list)
    escalated: list[tuple[str, str, Mover]] = field(default_factory=list)
    released: dict[str, list[Item]] = field(default_factory=dict)


def audience(verdict: Verdict, facts: Facts) -> str:
    return facts.lane if verdict.mover is Mover.LANE else str(verdict.mover)


def run_pass(store: Store, snapshot: Iterable[Facts], now: datetime, post: Post) -> Outcome:
    out = Outcome()
    now = parse(stamp(now))
    for facts in snapshot:
        verdict = classify(facts)
        out.verdicts[facts.number] = verdict
        store.put_pr(facts.repo, facts.number, facts.head, facts.base, facts.lane, str(verdict.state), now)
        if verdict.state in TERMINAL:
            store.close_items(facts.repo, facts.number, now)
            continue
        store.close_items(facts.repo, facts.number, now, keep=str(verdict.action))
        item = store.open_item(str(verdict.action), facts.repo, facts.number, facts.head, str(verdict.mover), now, verdict.deadline)
        if item.created == now:
            out.opened.append(item)
            if verdict.mover is not Mover.SUPERVISOR:
                post("item", to=[audience(verdict, facts)], item=item.id, kind=str(verdict.action), pr=facts.number, head=facts.head, mover=str(verdict.mover), deadline=stamp(item.deadline))
        if verdict.mover is Mover.SUPERVISOR and verdict.action in SAME_PASS:
            out.acts.append((facts, verdict))
    for item in store.overdue(now):
        to = ESCALATES_TO[Mover(item.mover)]
        subject, cause = f"{item.repo}#{item.number}", f"{item.kind} overdue"
        if store.escalate(subject, cause, to, now):
            out.escalated.append((subject, cause, to))
            post("escalation", to=[str(to)], subject=subject, cause=cause)
    for name in store.stale_workers(now):
        out.released[name] = store.release_worker(name)
        subject, cause = f"worker:{name}", "silent 15 minutes"
        if store.escalate(subject, cause, Mover.ROOT, now):
            out.escalated.append((subject, cause, Mover.ROOT))
            post("escalation", to=["root"], subject=subject, cause=cause)
    return out
