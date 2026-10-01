#!/usr/bin/env python3
"""The action contract: one durable record per side effect an executor owns, under one owner generation.

    actions.py show     --incident ID [--json]
    actions.py list     [--json]
    actions.py transfer --incident ID --expect-generation N --to OWNER
    actions.py ack      --incident ID --owner OWNER

STDLIB ONLY. One JSON file per incident at ``~/.claude/long-running/incidents/<id>.json``
holds the incident's facts, its owner and owner generation, every action, every open
decision, and the milestones. Every write holds an ``fcntl`` lock on the file's sibling
``.lock`` and replaces the file atomically. A write names the owner generation it acts
under and fails with :class:`StaleGeneration` when the stored one differs, so a former
owner cannot move an action after a transfer.

An action moves ``accepted`` -> ``started`` -> ``completed`` -> ``verified``, or ends
``failed``. ``started`` is persisted before the side effect runs and ``completed`` after
its response, so an action found ``started`` after a crash had its response lost: it
becomes ``unverifiable`` and only a read of external state settles it, never a blind
retry. Accepting an ``action_id`` that already exists returns the stored action, so a
duplicate delivery never creates a second side effect.

A transfer is two steps: ``transfer`` names a pending owner under the current generation,
and that owner's ``ack`` makes it the owner at the next generation. Nothing here stops or
signals a process; the former owner's next write fails on the generation and it exits.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

STATUSES = ("accepted", "started", "completed", "verified", "failed", "unverifiable")
OPEN = ("accepted", "started", "completed", "unverifiable")
TERMINAL = ("verified", "failed")


class StaleGeneration(RuntimeError):
    """The stored owner generation moved past the writer's, so the writer no longer owns the incident."""


class UnknownAction(KeyError):
    """No action with this id exists in the incident."""


def stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Action:
    incident_id: str
    action_id: str
    kind: str
    target: str
    authority_ref: str | None
    input_revision: int
    owner_generation: int
    accepted_at: str
    dispatch_id: str | None = None
    started_at: str | None = None
    deadline: str | None = None
    status: str = "accepted"
    response: dict | None = None
    verification_receipt: dict | None = None
    reason: str | None = None

    @classmethod
    def load(cls, record: dict) -> Action:
        return cls(**record)

    def overdue(self, now: datetime) -> bool:
        return self.status in OPEN and self.deadline is not None and parse_stamp(self.deadline) <= now


@dataclass
class Incident:
    incident_id: str
    owner: str
    owner_generation: int
    opened_at: str
    facts: dict
    input_revision: int = 1
    status: str = "open"
    pending_owner: str | None = None
    actions: dict[str, Action] = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)
    milestones: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, record: dict) -> Incident:
        actions = {key: Action.load(value) for key, value in record.pop("actions").items()}
        return cls(**record, actions=actions)

    def dump(self) -> dict:
        return asdict(self)

    def action(self, action_id: str) -> Action:
        if action_id not in self.actions:
            raise UnknownAction(action_id)
        return self.actions[action_id]

    def of_kind(self, kind: str) -> list[Action]:
        return [action for action in self.actions.values() if action.kind == kind]

    def accept(self, action_id: str, kind: str, target: str, authority_ref: str | None, at: datetime) -> Action:
        if action_id not in self.actions:
            self.actions[action_id] = Action(
                self.incident_id, action_id, kind, target, authority_ref, self.input_revision, self.owner_generation, stamp(at)
            )
        return self.actions[action_id]

    def start(self, action_id: str, at: datetime, deadline: datetime | None = None, dispatch_id: str | None = None) -> Action:
        action = self.action(action_id)
        if action.status != "accepted":
            raise ValueError(f"{action_id} is {action.status}, not accepted")
        action.status, action.started_at = "started", stamp(at)
        action.deadline = stamp(deadline) if deadline else None
        action.dispatch_id = dispatch_id or action.dispatch_id
        return action

    def complete(self, action_id: str, response: dict, dispatch_id: str | None = None) -> Action:
        action = self.action(action_id)
        if action.status not in ("started", "unverifiable"):
            raise ValueError(f"{action_id} is {action.status}, not started")
        action.status, action.response = "completed", response
        action.dispatch_id = dispatch_id or action.dispatch_id
        return action

    def verify(self, action_id: str, receipt: dict) -> Action:
        action = self.action(action_id)
        if action.status in TERMINAL:
            raise ValueError(f"{action_id} is already {action.status}")
        action.status, action.verification_receipt = "verified", receipt
        return action

    def fail(self, action_id: str, reason: str) -> Action:
        action = self.action(action_id)
        if action.status in TERMINAL:
            raise ValueError(f"{action_id} is already {action.status}")
        action.status, action.reason = "failed", reason
        return action

    def lose(self, action_id: str) -> Action:
        action = self.action(action_id)
        if action.status != "started":
            raise ValueError(f"{action_id} is {action.status}, not started")
        action.status, action.reason = "unverifiable", "response lost; settle from external state"
        return action

    def decide(self, key: str, text: str, at: datetime) -> bool:
        if any(decision["key"] == key for decision in self.decisions):
            return False
        self.decisions.append({"key": key, "text": text, "at": stamp(at), "resolved_at": None})
        return True

    def resolve(self, key: str, at: datetime) -> None:
        for decision in self.decisions:
            if decision["key"] == key and decision["resolved_at"] is None:
                decision["resolved_at"] = stamp(at)

    def open_decisions(self) -> list[dict]:
        return [decision for decision in self.decisions if decision["resolved_at"] is None]

    def milestone(self, name: str, text: str, at: datetime) -> bool:
        if any(entry["name"] == name for entry in self.milestones):
            return False
        self.milestones.append({"name": name, "text": text, "at": stamp(at)})
        return True

    def reached(self, name: str) -> dict | None:
        return next((entry for entry in self.milestones if entry["name"] == name), None)


