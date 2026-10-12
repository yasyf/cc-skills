#!/usr/bin/env python3
"""Launch Orca lanes through the orca desk in one command: allocate the next `R<n>`, post each launch line to `orca-desk` on cci, and wait for the runner's verdict.

    desk-launch.py incident <name> <fix-brief> [<evidence-brief>] [--drive D] [--lane L] [--timeout-seconds S]
    desk-launch.py lane <lane> <model> <effort> <brief> [--now] [--drive D] [--lane L] [--timeout-seconds S]

STDLIB ONLY. `incident` launches `<name>-fix`, and `<name>-evidence` when an evidence brief is
given, each `NOW incident xhigh`, the Incident Turn's route. `lane` launches one lane on any
model `orca-launch.sh` starts; `--now` skips the runner's load hold. The next key is one past
the highest `R<n>` that opens a root `orca-desk:` record on the drive. Each launch is one
`cci post --kind go --to orca-desk` record in the runner's grammar, so nothing is posted
unaddressed or in another form. The command then reads the runner's records on the drive
until each key settles as `LAUNCHED`, `LAUNCH-FAILED`, `LAUNCH-HELD`, `UNVERIFIABLE`, or
`SPRITE-UNVERIFIABLE`, and prints one line per lane: its key, the runner's line, and so the
dispatch and terminal of a launched lane. It exits 0 when every lane launched, 1 when any
settled otherwise, and 2 when one has not settled within `--timeout-seconds`; a withdrawal
of an unsettled key is `cci post --kind go --to orca-desk --text "orca-desk: withdraw R<n>"`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DESK = "orca-desk"
RUNNER = "desk-runner"
PACIFIC = ZoneInfo("America/Los_Angeles")
KEY = re.compile(r"^(?:-\s+)?R(\d+)\b")
VERDICT = re.compile(r"^(?P<label>LAUNCHED|LAUNCH-FAILED|LAUNCH-HELD|UNVERIFIABLE|SPRITE-UNVERIFIABLE) (?:desk-lane-[\w.-]+/)?(?P<key>R\d+)\b")
POLL_SECONDS = 2
BUDGET = "30000"


def cci(drive: str, *argv: str) -> str:
    done = subprocess.run(["cci", *argv, *(["--drive", drive] if drive else [])], capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"cci {argv[0]} exited {done.returncode}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def records(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def next_key(drive: str) -> int:
    posted = records(cci(drive, "grep", f"{DESK}: ", "--lane", "root", "--json", "--limit", "50", "--budget", BUDGET))
    return 1 + max((int(match[1]) for record in posted if (match := KEY.match(record["text"]))), default=0)


def launches(args: argparse.Namespace) -> list[tuple[str, str, str, bool, Path]]:
    if args.verb == "incident":
        briefs = [("fix", args.fix_brief), *([("evidence", args.evidence_brief)] if args.evidence_brief else [])]
        return [(f"{args.name}-{role}", "incident", "xhigh", True, brief) for role, brief in briefs]
    return [(args.target, args.model, args.effort, args.now, args.brief)]


def post(drive: str, author: str, topic: str, key: int, lane: str, model: str, effort: str, now: bool, brief: Path) -> int:
    clock = datetime.now(PACIFIC).strftime("%-I:%M %p")
    line = f"R{key} ({clock}) {DESK}: launch {lane}{' NOW' if now else ''} {model} {effort} brief={brief}"
    stored = json.loads(cci(drive, "post", "--lane", author, "--kind", "go", "--to", DESK, "--topic", topic, "--text", line, "--json"))
    print(f"posted R{key} {lane} as cci #{stored['seq']}", flush=True)
    return stored["seq"]


def wait(drive: str, since: int, lanes: dict[str, str], timeout: float) -> dict[str, tuple[str, str]]:
    settled: dict[str, tuple[str, str]] = {}
    deadline = time.monotonic() + timeout
    while len(settled) < len(lanes) and time.monotonic() < deadline:
        for record in records(cci(drive, "tail", "--since", str(since), "--lane", RUNNER, "--json", "--budget", BUDGET)):
            since = max(since, record["seq"])
            verdict = VERDICT.match(record["text"])
            if verdict and verdict["key"] in lanes and verdict["key"] not in settled:
                settled[verdict["key"]] = (verdict["label"], record["text"])
                print(f"{lanes[verdict['key']]} {verdict['key']} {record['text']}", flush=True)
        if len(settled) < len(lanes):
            time.sleep(POLL_SECONDS)
    return settled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="desk-launch.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--drive", default="", help="the cci drive the orca runner reads; default the drive bound to this session")
    parser.add_argument("--lane", default="root", help="the lane posting the launch lines")
    parser.add_argument("--timeout-seconds", type=float, default=600)
    verbs = parser.add_subparsers(dest="verb", required=True)
    incident = verbs.add_parser("incident", help="launch <name>-fix and <name>-evidence NOW on incident xhigh")
    incident.add_argument("name")
    incident.add_argument("fix_brief", type=Path)
    incident.add_argument("evidence_brief", type=Path, nargs="?")
    lane = verbs.add_parser("lane", help="launch one lane")
    lane.add_argument("target", metavar="lane")
    lane.add_argument("model")
    lane.add_argument("effort")
    lane.add_argument("brief", type=Path)
    lane.add_argument("--now", action="store_true", help="owner-directed: skip the runner's load hold")
    args = parser.parse_args(argv)
    planned = launches(args)
    for _, _, _, _, brief in planned:
        if not brief.is_file():
            parser.error(f"brief {brief} is not a file")
    topic = args.name if args.verb == "incident" else args.target
    first = next_key(args.drive)
    keys = {f"R{first + offset}": lane for offset, (lane, *_) in enumerate(planned)}
    since = min(post(args.drive, args.lane, topic, first + offset, lane, model, effort, now, brief.resolve()) for offset, (lane, model, effort, now, brief) in enumerate(planned)) - 1
    settled = wait(args.drive, since, keys, args.timeout_seconds)
    for key, lane in keys.items():
        if key not in settled:
            print(f"{lane} {key} unsettled after {args.timeout_seconds:.0f}s: no runner verdict; check the orca runner's heartbeat beside the desk inbox", flush=True)
    if len(settled) < len(keys):
        return 2
    return 0 if all(label == "LAUNCHED" for label, _ in settled.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
