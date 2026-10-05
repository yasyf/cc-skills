#!/usr/bin/env python3
import argparse
import datetime
import fnmatch
import json
import os
import subprocess
import sys
import time

USAGE = """usage: monitor-watch.py once|watch --state <file> [--tag <glob>]... [--id <id>]... [--alert-inbox <file>]

Reads every Datadog monitor through `pup monitors list` and keeps those carrying a
tag that matches a --tag glob, such as release-target:*, or named by --id. It
prints one line per transition and nothing on an unchanged state:

  <now> <id> <from> -> <to> at <overall_state_modified> | <name>

A transition is a move into Alert, Warn, or No Data, or back to OK from one of
them. A monitor the state file has not seen yet prints with <from> start when it
is not OK, so the first read reports everything already alerting. A failed read
prints API-FAIL once per streak.

With --alert-inbox, each move from a known state into Alert or Warn also appends one
`orca-desk: alert dd-<id> <link> :: <what>` line to that file. The orca desk runner
records the transition and launches nothing; the alerts desk launches an incident
lane only when it judges one necessary. A monitor seen for the first time never
appends one.

once reads one time. watch reads every --interval seconds, default 60, until
--timeout seconds pass, default 1740, so a Monitor re-arms it before its own
30-minute expiry. --state persists across runs, so a re-armed watch reports
only what changed while it was down.
"""

LOUD = {"Alert", "Warn", "No Data"}
PAGING = {"Alert", "Warn"}


def utc() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def watched(monitor: dict, tags: list[str], ids: set[str]) -> bool:
    if str(monitor["id"]) in ids:
        return True
    return any(fnmatch.fnmatchcase(tag, glob) for tag in monitor.get("tags") or [] for glob in tags)


def transition(before: str | None, after: str) -> bool:
    if before is None:
        return after != "OK"
    return before != after and (after in LOUD or (after == "OK" and before in LOUD))


def poll(args: argparse.Namespace, failing: bool) -> bool:
    result = subprocess.run(
        ["pup", "--no-agent", "--read-only", "monitors", "list", "--limit", "1000", "--output", "json"],
        capture_output=True,
        text=True,
    )
    try:
        monitors = json.loads(result.stdout)
    except json.JSONDecodeError:
        if not failing:
            print(f"{utc()} API-FAIL {(result.stderr or result.stdout).strip()[:200]}", flush=True)
        return True
    known = json.load(open(args.state)) if os.path.exists(args.state) else {}
    states = {}
    for monitor in monitors:
        if not watched(monitor, args.tag, set(args.id)):
            continue
        key, after = str(monitor["id"]), monitor["overall_state"]
        states[key] = after
        before = known.get(key)
        if transition(before, after):
            print(
                f"{utc()} {key} {before or 'start'} -> {after} at {monitor.get('overall_state_modified')} | {monitor['name']}",
                flush=True,
            )
            if args.alert_inbox and before is not None and after in PAGING:
                with open(args.alert_inbox, "a") as inbox:
                    inbox.write(
                        f"orca-desk: alert dd-{key} https://app.datadoghq.com/monitors/{key} :: Datadog {before} -> {after} at {monitor.get('overall_state_modified')}: {monitor['name']}\n"
                    )
    with open(args.state + ".new", "w") as out:
        json.dump(states, out)
    os.replace(args.state + ".new", args.state)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(usage=USAGE)
    parser.add_argument("mode", choices=["once", "watch"])
    parser.add_argument("--state", required=True)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--timeout", type=float, default=1740)
    parser.add_argument("--alert-inbox")
    args = parser.parse_args()
    if not args.tag and not args.id:
        parser.error("name at least one --tag or --id")
    failing = poll(args, False)
    deadline = time.monotonic() + args.timeout
    while args.mode == "watch" and time.monotonic() + args.interval < deadline:
        time.sleep(args.interval)
        failing = poll(args, failing)


if __name__ == "__main__":
    sys.exit(main())
