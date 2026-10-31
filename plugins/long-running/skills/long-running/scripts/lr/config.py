"""`runner.json` carries configuration only; workers live in the store from their first heartbeat.

The file names the repository, the drive, the checkout the supervisor lands from, the drive
author and label that admit a PR, and the box the graders run on. Any other key is refused by
name, so a list of desks or workers in the config cannot collide with the ones that register.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Config:
    repo: str
    drive: str
    checkout: str
    author: str
    label: str
    remote_box: str = ""


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
    for name, value in raw.items():
        if not isinstance(value, str) or (name in required and not value.strip()):
            raise BadConfig(f"{path}: {name} must be non-empty text")
    return Config(**raw)
