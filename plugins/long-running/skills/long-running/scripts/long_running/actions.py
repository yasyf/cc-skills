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
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterator

from .model import OPEN, STATUSES, TERMINAL, Action, Incident, StaleGeneration, UnknownAction, parse_stamp, stamp


def incidents_dir() -> Path:
    return Path.home() / ".claude" / "long-running" / "incidents"


@dataclass(frozen=True)
class _ReadView:
    identity: tuple[int, ...]
    incident: Incident
    kinds: dict[str, tuple[str, ...]]
    statuses: dict[str, tuple[str, ...]]
    selections: dict[tuple[str, str], tuple[str, ...]]

    @classmethod
    def load(cls, identity: tuple[int, ...], incident: Incident) -> _ReadView:
        kinds: dict[str, list[str]] = {}
        statuses: dict[str, list[str]] = {}
        selections: dict[tuple[str, str], list[str]] = {}
        for key, action in incident.actions.items():
            kinds.setdefault(action.kind, []).append(key)
            statuses.setdefault(action.status, []).append(key)
            selections.setdefault((action.kind, action.status), []).append(key)
        return cls(identity, incident, {key: tuple(ids) for key, ids in kinds.items()}, {key: tuple(ids) for key, ids in statuses.items()}, {key: tuple(ids) for key, ids in selections.items()})


class Store:
    """The executor-owned state store: one locked JSON file per incident."""

    def __init__(self, root: Path | None = None):
        self.root = root or incidents_dir()
        self._views: dict[str, _ReadView] = {}
        self._view_lock = Lock()

    def path(self, incident_id: str) -> Path:
        return self.root / f"{incident_id}.json"

    @contextmanager
    def _locked(self, incident_id: str) -> Iterator[Path]:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / f".{incident_id}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield self.path(incident_id)

    def _write(self, path: Path, incident: Incident, previous: str | None = None) -> None:
        content = json.dumps(incident.dump(), indent=2) + "\n"
        if content != previous:
            staged = path.with_name(f".{path.name}.{os.getpid()}")
            staged.write_text(content)
            staged.replace(path)
            with self._view_lock:
                self._views.pop(incident.incident_id, None)

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
        found = sorted(path.stem for path in self.root.glob("*.json")) if self.root.exists() else []
        current = set(found)
        with self._view_lock:
            self._views = {key: view for key, view in self._views.items() if key in current}
        return found

    def _view(self, incident_id: str) -> _ReadView:
        path = self.path(incident_id)
        with self._view_lock:
            if not path.exists():
                self._views.pop(incident_id, None)
                raise SystemExit(f"no incident {incident_id} in {self.root}")
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if (view := self._views.get(incident_id)) is not None and view.identity == identity:
                return view
            with path.open() as stream:
                stat = os.fstat(stream.fileno())
                incident = Incident.load(json.load(stream))
            identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            view = _ReadView.load(identity, incident)
            self._views[incident_id] = view
            return view

    def find_action(self, incident_id: str, action_id: str) -> Action | None:
        """Read one action without rebuilding an unchanged container's history."""
        action = self._view(incident_id).incident.actions.get(action_id)
        return deepcopy(action) if action is not None else None

    def actions(self, incident_id: str, kind: str | None = None, status: str | None = None) -> list[Action]:
        """Read actions in stored order using the container's current selection indexes."""
        view = self._view(incident_id)
        if kind is not None and status is not None:
            selected = view.selections.get((kind, status), ())
        elif kind is not None:
            selected = view.kinds.get(kind, ())
        elif status is not None:
            selected = view.statuses.get(status, ())
        else:
            selected = view.incident.actions
        return [deepcopy(view.incident.actions[key]) for key in selected]

    def ownership(self, incident_id: str) -> tuple[str, int]:
        """Read the owner and generation from the same current container version."""
        incident = self._view(incident_id).incident
        return incident.owner, incident.owner_generation

    @contextmanager
    def edit(self, incident_id: str) -> Iterator[Incident]:
        """Edit the current owner's incident while holding its exclusive lock."""
        with self._locked(incident_id) as path:
            if not path.exists():
                raise SystemExit(f"no incident {incident_id} in {self.root}")
            previous = path.read_text()
            incident = Incident.load(json.loads(previous))
            yield incident
            self._write(path, incident, previous)

    @contextmanager
    def owned(self, incident_id: str, generation: int) -> Iterator[Incident]:
        with self._locked(incident_id) as path:
            previous = path.read_text()
            incident = Incident.load(json.loads(previous))
            if incident.owner_generation != generation:
                raise StaleGeneration(f"{incident_id} is at owner generation {incident.owner_generation} ({incident.owner}), not {generation}")
            yield incident
            self._write(path, incident, previous)

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
