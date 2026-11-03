"""The read-only shadow pass: where the v2 machine and the live runner disagree about a PR, one note per disagreement.

The shadow runs the same pass as the supervisor over its own store, acts on nothing, and
compares what the machine would do with what the queue shows: a head the machine has had
ready for a whole runner pass that the runner has not queued, and a head the runner queued
that the machine would hold, with the gates it still fails. Each disagreement is one note
per PR, head and cause, so a cutover reads the list and decides.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from lr import gates
from lr.machine import Action, Facts, State, Verdict
from lr.store import Store

RUNNER_PASS = timedelta(minutes=5)
SHADOW = "shadow"


def diffs(store: Store, snapshot: Iterable[Facts], verdicts: dict[int, Verdict], now: datetime) -> list[tuple[str, str]]:
    """Each `(subject, cause)` this pass newly disagrees on; an older one is never repeated."""
    out = []
    waiting = {(item.number, item.head): item for item in store.open_items() if item.kind == Action.ENQUEUE}
    for facts in snapshot:
        verdict = verdicts.get(facts.number)
        subject = f"#{facts.number} at {facts.head[:12]}"
        if facts.queue == "queued" and not facts.landable:
            cause = "the runner queued it; v2 holds it: " + "; ".join(gates.missing(facts))
        elif verdict and verdict.state in (State.READY, State.EVICTED) and (item := waiting.get((facts.number, facts.head))) and now - item.created >= RUNNER_PASS:
            cause = f"v2 would have queued it at {item.created:%H:%MZ}; the runner has not"
        else:
            continue
        if store.escalate(subject, cause, SHADOW, now):
            out.append((subject, cause))
    return out
