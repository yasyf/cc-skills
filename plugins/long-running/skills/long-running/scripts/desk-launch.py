#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Post every keyed orca desk line in one command: allocate the next `R<n>`, stamp the clock, post the line to `orca-desk` on cci, and for a launch wait for the runner's verdict.

    desk-launch.py incident <name> <fix-brief> [<evidence-brief>] [--drive D] [--lane L] [--timeout-seconds S]
    desk-launch.py lane <lane> <model> <effort> <brief> [--now] [--drive D] [--lane L] [--timeout-seconds S]
    desk-launch.py relay <lane>[,<lane>...] <text> [--drive D] [--lane L]
    desk-launch.py hold <slug> <owner> <what> [--drive D] [--lane L]
    desk-launch.py unhold <slug> [--drive D] [--lane L]
    desk-launch.py withdraw <R<n> | unfixed:<msg>:<n> | lane> [--drive D] [--lane L]
    desk-launch.py refused <lane> (--drive D | --session ID)

STDLIB ONLY. `incident` launches `<name>-fix`, and `<name>-evidence` when an evidence brief is
given, each `NOW incident xhigh` under its role, `role=fix` or `role=evidence`, the Incident
Turn's route; the runner's incident clock holds only the fix lane. `lane` launches one lane on any
model `orca-launch.sh` starts; `--now` skips the runner's load hold. `relay`, `hold`,
`unhold`, and `withdraw` post the runner's line of that verb and print it without waiting,
since the runner's verdict reaches the root on cci. The next key is one past the highest
`R<n>` opening an `orca-desk:` record addressed to `orca-desk` on the drive. A drive whose orca runner posted no `orca alive` record
in the last five minutes posts nothing: the command refuses in one line, naming the Agent to spawn
for a launch. Each line is one
`cci post --kind go --to orca-desk` record in the runner's grammar, so nothing is posted
unaddressed or in another form. A launch then reads the runner's records on the drive
until each key settles as `LAUNCHED`, `LAUNCH-FAILED`, `LAUNCH-EXPIRED`, `LAUNCH-HELD`, `UNVERIFIABLE`, or
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
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from long_running import drive as registry

DESK = "orca-desk"
RUNNER = "desk-runner"
PACIFIC = ZoneInfo("America/Los_Angeles")
KEY = re.compile(r"^(?:-\s+)?R(\d+)\b")
VERDICT = re.compile(r"^(?P<label>LAUNCHED|LAUNCH-FAILED|LAUNCH-EXPIRED|LAUNCH-HELD|UNVERIFIABLE|SPRITE-UNVERIFIABLE) (?:desk-lane-[\w.-]+/)?(?P<key>R\d+) (?P<lane>[\w.-]+): ")
LOAD_HIGH = "LOAD-HIGH "
LOAD_REFUSAL = re.compile(r"^LAUNCH-FAILED .*: R\d+ refused on load ")
REFUSAL_WINDOW = "30m"
FIX_ALIAS = "-fix"
POLL_SECONDS = 2
BUDGET = "30000"
KEY_LOCK = Path.home() / ".claude" / "scratch" / "desk-launch.lock"


