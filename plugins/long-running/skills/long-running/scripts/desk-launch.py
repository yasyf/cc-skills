#!/usr/bin/env python3
"""Launch Orca lanes through the orca desk in one command: allocate the next `R<n>`, post each launch line to `orca-desk` on cci, and wait for the runner's verdict.

    desk-launch.py incident <name> <fix-brief> [<evidence-brief>] [--drive D] [--lane L] [--timeout-seconds S]
    desk-launch.py lane <lane> <model> <effort> <brief> [--now] [--drive D] [--lane L] [--timeout-seconds S]
    desk-launch.py refused <lane> (--drive D | --session ID)

STDLIB ONLY. `incident` launches `<name>-fix`, and `<name>-evidence` when an evidence brief is
given, each `NOW incident xhigh` under its role, `role=fix` or `role=evidence`, the Incident
Turn's route; the runner's incident clock holds only the fix lane. `lane` launches one lane on any
model `orca-launch.sh` starts; `--now` skips the runner's load hold. The next key is one past
the highest `R<n>` that opens a root `orca-desk:` record on the drive. Each launch is one
`cci post --kind go --to orca-desk` record in the runner's grammar, so nothing is posted
unaddressed or in another form. The command then reads the runner's records on the drive
until each key settles as `LAUNCHED`, `LAUNCH-FAILED`, `LAUNCH-HELD`, `UNVERIFIABLE`, or
`SPRITE-UNVERIFIABLE`, and prints one line per lane: its key, the runner's line, and so the
dispatch and terminal of a launched lane. It exits 0 when every lane launched, 1 when any
settled otherwise, and 2 when one has not settled within `--timeout-seconds`; a withdrawal
of an unsettled key is `cci post --kind go --to orca-desk --text "orca-desk: withdraw R<n>"`.
`refused` prints the runner record from the last 30 minutes that sends `<lane>`, or its
`-fix` alias, to an in-process launch, and exits 1 when there is none: the lane's newest
verdict when it is a `LAUNCH-FAILED` refused on load, or the newest `LOAD-HIGH` when the
lane has no verdict at all. `--session` reads the drive from the drive registry.
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

import drive as registry

DESK = "orca-desk"
RUNNER = "desk-runner"
PACIFIC = ZoneInfo("America/Los_Angeles")
KEY = re.compile(r"^(?:-\s+)?R(\d+)\b")
VERDICT = re.compile(r"^(?P<label>LAUNCHED|LAUNCH-FAILED|LAUNCH-HELD|UNVERIFIABLE|SPRITE-UNVERIFIABLE) (?:desk-lane-[\w.-]+/)?(?P<key>R\d+) (?P<lane>[\w.-]+): ")
LOAD_HIGH = "LOAD-HIGH "
LOAD_REFUSAL = re.compile(r"^LAUNCH-FAILED .*: R\d+ refused on load ")
REFUSAL_WINDOW = "30m"
FIX_ALIAS = "-fix"
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
    posted = records(cci(drive, "grep", f"R[0-9]+ .*{DESK}: ", "--lane", "root", "--json", "--limit", "20", "--budget", BUDGET))
    return 1 + max((int(match[1]) for record in posted if (match := KEY.match(record["text"]))), default=0)


def launches(args: argparse.Namespace) -> list[tuple[str, str, str, bool, str, Path]]:
    if args.verb == "incident":
        briefs = [("fix", args.fix_brief), *([("evidence", args.evidence_brief)] if args.evidence_brief else [])]
        return [(f"{args.name}-{role}", "incident", "xhigh", True, role, brief) for role, brief in briefs]
    return [(args.target, args.model, args.effort, args.now, "", args.brief)]


def refusal(drive: str, lane: str) -> str:
    posted = records(cci(drive, "grep", "LOAD-HIGH|LAUNCHED|LAUNCH-FAILED|LAUNCH-HELD|UNVERIFIABLE", "--lane", RUNNER, "--since", REFUSAL_WINDOW, "--json", "--budget", BUDGET))
    newest = sorted(posted, key=lambda record: record["seq"], reverse=True)
    names = {lane, f"{lane}{FIX_ALIAS}"}
    verdicts = [record["text"] for record in newest if (verdict := VERDICT.match(record["text"])) and verdict["lane"] in names]
    if verdicts:
        return verdicts[0] if LOAD_REFUSAL.match(verdicts[0]) else ""
    return next((record["text"] for record in newest if record["text"].startswith(LOAD_HIGH)), "")


def refused(args: argparse.Namespace) -> int:
    drive = args.drive
    if args.session:
        if not (entry := registry.find(None, args.session)):
            sys.exit(f"no drive in the registry claims session {args.session}")
        drive = registry.cci_drive(entry)
    if text := refusal(drive, args.target):
        print(text)
        return 0
    return 1


def post(drive: str, author: str, topic: str, key: int, lane: str, model: str, effort: str, now: bool, role: str, brief: Path) -> int:
    clock = datetime.now(PACIFIC).strftime("%-I:%M %p")
    line = f"R{key} ({clock}) {DESK}: launch {lane}{' NOW' if now else ''} {model} {effort}{f' role={role}' if role else ''} brief={brief}"
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
            if verdict and lanes.get(verdict["key"]) == verdict["lane"] and verdict["key"] not in settled:
                settled[verdict["key"]] = (verdict["label"], record["text"])
                print(f"{lanes[verdict['key']]} {verdict['key']} {record['text']}", flush=True)
        if len(settled) < len(lanes):
            time.sleep(POLL_SECONDS)
    return settled


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--drive", default="", help="the cci drive the orca runner reads; default the drive bound to this session")
    common.add_argument("--lane", default="root", help="the lane posting the launch lines")
    common.add_argument("--timeout-seconds", type=float, default=600)
    parser = argparse.ArgumentParser(prog="desk-launch.py", description=__doc__.split("\n", 1)[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    incident = verbs.add_parser("incident", parents=[common], help="launch <name>-fix and <name>-evidence NOW on incident xhigh")
    incident.add_argument("name")
    incident.add_argument("fix_brief", type=Path)
    incident.add_argument("evidence_brief", type=Path, nargs="?")
    lane = verbs.add_parser("lane", parents=[common], help="launch one lane")
    lane.add_argument("target", metavar="lane")
    lane.add_argument("model")
    lane.add_argument("effort")
    lane.add_argument("brief", type=Path)
    lane.add_argument("--now", action="store_true", help="owner-directed: skip the runner's load hold")
    load = verbs.add_parser("refused", help="print the runner's load refusal that sends <lane> to an in-process launch")
    load.add_argument("target", metavar="lane")
    which = load.add_mutually_exclusive_group(required=True)
    which.add_argument("--drive", default="")
    which.add_argument("--session", default="")
    args = parser.parse_args(argv)
    if args.verb == "refused":
        return refused(args)
    planned = launches(args)
    for *_, brief in planned:
        if not brief.is_file():
            parser.error(f"brief {brief} is not a file")
    topic = args.name if args.verb == "incident" else args.target
    first = next_key(args.drive)
    keys = {f"R{first + offset}": lane for offset, (lane, *_) in enumerate(planned)}
    since = min(post(args.drive, args.lane, topic, first + offset, lane, model, effort, now, role, brief.resolve()) for offset, (lane, model, effort, now, role, brief) in enumerate(planned)) - 1
    settled = wait(args.drive, since, keys, args.timeout_seconds)
    for key, lane in keys.items():
        if key not in settled:
            print(f"{lane} {key} unsettled after {args.timeout_seconds:.0f}s: no runner verdict; check the orca runner's heartbeat beside the desk inbox", flush=True)
    if len(settled) < len(keys):
        return 2
    return 0 if all(label == "LAUNCHED" for label, _ in settled.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
