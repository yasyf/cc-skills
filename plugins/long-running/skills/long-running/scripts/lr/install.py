"""`lr install <version>`: the one path to new code, announced before and after, with the lease as the handoff.

Install preflights first and refuses before anything is swapped when a check fails. It then
posts `installing <version>`, installs the release, starts the successor, and waits for the
successor to take the drive's lease. The running supervisor exits on its own next tick (see
:mod:`lr.loop`). A successor that has not taken the lease within three minutes is an
escalation, never silence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from lr.store import Store

HANDOFF = timedelta(minutes=3)
POLL = timedelta(seconds=5)


class Refused(RuntimeError):
    """A preflight failed; nothing was installed."""


@dataclass(frozen=True)
class Steps:
    preflight: Callable[[], list[str]]
    install: Callable[[str], None]
    start: Callable[[str], int]
    post: Callable[..., object]
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]


def install(store: Store, drive: str, version: str, steps: Steps) -> int:
    """Install `version` and return the successor's lease generation."""
    if failed := steps.preflight():
        raise Refused(f"install {version} refused before the swap: " + "; ".join(failed))
    before = store.lease(drive).generation
    steps.post("state", to=["supervisor"], lane="install", text=f"installing {version}")
    steps.install(version)
    steps.start(version)
    deadline = steps.clock() + HANDOFF
    while steps.clock() < deadline:
        lease = store.lease(drive)
        if lease.generation > before and lease.version == version:
            steps.post("state", to=["supervisor"], lane="install", text=f"serving {version} on generation {lease.generation}")
            return lease.generation
        steps.sleep(POLL.total_seconds())
    store.escalate(f"install:{version}", "no successor within 3 minutes", "root", steps.clock())
    steps.post("escalation", to=["root"], subject=f"install:{version}", cause="no successor within 3 minutes")
    raise RuntimeError(f"install {version}: the successor did not take the lease within 3 minutes; generation {before} still serves")