def cci(drive: str, *argv: str) -> str:
    done = subprocess.run(["cci", *argv, *(["--drive", drive] if drive else [])], capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"cci {argv[0]} exited {done.returncode}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def records(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def require_runner(drive: str, fallback: str) -> None:
    session = os.environ.get(registry.SESSION_ENV)
    entry = next((entry for entry in registry.drives() if registry.cci_drive(entry) == drive or (not drive and session in entry["sessions"])), None)
    if not entry:
        sys.exit(f"no drive in the registry is {f'cci drive {drive}' if drive else f'bound to session {session}'}, so its orca desk runner is unknown and nothing is posted")
    beat = registry.heartbeat(lambda argv: cci("", *argv[1:]), registry.cci_drive(entry), "orca")
    if not registry.beating(beat, datetime.now(timezone.utc)):
        last = f"last at {datetime.fromisoformat(beat['at']).astimezone(PACIFIC).strftime('%-I:%M %p')}" if beat else "never"
        sys.exit(f"refused: no orca desk runner has posted an orca alive record on cci drive {registry.cci_drive(entry)} in the last five minutes ({last}), so nothing reads {DESK} lines; {fallback}")


def agent_fallback(args: argparse.Namespace) -> str:
    if args.verb == "incident":
        return f"spawn an Agent named `{args.name}-fix`{f' and one named `{args.name}-evidence`' if args.evidence_brief else ''} instead"
    if args.verb == "lane":
        return f"spawn an Agent named `{args.target}` instead"
    return "nothing was posted"


@contextmanager
def keyed():
    """Hold the machine-wide key lock while a caller reads the next `R<n>` and posts it, so two launches started together never post the same key."""
    KEY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with KEY_LOCK.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def next_key(drive: str, fallback: str) -> int:
    require_runner(drive, fallback)
    posted = [record["text"] for record in records(cci(drive, "grep", f"R[0-9]+ .*{DESK}: ", "--to", DESK, "--json", "--limit", "20", "--budget", BUDGET))]
    return 1 + max((int(match[1]) for text in posted if (match := KEY.match(text.strip()))), default=0)


def directive(args: argparse.Namespace) -> tuple[str, str]:
    if args.verb == "relay":
        lanes = args.lanes.split(",")
        return lanes[0], f"relay to {', '.join(lanes)}: {args.text}"
    if args.verb == "hold":
        return args.slug, f"hold {args.slug} owner={args.owner} :: {args.what}"
    if args.verb == "unhold":
        return args.slug, f"unhold {args.slug}"
    return args.target, f"withdraw {args.target}"


def launch_directive(lane: str, model: str, effort: str, now: bool, role: str, brief: Path) -> str:
    return f"launch {lane}{' NOW' if now else ''} {model} {effort}{f' role={role}' if role else ''} brief={brief}"


def launches(args: argparse.Namespace) -> list[tuple[str, str, str, bool, str, Path]]:
    if args.verb == "incident":
        briefs = [("fix", args.fix_brief), *([("evidence", args.evidence_brief)] if args.evidence_brief else [])]
        return [(f"{args.name}-{role}", "incident", "xhigh", True, role, brief) for role, brief in briefs]
    return [(args.target, args.model, args.effort, args.now, "", args.brief)]


def refusal(drive: str, lane: str) -> str:
    posted = records(cci(drive, "grep", "LOAD-HIGH|LAUNCHED|LAUNCH-FAILED|LAUNCH-EXPIRED|LAUNCH-HELD|UNVERIFIABLE", "--lane", RUNNER, "--since", REFUSAL_WINDOW, "--json", "--budget", BUDGET))
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


def post(drive: str, author: str, topic: str, key: int, text: str) -> int:
    line = f"R{key} ({datetime.now(PACIFIC).strftime('%-I:%M %p')}) {DESK}: {text}"
    stored = json.loads(cci(drive, "post", "--lane", author, "--kind", "go", "--to", DESK, "--topic", topic, "--text", line, "--json"))
    print(f"posted R{key} as cci #{stored['seq']}: {line}", flush=True)
    return stored["seq"]


def wait(drive: str, since: int, lanes: dict[str, str], timeout: float) -> dict[str, tuple[str, str]]:
    settled: dict[str, tuple[str, str]] = {}
    deadline = time.monotonic() + timeout
    while len(settled) < len(lanes) and time.monotonic() < deadline:
        for record in records(cci(drive, "tail", "--since-seq", str(since), "--lane", RUNNER, "--json", "--budget", BUDGET)):
            since = max(since, record["seq"])
            verdict = VERDICT.match(record["text"])
            if verdict and lanes.get(verdict["key"]) == verdict["lane"] and verdict["key"] not in settled:
                settled[verdict["key"]] = (verdict["label"], record["text"])
                print(f"{lanes[verdict['key']]} {verdict['key']} {record['text']}", flush=True)
        if len(settled) < len(lanes):
            time.sleep(POLL_SECONDS)
    return settled


def main(argv: list[str] | None = None) -> int:
    poster = argparse.ArgumentParser(add_help=False)
    poster.add_argument("--drive", default="", help="the cci drive the orca runner reads; default the drive bound to this session")
    poster.add_argument("--lane", default="root", help="the lane posting the lines")
    common = argparse.ArgumentParser(add_help=False, parents=[poster])
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
    relay = verbs.add_parser("relay", parents=[poster], help="relay text to each named lane's live dispatch")
    relay.add_argument("lanes", metavar="lane[,lane...]")
    relay.add_argument("text")
    hold = verbs.add_parser("hold", parents=[poster], help="start the runner's clock on an urgent hold")
    hold.add_argument("slug")
    hold.add_argument("owner")
    hold.add_argument("what")
    unhold = verbs.add_parser("unhold", parents=[poster], help="lift a hold")
    unhold.add_argument("slug")
    withdraw = verbs.add_parser("withdraw", parents=[poster], help="withdraw a launch by key, or a lane")
    withdraw.add_argument("target", metavar="R<n>|unfixed:<msg>:<n>|lane")
    load = verbs.add_parser("refused", help="print the runner's load refusal that sends <lane> to an in-process launch")
    load.add_argument("target", metavar="lane")
    which = load.add_mutually_exclusive_group(required=True)
    which.add_argument("--drive", default="")
    which.add_argument("--session", default="")
    args = parser.parse_args(argv)
    if args.verb == "refused":
        return refused(args)
    if args.verb in ("relay", "hold", "unhold", "withdraw"):
        topic, text = directive(args)
        with keyed():
            post(args.drive, args.lane, topic, next_key(args.drive, agent_fallback(args)), text)
        return 0
    planned = launches(args)
    for *_, brief in planned:
        if not brief.is_file():
            parser.error(f"brief {brief} is not a file")
    topic = args.name if args.verb == "incident" else args.target
    with keyed():
        first = next_key(args.drive, agent_fallback(args))
        keys = {f"R{first + offset}": lane for offset, (lane, *_) in enumerate(planned)}
        since = min(post(args.drive, args.lane, topic, first + offset, launch_directive(lane, model, effort, now, role, brief.resolve())) for offset, (lane, model, effort, now, role, brief) in enumerate(planned)) - 1
    settled = wait(args.drive, since, keys, args.timeout_seconds)
    for key, lane in keys.items():
        if key not in settled:
            print(f"{lane} {key} unsettled after {args.timeout_seconds:.0f}s: no runner verdict; check the orca runner's newest alive record with `cci tail --drive {args.drive or '<drive>'} --lane {RUNNER} --kind state --topic orca -n 1`", flush=True)
    if len(settled) < len(keys):
        return 2
    return 0 if all(label == "LAUNCHED" for label, _ in settled.values()) else 1


if __name__ == "__main__":
    from long_running import drive

    drive.run_installed(__file__)
    sys.exit(main())
