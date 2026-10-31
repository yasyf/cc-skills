"""The grading mover: every ungraded head gets its finder and fidelity jobs, once, on the pool.

A grade item with no worker, or one past its deadline, has its missing graders dispatched
through the pool adapter; each job beats as a worker holding the item, so a job that dies
goes stale and its item comes back. Grades land as `grade` records on the bus and close the
item through the facts; the mover never reads results. A rebase to a patch-identical head
carries the grades and the verdict, so the new head is not graded twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from lr.machine import Facts
from lr.store import Item, Store

GRADERS = ("finder", "fidelity")
CARRIED = frozenset({"finder", "fidelity", "verdict"})


class Pool(Protocol):
    def dispatch(self, grader: str, repo: str, number: int, head: str) -> str:
        """Start one grading job; return its worker name."""
        ...


@dataclass
class Done:
    dispatched: list[tuple[str, int, str]] = field(default_factory=list)


def missing(facts: Facts) -> list[str]:
    return [grader for grader in GRADERS if getattr(facts, grader) is None]


def act(store: Store, pool: Pool, items: list[Item], snapshot: dict[int, Facts], now: datetime, generation: int) -> Done:
    done = Done()
    for item in items:
        if item.kind != "grade" or item.number not in snapshot or not (item.worker is None or item.deadline <= now):
            continue
        facts = snapshot[item.number]
        if facts.head != item.head:
            continue
        for grader in missing(facts):
            name = pool.dispatch(grader, facts.repo, facts.number, facts.head)
            store.heartbeat(name, generation, now, item=item.id)
            done.dispatched.append((grader, facts.number, facts.head))
    return done


def carry_grades(store: Store, repo: str, number: int, old_head: str, new_head: str, patch_ids: dict[str, str], at: datetime) -> list[str]:
    """Copy the grades and verdict of `old_head` onto a patch-identical `new_head`; the kinds carried."""
    if old_head == new_head or patch_ids.get(old_head) is None or patch_ids.get(old_head) != patch_ids.get(new_head):
        return []
    carried = []
    for kind, value in store.facts(repo, number, old_head).items():
        if kind in CARRIED:
            store.put_fact(repo, number, new_head, kind, value, at)
            carried.append(kind)
    return sorted(carried)
