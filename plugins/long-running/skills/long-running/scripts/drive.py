#!/usr/bin/env python3
"""The drive registry: which long-running drive, and so which ledger, a session's pull requests belong to.

    drive.py start   --ledger ID [--drive ID] [--orca-run ID] [--state-dir DIR] [--cci-drive NAME]
    drive.py desk    --name NAME (--terminal HANDLE [--environment NAME] [--supervisor] | --remove) [--drive ID]
    drive.py end     [--drive ID]
    drive.py context [--drive ID]
    drive.py current
    drive.py orca-run
    drive.py list    [--json]
    drive.py record  --session ID --lane NAME --cwd DIR [--drive ID] --pr [OWNER/NAME#]N[=SHA]...
    drive.py landed  --session ID [--drive ID] --pr N...
    drive.py thread  --session ID --lane NAME [--drive ID] --channel ID --thread-ts TS --posted-ts TS

STDLIB ONLY. One file per drive at ``$CLAUDE_CONFIG_DIR/long-running/drives/<drive>.json`` (default ``~/.claude``) names the
drive's ledger, its repository, the git common dir every checkout of that repository shares,
the checkout the drive started in, every root session that has run it, its Orca run, and its state
directory, ``~/.claude/scratch/<drive>`` unless ``start --state-dir`` names another.
``start`` is an upsert: the resumed root of a handoff runs it again and joins ``sessions``.
Run inside an Orca terminal, it records that terminal as ``root_terminal``, the coordinator's.
``desk`` records a standing desk's Orca terminal under ``desks``, with ``--environment`` for one on a
remote host. ``--supervisor`` marks the one desk that hears of the coordinator's own prompts, by
cci records addressed to its name; ``--remove`` forgets a desk. ``prompt_watch.py`` reads all three.
Every registry write also writes ``<state dir>/dashboard/context.json``, the facts the drive's
live dashboard binds its cards from; ``context`` rewrites it from the registry alone.

``record`` is the capt-hook pack's entry point after a command opened or pushed pull requests.
A session belongs to a drive when its id is one of the drive's root sessions, which covers every
in-process subagent and teammate, or when it carries ``CLAUDE_LONG_RUNNING_DRIVE``, which Orca workers
inherit from ``orca-launch.sh``. ``current`` also resolves the drive whose Orca run is ``ORCA_LAUNCH_RUN``, so a
desk runner that holds no session still launches workers into the drive. ``orca-run`` prints the Orca run
of the drive this session belongs to, which ``orca-launch.sh`` launches into when ``ORCA_LAUNCH_RUN`` is unset.
A session in no drive, a command run outside the drive's repository, and a pull request on another repository
are not the drive's and record nothing; a session in no drive says so on stderr.

``landed`` is the pack's entry point after a ``cci post --kind landed`` or a ``gh pr close``: it settles each named
PR the drive's ledger tracks against the trunk in the drive's checkout through ``ledger.py landed``, so the row
leaves the open set in the same step that announced the landing or the close.

``thread`` is the pack's entry point after a Slack post: it appends the posted thread to
``<state dir>/slack/watched-threads.jsonl``, the list the drive's Slack watch lane polls.
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
DRIVE_ENV = ledger.DRIVE_ENV
ORCA_RUN_ENV = "ORCA_LAUNCH_RUN"
TERMINAL_ENV = "ORCA_TERMINAL_HANDLE"
DRIVE_ID_LENGTH = 8
WATCHED_THREADS = Path("slack") / "watched-threads.jsonl"
DASHBOARD_SERVER = Path("dashboard") / "server.json"
DASHBOARD_CONTEXT = Path("dashboard") / "context.json"
PACK = Path(__file__).resolve().parents[3] / "dashboard"
PR_SPEC = re.compile(r"^(?:(?P<repo>[\w.-]+/[\w.-]+)#)?(?P<pr>\d+)(?:=(?P<head>[0-9a-f]{7,40}))?$")


def drives_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "long-running" / "drives"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def common_dir(cwd: Path) -> str:
    return git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def drives() -> list[dict]:
    return [load(path) for path in sorted(drives_dir().glob("*.json"))]


def find(drive: str | None, session: str | None) -> dict | None:
    for entry in drives():
        if entry["drive"] == drive or (not drive and session in entry["sessions"]):
            return entry
    return None


def dashboard_url(server: dict) -> str:
    return server.get("tailnet_url", server["url"])


def write_atomic(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(json.dumps(payload, indent=2) + "\n")
    staged.replace(path)
    return path


def cci_drive(entry: dict) -> str:
    return entry.get("cci_drive") or Path(entry["state_dir"]).name


def context_of(entry: dict) -> dict:
    program = Path(entry["state_dir"]).name
    return {
        "id": entry["drive"],
        "title": program,
        "program": program,
        "repo": entry["repo"],
        "checkout": entry["checkout"],
        "ledger": entry["ledger"],
        "sessions": entry["sessions"],
        "cci_drive": cci_drive(entry),
        "orca_run": entry["orca_run"],
        "state_dir": entry["state_dir"],
        "started_at": entry["started_at"],
        "packs": {"lr": str(PACK)},
    }


def write_context(entry: dict) -> Path:
    path = Path(entry["state_dir"]) / DASHBOARD_CONTEXT
    task_facts = json.loads(path.read_text()) if path.exists() else {}
    return write_atomic(path, task_facts | context_of(entry))


def save(entry: dict) -> Path:
    write_context(entry)
    return write_atomic(drives_dir() / f"{entry['drive']}.json", entry)


def session_id() -> str:
    if not (session := os.environ.get(SESSION_ENV)):
        raise SystemExit(f"{SESSION_ENV} is unset; run this from the drive's root Claude session")
    return session


def current_drive() -> str | None:
    if drive := os.environ.get(DRIVE_ENV):
        return drive
    entry = find(None, os.environ.get(SESSION_ENV))
    return entry["drive"] if entry else None


def orca_run_drive() -> str | None:
    run = os.environ.get(ORCA_RUN_ENV)
    return next((entry["drive"] for entry in drives() if run and entry["orca_run"] == run), None)


def cmd_start(args: argparse.Namespace, shell: ledger.Shell) -> int:
    session = session_id()
    drive = args.drive or current_drive() or session[:DRIVE_ID_LENGTH]
    if (joined := find(None, session)) and joined["drive"] != drive:
        raise SystemExit(f"this session already runs drive {joined['drive']}; end it with drive.py end before starting {drive}")
    cwd = Path.cwd()
    entry = find(drive, None) or {"drive": drive, "sessions": [], "orca_run": None, "started_at": stamp()}
    entry |= {
        "ledger": args.ledger,
        "repo": ledger.origin_repo(ledger.Shell(), cwd),
        "git_common_dir": common_dir(cwd),
        "checkout": str(cwd),
        "sessions": [*entry["sessions"], session] if session not in entry["sessions"] else entry["sessions"],
        "orca_run": args.orca_run or entry["orca_run"],
        "state_dir": str(args.state_dir or entry.get("state_dir") or Path.home() / ".claude" / "scratch" / drive),
        "cci_drive": args.cci_drive or entry.get("cci_drive"),
        "root_terminal": os.environ.get(TERMINAL_ENV) or entry.get("root_terminal"),
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


def cmd_desk(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := args.drive or current_drive()):
        raise SystemExit("this session runs no drive; pass --drive")
    if not (entry := find(drive, None)):
        raise SystemExit(f"no drive {drive} in {drives_dir()}")
    desks = dict(entry.get("desks") or {})
    if args.remove:
        if args.name not in desks:
            raise SystemExit(f"drive {drive} has no desk {args.name}")
        del desks[args.name]
    else:
        if args.supervisor:
            desks = {name: desk | {"supervisor": False} for name, desk in desks.items()}
        desks[args.name] = {"terminal": args.terminal, "environment": args.environment, "supervisor": args.supervisor}
    save(entry | {"desks": desks, "updated_at": stamp()})
    listed = [f"{name}={desk['terminal']}{' (supervisor)' if desk['supervisor'] else ''}" for name, desk in sorted(desks.items())]
    print(f"drive {drive} desks: {', '.join(listed) or 'none'}")
    return 0


def cmd_context(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := args.drive or current_drive()):
        raise SystemExit("this session runs no drive; pass --drive")
    if not (entry := find(drive, None)):
        raise SystemExit(f"no drive {drive} in {drives_dir()}")
    print(write_context(entry))
    return 0


def cmd_current(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := current_drive() or orca_run_drive()):
        return 1
    print(drive)
    return 0


def cmd_orca_run(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := current_drive()) or not (entry := find(drive, None)) or not entry["orca_run"]:
        return 1
    print(entry["orca_run"])
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
    if not (entry := find(args.drive, args.session)):
        print(
            f"no drive claims session {args.session} (lane {args.lane}, {DRIVE_ENV}={args.drive or 'unset'}); "
            f"PR {', '.join('#' + match['pr'] for match in args.pr)} not recorded",
            file=sys.stderr,
        )
        return 0
    if not in_repo(args.cwd, entry):
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


def cmd_landed(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (entry := find(args.drive, args.session)):
        return 0
    argv = ["-C", entry["checkout"], "landed", "--repo", entry["repo"], "--ledger", entry["ledger"], "--checkout", entry["checkout"]]
    argv += [flag for pr in args.pr for flag in ("--pr", pr)]
    try:
        return ledger.main(argv, shell)
    except (subprocess.CalledProcessError, OSError) as failure:
        reason = (getattr(failure, "stderr", "") or str(failure)).strip().splitlines()[-1:] or ["no reason given"]
        print(f"PR {', '.join('#' + pr for pr in args.pr)} not settled in ledger {entry['ledger']}: {reason[0]} — run `ledger.py {' '.join(argv)}`", file=sys.stderr)
        return 1


def cmd_thread(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (entry := find(args.drive, args.session)):
        return 0
    path = Path(entry["state_dir"]) / WATCHED_THREADS
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "channel": args.channel,
        "thread_ts": args.thread_ts,
        "posted_ts": args.posted_ts,
        "posted_at": stamp(),
        "session": args.session,
        "lane": args.lane,
    }
    with path.open("a") as out:
        out.write(json.dumps(row) + "\n")
    print(f"{args.channel}/{args.thread_ts} is on the drive's Slack watch list at {path}")
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
    start.add_argument("--state-dir", type=Path, metavar="DIR", help="the drive's scratch directory; defaults to ~/.claude/scratch/<drive>")
    start.add_argument("--cci-drive", metavar="NAME", help="the cci drive the lanes post to; defaults to the state dir's name")
    start.set_defaults(handler=cmd_start)

    desk = subparsers.add_parser("desk", help="record a standing desk's Orca terminal for the prompt watch, or forget one")
    desk.add_argument("--name", required=True, help="the desk's lane name; a supervisor's cci records are addressed to it")
    desk.add_argument("--drive")
    target = desk.add_mutually_exclusive_group(required=True)
    target.add_argument("--terminal", metavar="HANDLE")
    target.add_argument("--remove", action="store_true")
    desk.add_argument("--environment", metavar="NAME", help="the Orca environment of a desk on a remote host")
    desk.add_argument("--supervisor", action="store_true", help="the one desk told of the coordinator's own prompts")
    desk.set_defaults(handler=cmd_desk)

    end = subparsers.add_parser("end", help="the drive is over; its PRs stop being recorded")
    end.add_argument("--drive")
    end.set_defaults(handler=cmd_end)

    context = subparsers.add_parser("context", help="rewrite the drive dashboard's context.json from the registry")
    context.add_argument("--drive")
    context.set_defaults(handler=cmd_context)

    current = subparsers.add_parser("current", help="print the drive this session belongs to; exit 1 when none")
    current.set_defaults(handler=cmd_current)

    orca_run = subparsers.add_parser("orca-run", help="print the Orca run of the drive this session belongs to; exit 1 when none")
    orca_run.set_defaults(handler=cmd_orca_run)

    list_cmd = subparsers.add_parser("list", help="every registered drive")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(handler=cmd_list)

    record = subparsers.add_parser("record", help="register PRs a command opened or pushed in the drive's ledger")
    record.add_argument("--session", required=True)
    record.add_argument("--drive", help="the drive named by the session's CLAUDE_LONG_RUNNING_DRIVE")
    record.add_argument("--lane", required=True)
    record.add_argument("--cwd", required=True, type=Path)
    record.add_argument("--pr", required=True, action="append", type=pr_spec, metavar="[OWNER/NAME#]N[=SHA]")
    record.set_defaults(handler=cmd_record)

    landed = subparsers.add_parser("landed", help="settle PRs a command announced as landed or closed in the drive's ledger")
    landed.add_argument("--session", required=True)
    landed.add_argument("--drive", help="the drive named by the session's CLAUDE_LONG_RUNNING_DRIVE")
    landed.add_argument("--pr", required=True, action="append", type=ledger.pr_number)
    landed.set_defaults(handler=cmd_landed)

    thread = subparsers.add_parser("thread", help="add a Slack thread a session posted in to the drive's watch list")
    thread.add_argument("--session", required=True)
    thread.add_argument("--drive", help="the drive named by the session's CLAUDE_LONG_RUNNING_DRIVE")
    thread.add_argument("--lane", required=True)
    thread.add_argument("--channel", required=True)
    thread.add_argument("--thread-ts", required=True)
    thread.add_argument("--posted-ts", required=True)
    thread.set_defaults(handler=cmd_thread)

    return parser


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or ledger.Shell())


if __name__ == "__main__":
    raise SystemExit(main())
