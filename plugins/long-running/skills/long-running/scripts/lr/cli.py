#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""`lr`, the drive's command line.

    lr post <kind> [--drive D] [--as LANE] --<field> <value>... [--to <lane>]...
    lr status --store <path> [--repo <owner/name>]

`post` writes one typed record through the bus and prints the corrected command when the
bus refuses it. `status` is the root's one read: PRs per state, overdue items, open owner
asks, and the heads waiting under each base.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from lr.bus import KINDS, Bus, Refused
from lr.machine import Mover, State
from lr.store import Store


def fields(pairs: list[str]) -> tuple[dict, list[str]]:
    body, to = {}, []
    key = None
    for word in pairs:
        if word.startswith("--"):
            key = word[2:]
            continue
        if key is None:
            raise SystemExit(f"lr post: expected --<field> before {word!r}")
        if key == "to":
            to.append(word)
        else:
            body[key] = int(word) if word.isdigit() else word
        key = None
    if key is not None:
        raise SystemExit(f"lr post: --{key} needs a value")
    return body, to


def cmd_post(args: argparse.Namespace, run: Callable[[list[str]], str] | None) -> int:
    body, to = fields(args.fields)
    bus = Bus(args.drive, args.lane, run) if run else Bus(args.drive, args.lane)
    try:
        record = bus.post(args.kind, to=to, **body)
    except Refused as refused:
        print(f"lr post: refused: {refused}", file=sys.stderr)
        return 2
    print(f"#{record.seq} {record.kind}")
    return 0


def status_lines(store: Store, repo: str, now: datetime) -> list[str]:
    prs = store.prs(repo)
    counts = Counter(row["state"] for row in prs)
    lines = ["states: " + ", ".join(f"{state} {counts[state]}" for state in State if counts[state])]
    overdue = store.overdue(now)
    lines.append(f"overdue: {len(overdue)}")
    lines += [f"  #{item.number} {item.kind} {item.mover} since {item.deadline.strftime('%H:%MZ')}" for item in overdue]
    owner = [item for item in store.open_items(repo) if item.mover == Mover.OWNER]
    lines.append(f"owner asks: {len(owner)}")
    lines += [f"  #{item.number} {item.kind} {item.head[:12]}" for item in owner]
    waiting = defaultdict(list)
    heads = {row["head"]: row["number"] for row in prs}
    for row in prs:
        if row["state"] not in (State.LANDED, State.CLOSED) and row["base"] in heads:
            waiting[heads[row["base"]]].append(row["number"])
    for base, numbers in sorted(waiting.items()):
        lines.append(f"base #{base} holds {len(numbers)}: " + ", ".join(f"#{n}" for n in numbers))
    return lines


def cmd_status(args: argparse.Namespace, _run) -> int:
    print("\n".join(status_lines(Store.open(Path(args.store)), args.repo, datetime.now(UTC))))
    return 0


def main(argv: list[str], run: Callable[[list[str]], str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lr")
    verbs = parser.add_subparsers(dest="verb", required=True)
    post = verbs.add_parser("post", help="write one typed record")
    post.add_argument("kind", choices=list(KINDS))
    post.add_argument("--drive", default=os.environ.get("LR_DRIVE", ""))
    post.add_argument("--as", dest="lane", default=os.environ.get("LR_LANE", ""))
    post.set_defaults(cmd=cmd_post)
    status = verbs.add_parser("status", help="the drive at a glance")
    status.add_argument("--store", required=True)
    status.add_argument("--repo", required=True)
    status.set_defaults(cmd=cmd_status)
    args, extras = parser.parse_known_args(argv)
    args.fields = extras
    return args.cmd(args, run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
