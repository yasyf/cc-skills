#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Grade a release's end state and its series watch before the release walker cuts again.

    endstate.py grade --prefix r1a|r1b|promoted --reading PATH [--cluster ENV]...
    endstate.py watch COMPONENT [--repo PATH]

STDLIB ONLY. `grade` reads the release owner's hand reading of one real Mac and one pod per cluster, a JSON object
`{"mac": HOST, "build": "drive"|"r1b", "rows": {ROW: VALUE}, "pods": {CLUSTER: {ROW: VALUE}}}`, and exits 1 naming
each row that differs from the rollout prefix's end state, or each row and `--cluster` the reading leaves out. After
r1a the Mac runs the drive build and only pod rows are graded; after r1b the Mac is a pinned internal Mac on R1b and
every row is graded; after promotion a Mac on R1b is graded and a Mac below it is counted, not graded. `watch` runs
`tools/ci release series COMPONENT --json` in the monorepo at `--repo` (default the current directory) and exits 1
while under 30 minutes have passed since the component's last cut or a declared series refuses.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

WATCH = timedelta(minutes=30)
PREFIXES = ("r1a", "r1b", "promoted")
BUILDS = ("drive", "r1b")
TABLE = (
    ("primaries", "mac", "1", 0),
    ("replicas", "mac", "", 0),
    ("serve-cpu-percent", "mac", "", 1),
    ("serve-rss-mb", "mac", "", 200),
    ("primary-rss-mb", "mac", "", 64),
    ("litestream-rss-mb", "mac", "", 32),
    ("read-path-local", "mac", "primary", 0),
    ("read-path-shared", "mac", "reader", 0),
    ("read-path-cloud", "mac", "reader", 0),
    ("failed-opens-offline", "mac", "0", 0),
    ("failed-opens-online", "mac", "0", 0),
    ("delete-calls", "mac", "0", 0),
    ("work-discovery-calls", "pod", "0", 0),
    ("lock-waits", "pod", "0", 0),
    ("writers-per-agent", "pod", "1", 0),
    ("delete-calls", "pod", "0", 0),
    ("permission-rows", "pod", "", 0),
    ("queue-contents", "pod", "", 0),
)




def design(equals: str, at_most: float) -> str:
    if at_most:
        return f"at most {at_most:g}"
    return equals or "read and recorded"


def holds(equals: str, at_most: float, read: str) -> bool:
    if not at_most:
        return not equals or read == equals
    try:
        return float(read) <= at_most
    except ValueError:
        return False


def gaps(prefix: str, reading: dict, clusters: list[str]) -> list[str]:
    found = []
    if prefix not in PREFIXES:
        found.append(f"prefix {prefix!r} is none of {', '.join(PREFIXES)}")
    if reading.get("build") not in BUILDS:
        found.append(f"build {reading.get('build')!r} is neither drive nor r1b")
    if not reading.get("mac"):
        found.append("the reading names no Mac")
    found += unread("mac", "the Mac", reading.get("rows", {}))
    pods = reading.get("pods", {})
    if not pods:
        found.append("the reading has no pod")
    found += [f"no pod read on {cluster}" for cluster in clusters if cluster not in pods]
    for cluster, rows in sorted(pods.items()):
        found += unread("pod", f"the pod on {cluster}", rows)
    return found


def unread(side: str, where: str, rows: dict) -> list[str]:
    names = [name for name, of, _, _ in TABLE if of == side]
    missing = [f"{where} reads no {name}" for name in names if not rows.get(name)]
    return missing + [f"{where} has no row {name}" for name in sorted(rows) if name not in names]


def differences(prefix: str, reading: dict) -> list[str]:
    mac, build = reading["mac"], reading["build"]
    found = []
    if prefix == "r1a" and build != "drive":
        found.append(f"after r1a the Mac runs the drive build, and {mac} runs R1b")
    elif prefix == "r1b" and build != "r1b":
        found.append(f"after r1b the reading is of a pinned internal Mac on R1b, and {mac} runs the drive build")
    elif prefix != "r1a" and build == "r1b":
        found += differing("mac", mac, reading["rows"])
    for cluster, rows in sorted(reading["pods"].items()):
        found += differing("pod", f"the pod on {cluster}", rows)
    return found


def differing(side: str, where: str, rows: dict) -> list[str]:
    return [
        f"{where} reads {name} {rows[name]}, and the design is {design(equals, at_most)}"
        for name, of, equals, at_most in TABLE
        if of == side and not holds(equals, at_most, rows[name])
    ]


def instant(stamp: str) -> datetime:
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", stamp))


def watch_refusals(check: dict) -> list[str]:
    since, until = instant(check["since"]), instant(check["until"])
    found = []
    if until - since < WATCH:
        read = int((until - since).total_seconds() // 60)
        found.append(f"the watch read {read} of 30 minutes since the last cut; watch again from {(since + WATCH).isoformat()}")
    return found + [f"{reading['series']['name']}: {reading['failed']}" for reading in check["readings"] if reading.get("failed")]


def grade(args: argparse.Namespace) -> int:
    reading = json.loads(Path(args.reading).read_text())
    found = gaps(args.prefix, reading, args.cluster) or differences(args.prefix, reading)
    for line in found:
        print(line)
    if found:
        return 1
    print(f"{reading['mac']} and {len(reading['pods'])} pods match the {args.prefix} end state")
    return 0


def watch(args: argparse.Namespace) -> int:
    ran = subprocess.run(["tools/ci", "release", "series", args.component, "--json"], cwd=args.repo, capture_output=True, text=True)
    try:
        check = json.loads(ran.stdout)
    except json.JSONDecodeError:
        print(f"tools/ci release series {args.component} printed no check: {ran.stderr.strip()}", file=sys.stderr)
        return 1
    found = watch_refusals(check)
    for line in found:
        print(line)
    if found or ran.returncode:
        return 1
    print(f"{args.component}: the series watch is clean for {len(check['readings'])} series")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="endstate.py")
    verbs = parser.add_subparsers(dest="verb", required=True)
    graded = verbs.add_parser("grade")
    graded.add_argument("--prefix", required=True)
    graded.add_argument("--reading", required=True)
    graded.add_argument("--cluster", action="append", default=[])
    graded.set_defaults(run=grade)
    watched = verbs.add_parser("watch")
    watched.add_argument("component")
    watched.add_argument("--repo", default=".")
    watched.set_defaults(run=watch)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
