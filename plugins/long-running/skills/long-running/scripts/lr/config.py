"""`runner.json` carries configuration only; workers live in the store from their first heartbeat.

The file names the repository, the drive, the checkout the supervisor lands from, the drive
author and label that admit a PR, the movers the supervisor runs, the Orca run its lanes
launch into, the box the graders run on, the Slack channels, comma-separated, whose Datadog
and Sentry alerts the supervisor holds to an incident record, the lanes besides root whose `go`
may name an exempt class, and, for the landing mover, the trunk branch, the repository's queue script
relative to the checkout, and the command that prints the queue's key at call time. Any other key is refused by name, so a list of
desks or workers in the config cannot collide with the ones that register. `movers` has no
default: each mover cuts over when the file names it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

MOVERS = ("lanes", "shadow", "landing", "grading", "items")


@dataclass(frozen=True)
class Config:
    repo: str
    drive: str
    checkout: str
    author: str
    label: str
    movers: tuple[str, ...]
    orca_run: str = ""
    remote_box: str = ""
    go_lanes: str = ""
    trunk: str = "main"
    queue_script: str = ""
    queue_key: str = ""
    alert_channels: str = ""

    @property
    def go_lane_names(self) -> tuple[str, ...]:
        return tuple(name.strip() for name in self.go_lanes.split(",") if name.strip())


class BadConfig(ValueError):
    """The file names something the store owns, or lacks a field the supervisor needs."""


def load(path: Path) -> Config:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise BadConfig(f"{path}: must be a JSON object")
    known = {field.name for field in fields(Config)}
    required = {field.name for field in fields(Config) if field.default is field.default_factory}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise BadConfig(f"{path}: {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not configuration; workers and desks register themselves with their first heartbeat and never appear here")
    missing = sorted(required - set(raw))
    if missing:
        raise BadConfig(f"{path}: missing {', '.join(missing)}")
    movers = raw.pop("movers")
    if not isinstance(movers, list) or not movers or any(mover not in MOVERS for mover in movers):
        raise BadConfig(f"{path}: movers must be a non-empty list drawn from {', '.join(MOVERS)}, got {movers!r}")
    for name, value in raw.items():
        if not isinstance(value, str) or (name in required and not value.strip()):
            raise BadConfig(f"{path}: {name} must be non-empty text")
    return Config(**raw, movers=tuple(movers))
