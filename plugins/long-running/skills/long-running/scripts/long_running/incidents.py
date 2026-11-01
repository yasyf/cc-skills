"""Incident mode: while an open incident record names a PR or a lane, the drive's checks on that PR and lane warn instead of refusing, and the incident rules (approval, green required checks, squash, 0 deletes and 0 replaces) are the only gates (owner, 2:21 PM Nov 1: "you need ti find a way to get your tooling out if you have an incudent")."""

from __future__ import annotations

from collections.abc import Callable

from . import cci, grades

INCIDENT = "incident"


def open_incidents(run: Callable[[list[str]], str], drive: str) -> list[dict]:
    """Every open incident record on `drive`, read once per pass by the caller."""
    return [record for record in cci.current_records(run, drive, f"--kind={INCIDENT}") if (record["kind"], record["drive"], record.get("status")) == (INCIDENT, drive, "open")]


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