def incidents_dir() -> Path:
    return Path.home() / ".claude" / "long-running" / "incidents"


class Store:
    """The executor-owned state store: one locked JSON file per incident."""

    def __init__(self, root: Path | None = None):
        self.root = root or incidents_dir()

    def path(self, incident_id: str) -> Path:
        return self.root / f"{incident_id}.json"

    @contextmanager
    def _locked(self, incident_id: str) -> Iterator[Path]:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / f".{incident_id}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield self.path(incident_id)

    def _write(self, path: Path, incident: Incident) -> None:
        staged = path.with_name(f".{path.name}.{os.getpid()}")
        staged.write_text(json.dumps(incident.dump(), indent=2) + "\n")
        staged.replace(path)

    def create(self, incident: Incident) -> Incident:
        with self._locked(incident.incident_id) as path:
            if path.exists():
                return Incident.load(json.loads(path.read_text()))
            self._write(path, incident)
            return incident

    def load(self, incident_id: str) -> Incident:
        path = self.path(incident_id)
        if not path.exists():
            raise SystemExit(f"no incident {incident_id} in {self.root}")
        return Incident.load(json.loads(path.read_text()))

    def ids(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.json")) if self.root.exists() else []

    @contextmanager
    def owned(self, incident_id: str, generation: int) -> Iterator[Incident]:
        with self._locked(incident_id) as path:
            incident = Incident.load(json.loads(path.read_text()))
            if incident.owner_generation != generation:
                raise StaleGeneration(f"{incident_id} is at owner generation {incident.owner_generation} ({incident.owner}), not {generation}")
            yield incident
            self._write(path, incident)

    @contextmanager
    def inputs(self, incident_id: str) -> Iterator[Incident]:
        with self._locked(incident_id) as path:
            incident = Incident.load(json.loads(path.read_text()))
            yield incident
            incident.input_revision += 1
            self._write(path, incident)

    def transfer(self, incident_id: str, expect_generation: int, to: str) -> Incident:
        with self.owned(incident_id, expect_generation) as incident:
            incident.pending_owner = to
        return incident

    def ack(self, incident_id: str, owner: str) -> Incident:
        with self._locked(incident_id) as path:
            incident = Incident.load(json.loads(path.read_text()))
            if incident.pending_owner != owner:
                raise SystemExit(f"{incident_id} has no pending transfer to {owner} (pending: {incident.pending_owner})")
            incident.owner, incident.pending_owner = owner, None
            incident.owner_generation += 1
            self._write(path, incident)
            return incident


def summary(incident: Incident) -> str:
    counts: dict[str, int] = {}
    for action in incident.actions.values():
        counts[action.status] = counts.get(action.status, 0) + 1
    tally = " ".join(f"{status}={counts[status]}" for status in STATUSES if status in counts)
    return (
        f"{incident.incident_id} {incident.status} owner={incident.owner} generation={incident.owner_generation} "
        f"actions: {tally or 'none'} decisions open={len(incident.open_decisions())}"
    )


def cmd_show(args: argparse.Namespace, store: Store) -> int:
    incident = store.load(args.incident)
    if args.json:
        print(json.dumps(incident.dump()))
        return 0
    print(summary(incident))
    for action in incident.actions.values():
        print(f"  {action.action_id} {action.status} target={action.target} authority={action.authority_ref or '-'}")
    for decision in incident.open_decisions():
        print(f"  decision {decision['key']}: {decision['text']}")
    return 0


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    incidents = [store.load(incident_id) for incident_id in store.ids()]
    if args.json:
        print(json.dumps([incident.dump() for incident in incidents]))
        return 0
    for incident in incidents:
        print(summary(incident))
    return 0


def cmd_transfer(args: argparse.Namespace, store: Store) -> int:
    incident = store.transfer(args.incident, args.expect_generation, args.to)
    print(f"{incident.incident_id} pending transfer to {args.to}; it takes effect when {args.to} acks")
    return 0


def cmd_ack(args: argparse.Namespace, store: Store) -> int:
    incident = store.ack(args.incident, args.owner)
    print(f"{incident.incident_id} owned by {incident.owner} at generation {incident.owner_generation}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="actions.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show", help="one incident's actions and open decisions")
    show.add_argument("--incident", required=True)
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_show)

    list_cmd = subparsers.add_parser("list", help="every incident in the store")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(handler=cmd_list)

    transfer = subparsers.add_parser("transfer", help="offer the incident to a new owner under the current generation")
    transfer.add_argument("--incident", required=True)
    transfer.add_argument("--expect-generation", required=True, type=int)
    transfer.add_argument("--to", required=True)
    transfer.set_defaults(handler=cmd_transfer)

    ack = subparsers.add_parser("ack", help="the pending owner takes the incident at the next generation")
    ack.add_argument("--incident", required=True)
    ack.add_argument("--owner", required=True)
    ack.set_defaults(handler=cmd_ack)

    return parser


def main(argv: list[str] | None = None, store: Store | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args, store or Store())
    except StaleGeneration as stale:
        print(str(stale), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
