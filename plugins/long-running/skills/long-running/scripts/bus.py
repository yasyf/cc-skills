#!/usr/bin/env python3
"""The lane bus for incident.py's comms, over cci: one cci drive per bus.

    bus.py post --bus DRIVE --from LANE --kind decision|head|contract|blocker|ask|answer|withdraw --topic T --text ... [--to LANE]... [--re SEQ]
    bus.py read --bus DRIVE --lane LANE [--kind K]... [--topic T]... --json

STDLIB ONLY. `post` is one `cci post` and prints its `#<seq>`. `read` prints, as one JSON
list, every record cci delivers to the lane, oldest first, as `{seq, at, kind, topic,
from, to, text, re}`; it pages through `cci tail --since` and touches no cursor. Lanes
use cci directly; this script keeps incident.py's calls working. Every subprocess goes
through :class:`Shell`, the one seam tests replace.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import cci

KINDS = ("decision", "head", "contract", "blocker", "ask", "answer", "withdraw")


class Shell:
    """The single subprocess boundary: every `cci` call passes through here."""

    def run(self, argv: list[str]) -> str:
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def entry(record: dict) -> dict:
    return {
        "seq": record["seq"],
        "at": record["at"],
        "kind": record["kind"],
        "topic": record.get("topic", ""),
        "from": record["lane"],
        "to": record.get("to", []),
        "text": record["text"],
        "re": record.get("re"),
    }


def cmd_post(args: argparse.Namespace, shell: Shell) -> int:
    argv = ["cci", "post", "--drive", args.bus, "--lane", args.sender, "--kind", args.kind, "--text", args.text]
    argv += ["--topic", args.topic] if args.topic else []
    argv += ["--re", str(args.re)] if args.re is not None else []
    argv += [flag for lane in args.to for flag in ("--to", lane)]
    print(shell.run(argv).strip())
    return 0


def cmd_read(args: argparse.Namespace, shell: Shell) -> int:
    filters = [*(f"--kind={kind}" for kind in args.kind), *(f"--topic={topic}" for topic in args.topic)]
    print(json.dumps([entry(record) for record in cci.records(shell.run, args.bus, "--reader", args.lane, *filters)]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bus.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    post = sub.add_parser("post")
    post.add_argument("--bus", required=True)
    post.add_argument("--from", dest="sender", required=True)
    post.add_argument("--kind", required=True, choices=KINDS)
    post.add_argument("--topic")
    post.add_argument("--text", required=True)
    post.add_argument("--to", action="append", default=[])
    post.add_argument("--re", type=int)
    post.add_argument("--repo")
    read = sub.add_parser("read")
    read.add_argument("--bus", required=True)
    read.add_argument("--lane", required=True)
    read.add_argument("--kind", action="append", default=[], choices=KINDS)
    read.add_argument("--topic", action="append", default=[])
    read.add_argument("--all", action="store_true")
    read.add_argument("--peek", action="store_true")
    read.add_argument("--json", action="store_true", required=True)
    read.add_argument("--repo")
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {"post": cmd_post, "read": cmd_read}[args.cmd](args, shell or Shell())


if __name__ == "__main__":
    sys.exit(main())
