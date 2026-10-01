#!/usr/bin/env python3
"""The drive registry: which long-running drive, and so which ledger, a session's pull requests belong to.

    drive.py start   --ledger ID [--drive ID] [--orca-run ID]
    drive.py end     [--drive ID]
    drive.py current
    drive.py list    [--json]
    drive.py record  --session ID --lane NAME --cwd DIR [--drive ID] --pr [OWNER/NAME#]N[=SHA]...

STDLIB ONLY. One file per drive at ``~/.claude/long-running/drives/<drive>.json`` names the
drive's ledger, its repository, the git common dir every checkout of that repository shares,
the checkout the drive started in, every root session that has run it, and its Orca run.
``start`` is an upsert: the resumed root of a handoff runs it again and joins ``sessions``.

``record`` is the capt-hook pack's entry point after a command opened or pushed pull requests.
A session belongs to a drive when its id is one of the drive's root sessions, which covers every
in-process subagent and teammate, or when it carries ``LONG_RUNNING_DRIVE``, which Orca workers
inherit from ``orca-launch.sh``. A session in no drive, a command run outside the drive's
repository, and a pull request on another repository are not the drive's and record nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import ledger

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
DRIVE_ENV = "LONG_RUNNING_DRIVE"
DRIVE_ID_LENGTH = 8
PR_SPEC = re.compile(r"^(?:(?P<repo>[\w.-]+/[\w.-]+)#)?(?P<pr>\d+)(?:=(?P<head>[0-9a-f]{7,40}))?$")
REMOTE = re.compile(r"[:/](?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?/?$")


def drives_dir() -> Path:
    return Path.home() / ".claude" / "long-running" / "drives"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def common_dir(cwd: Path) -> str:
    return git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")


def origin_repo(cwd: Path) -> str:
    url = git(cwd, "remote", "get-url", "origin")
    if not (match := REMOTE.search(url)):
        raise SystemExit(f"origin {url!r} names no owner/name")
    return match["repo"]


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def drives() -> list[dict]:
    return [load(path) for path in sorted(drives_dir().glob("*.json"))]


def find(drive: str | None, session: str | None) -> dict | None:
    for entry in drives():
        if entry["drive"] == drive or (not drive and session in entry["sessions"]):
            return entry
    return None


def save(entry: dict) -> Path:
    path = drives_dir() / f"{entry['drive']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(json.dumps(entry, indent=2) + "\n")
    staged.replace(path)
    return path


def session_id() -> str:
    if not (session := os.environ.get(SESSION_ENV)):
        raise SystemExit(f"{SESSION_ENV} is unset; run this from the drive's root Claude session")
    return session


def current_drive() -> str | None:
    if drive := os.environ.get(DRIVE_ENV):
        return drive
    entry = find(None, os.environ.get(SESSION_ENV))
    return entry["drive"] if entry else None


def cmd_start(args: argparse.Namespace, shell: ledger.Shell) -> int:
    session = session_id()
    drive = args.drive or current_drive() or session[:DRIVE_ID_LENGTH]
    cwd = Path.cwd()
    entry = find(drive, None) or {"drive": drive, "sessions": [], "orca_run": None, "started_at": stamp()}
    entry |= {
        "ledger": args.ledger,
        "repo": origin_repo(cwd),
        "git_common_dir": common_dir(cwd),
        "checkout": str(cwd),
        "sessions": [*entry["sessions"], session] if session not in entry["sessions"] else entry["sessions"],
        "orca_run": args.orca_run or entry["orca_run"],
        "updated_at": stamp(),
    }
    print(f"drive {drive} on ledger {args.ledger} at {save(entry)}")
    return 0


def cmd_end(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := args.drive or current_drive()):
        raise SystemExit("this session runs no drive; pass --drive")
    path = drives_dir() / f"{drive}.json"
    if not path.exists():
        raise SystemExit(f"no drive {drive} in {drives_dir()}")
    path.unlink()
    print(f"ended drive {drive}")
    return 0


def cmd_current(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := current_drive()):
        return 1
    print(drive)
    return 0


def cmd_list(args: argparse.Namespace, shell: ledger.Shell) -> int:
    entries = drives()
    if args.json:
        print(json.dumps(entries))
        return 0
    for entry in entries:
        print(f"{entry['drive']} ledger={entry['ledger']} repo={entry['repo']} checkout={entry['checkout']} sessions={len(entry['sessions'])}")
    return 0


def in_repo(cwd: Path, entry: dict) -> bool:
    try:
        return common_dir(cwd) == entry["git_common_dir"]
    except subprocess.CalledProcessError:
        return False


def cmd_record(args: argparse.Namespace, shell: ledger.Shell) -> int:
    entry = find(args.drive, args.session)
    if not entry or not in_repo(args.cwd, entry):
        return 0
    specs = [match for match in args.pr if (match["repo"] or entry["repo"]).lower() == entry["repo"].lower()]
    os.chdir(args.cwd)
    for spec in specs:
        argv = ["register", "--ledger", entry["ledger"], "--lane", args.lane, "--pr", spec["pr"]]
        argv += ["--head", spec["head"]] if spec["head"] else []
        try:
            ledger.main(argv, shell)
        except (subprocess.CalledProcessError, SystemExit, OSError) as failure:
            reason = (getattr(failure, "stderr", "") or str(failure)).strip().splitlines()[-1:] or ["no reason given"]
            print(
                f"PR #{spec['pr']} was not recorded in ledger {entry['ledger']}: {reason[0]} — run "
                f"`ledger.py {' '.join(argv)}` from {args.cwd}",
                file=sys.stderr,
            )
            return 1
    return 0


def pr_spec(value: str) -> re.Match:
    if not (match := PR_SPEC.match(value)):
        raise argparse.ArgumentTypeError(f"{value!r} is not [OWNER/NAME#]N[=SHA]")
    return match


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drive.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="register this session's drive, or join it after a handoff")
    start.add_argument("--ledger", required=True)
    start.add_argument("--drive", help="the drive id; defaults to the drive this session already runs, else its id's first 8 characters")
    start.add_argument("--orca-run", metavar="ID")
    start.set_defaults(handler=cmd_start)

    end = subparsers.add_parser("end", help="the drive is over; its PRs stop being recorded")
    end.add_argument("--drive")
    end.set_defaults(handler=cmd_end)

    current = subparsers.add_parser("current", help="print the drive this session belongs to; exit 1 when none")
    current.set_defaults(handler=cmd_current)

    list_cmd = subparsers.add_parser("list", help="every registered drive")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(handler=cmd_list)

    record = subparsers.add_parser("record", help="register PRs a command opened or pushed in the drive's ledger")
    record.add_argument("--session", required=True)
    record.add_argument("--drive", help="the drive named by the session's LONG_RUNNING_DRIVE")
    record.add_argument("--lane", required=True)
    record.add_argument("--cwd", required=True, type=Path)
    record.add_argument("--pr", required=True, action="append", type=pr_spec, metavar="[OWNER/NAME#]N[=SHA]")
    record.set_defaults(handler=cmd_record)

    return parser


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or ledger.Shell())


if __name__ == "__main__":
    raise SystemExit(main())
