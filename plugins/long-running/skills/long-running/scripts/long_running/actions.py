"""The action contract: one durable record per side effect an executor owns, under one owner generation.

    actions.py show     --incident ID [--json]
    actions.py list     [--json]
    actions.py transfer --incident ID --expect-generation N --to OWNER
    actions.py ack      --incident ID --owner OWNER

STDLIB ONLY. One SQLite database per incident holds indexed action and fact records.
Writes serialize under the incident lock and commit before external effects. A
generation check rejects a former owner after a transfer.

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
import json
import sys
from pathlib import Path

from .model import OPEN, STATUSES, TERMINAL, Action, Incident, StaleGeneration, UnknownAction, parse_stamp, stamp
from .sqlite_store import Store, Transaction, detached


def incidents_dir() -> Path:
    return Path.home() / ".claude" / "long-running" / "incidents"



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
