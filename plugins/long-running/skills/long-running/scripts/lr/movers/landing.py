"""The landing mover: `ready` and `evicted` heads into the queue, moved heads out of it, conflicts restacked.

One action per PR per pass, each a call on a queue or restack adapter a test replaces. The
mover writes what it did as facts (a resubmission count, a restack attempt) so the next
pass's classification sees it; it never narrates. A head that stops being landable between
the facts read and the action is still acted on from the facts the pass read: the next
pass reads the truth and corrects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from lr.machine import Action, Facts, Verdict
from lr.store import Store

RESUBMITTED = "resubmitted"
RESTACKED = "restacked"


class Queue(Protocol):
    def enqueue(self, number: int, head: str) -> None: ...
    def dequeue(self, number: int) -> None: ...


class Restacker(Protocol):
    def restack(self, number: int, head: str, base: str) -> str:
        """Rebase the branch mechanically; return the new head, or an empty string on a conflict only a lane can resolve."""
        ...


@dataclass
class Done:
    enqueued: list[tuple[int, str]] = field(default_factory=list)
    resubmitted: list[tuple[int, str]] = field(default_factory=list)
    dequeued: list[int] = field(default_factory=list)
    restacked: list[tuple[int, str, str]] = field(default_factory=list)
    conflicts: list[int] = field(default_factory=list)


def act(store: Store, queue: Queue, restacker: Restacker, acts: list[tuple[Facts, Verdict]], now: datetime) -> Done:
    done = Done()
    for facts, verdict in acts:
        if verdict.action is Action.ENQUEUE and facts.queue == "evicted":
            queue.enqueue(facts.number, facts.head)
            store.put_fact(facts.repo, facts.number, facts.head, RESUBMITTED, str(facts.resubmitted + 1), now)
            done.resubmitted.append((facts.number, facts.head))
        elif verdict.action is Action.ENQUEUE:
            queue.enqueue(facts.number, facts.head)
            done.enqueued.append((facts.number, facts.head))
        elif verdict.action is Action.DEQUEUE:
            queue.dequeue(facts.number)
            done.dequeued.append(facts.number)
        elif verdict.action is Action.RESTACK:
            if store.facts(facts.repo, facts.number, facts.head).get(RESTACKED) == "conflict":
                continue
            new_head = restacker.restack(facts.number, facts.head, facts.base)
            store.put_fact(facts.repo, facts.number, facts.head, RESTACKED, new_head or "conflict", now)
            if new_head:
                done.restacked.append((facts.number, facts.head, new_head))
            else:
                done.conflicts.append(facts.number)
    return done
