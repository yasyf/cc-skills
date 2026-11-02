"""Incident mode: while an open incident record names a PR or a lane, the drive's process gates on that PR and lane (tap rows, class refusals, fallback clocks, identity checks) drop, and the plan check never does (owner, 2:21 PM Nov 1: "you need ti find a way to get your tooling out if you have an incudent"; 3:05 PM: "it is absolutely unacceptable to accumulate drift or diverge from the plan at all in fixing this")."""

from __future__ import annotations

import re
from collections.abc import Callable

from . import cci, grades

INCIDENT = "incident"
CLOSED = re.compile(r"\bINCIDENT \S+ CLOSED\b")


def closes(record: dict) -> bool:
    """Whether `record` is the root's `INCIDENT <slug> CLOSED` record, which ends incident mode for every lane it is addressed to."""
    return record["lane"] == "root" and CLOSED.search(record.get("text") or "") is not None


def ended(records: list[dict]) -> dict[str, int]:
    """Each lane a root close among `records` addresses, with the newest such close's seq."""
    return {lane: record["seq"] for record in sorted(records, key=lambda record: record["seq"]) if closes(record) for lane in record.get("to") or []}


def incident_records(run: Callable[[list[str]], str], drive: str) -> list[dict]:
    """Every incident record on `drive` with its current status, read once per pass by the caller."""
    return [record for record in cci.current_records(run, drive, f"--kind={INCIDENT}") if (record["kind"], record["drive"]) == (INCIDENT, drive)]


def open_incidents(records: list[dict]) -> list[dict]:
    """The open records of `records`, but a root close and each record older than a close addressed to every lane it names besides root (#301886: #47627 squashed after 301284 closed its incident)."""
    closed = ended(records)
    return [
        record
        for record in records
        if record.get("status") == "open" and not closes(record) and not ((lanes := {record["lane"], *(record.get("to") or [])} - {"root"}) and all(closed.get(lane, 0) > record["seq"] for lane in lanes))
    ]


def incident_scope(records: list[dict], drive: str, repo: str, pr: str | None, lane: str | None) -> dict | None:
    """The newest open incident record on `drive` among `records` that names `pr` of `repo` by its refs or text, or names `lane` as its poster or a recipient."""
    return max(
        (
            record
            for record in records
            if (record["kind"], record["drive"], record.get("status")) == (INCIDENT, drive, "open")
            and (pr and grades.about_pr(record, repo, pr) or lane and (record["lane"] == lane or lane in (record.get("to") or [])))
        ),
        key=lambda record: record["seq"],
        default=None,
    )
