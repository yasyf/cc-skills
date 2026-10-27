#!/usr/bin/env python3
"""The drive registry: which long-running drive, and so which ledger, a session's pull requests belong to.

    drive.py start   --ledger ID [--drive ID] [--orca-run ID] [--state-dir DIR] [--cci-drive NAME] [--program SLUG] [--register-lane NAME]
    drive.py desk    --name NAME (--terminal HANDLE [--environment NAME] [--supervisor] | --remove) [--drive ID]
    drive.py end     [--drive ID]
    drive.py context [--drive ID]
    drive.py current
    drive.py orca-run
    drive.py list    [--json]
    drive.py record  --session ID --lane NAME --cwd DIR [--drive ID] [--opened] --pr [OWNER/NAME#]N[=SHA]...
    drive.py landed  --session ID [--drive ID] --pr N...
    drive.py lane-status LANE [--drive ID]
    drive.py pr-owed LANE [--drive ID]
    drive.py digest  [--drive ID]
    drive.py worktree --lane NAME --path DIR [--session ID] [--drive ID]
    drive.py release --cci-drive NAME --owner LANE@DRIVE --build BUILD [--release ID]

STDLIB ONLY. One file per drive at ``$CLAUDE_CONFIG_DIR/long-running/drives/<drive>.json`` (default ``~/.claude``) names the
drive's ledger, its repository, the git common dir every checkout of that repository shares,
the checkout the drive started in, every root session that has run it, its Orca run, and its state
directory, ``~/.claude/scratch/<drive>`` unless ``start --state-dir`` names another, and its ``program``:
the plan slug every worker resolves the register by, set with ``start --program`` and otherwise the
state directory's name when the entry is first written.
``start`` is an upsert: the resumed root of a handoff runs it again and joins ``sessions``.
Run inside an Orca terminal, it records that terminal as ``root_terminal``, the coordinator's.
``desk`` records a standing desk's Orca terminal under ``desks``, with ``--environment`` for one on a
remote host. ``--supervisor`` marks the one desk that hears of the coordinator's own prompts, by
cci records addressed to its name and one line typed at its idle prompt, and prints the
``cci watch`` command a desk that holds a wait runs instead; ``--remove`` forgets a desk. ``prompt_watch.py`` reads all three.
Every registry write also writes ``<state dir>/dashboard/context.json``, the facts the drive's
live dashboard binds its cards from; ``context`` rewrites it from the registry alone.

``record`` is the capt-hook pack's entry point after a command opened or pushed pull requests.
A session belongs to a drive when its id is one of the drive's root sessions, which covers every
in-process subagent and teammate, or when it carries ``CLAUDE_LONG_RUNNING_DRIVE``, which Orca workers
inherit from ``orca-launch.sh``. ``current`` also resolves the drive whose Orca run is ``ORCA_LAUNCH_RUN``, so a
desk runner that holds no session still launches workers into the drive. ``orca-run`` prints the Orca run
of the drive this session belongs to, which ``orca-launch.sh`` launches into when ``ORCA_LAUNCH_RUN`` is unset.
A session in no drive, a command run outside the drive's repository, and a pull request on another repository
are not the drive's and record nothing; a session in no drive says so on stderr. ``--opened`` also posts each
PR's ``opened`` record on the drive's cci drive under ``--lane``, for a Codex worker, whose ``ccx vcs ship``
runs no PR hook and binds no cci lane, so neither the ledger row nor the ``opened`` record exists unless it
runs ``record`` itself.

``landed`` is the pack's entry point after a ``cci post --kind landed`` or a ``gh pr close``: it settles each named
PR the drive's ledger tracks against the trunk in the drive's checkout through ``ledger.py landed``, so the row
leaves the open set in the same step that announced the landing or the close.

``lane-status`` answers what code a lane holds. It prints one line per worktree the lane owns, found by
``lanes.lane_worktrees`` from the drive's launch receipts, the lane's cci records of the last day, the
checkout's ``git worktree list``, and its PRs' head branches. Each line names the branch, dirty files,
commits ahead of ``origin/<trunk>``, the last commit's age, whether the branch is pushed, and its open PR
with CI state. With none found it prints ``no worktree found for lane <lane>`` and exits 1.

``pr-owed`` answers whether a lane still owes a PR for its mechanism, from the drive's records of the last 12 hours.
It prints ``owed: <mark> #<seq>`` and exits 0 when the lane's newest MECHANISM or CANDIDATE has no settling record of its own
and no PR, ready, fix-live, or root go from any lane answers it by ``--re``, topic, or incident tag such as ``SAND-5A``.
Otherwise it prints ``not owed: <why>`` and exits 1, as it does for an evidence, proof, closeout, desk, or audit lane.

``digest`` prints one line per desk runner from its heartbeat in ``<state dir>/inbox``: its pid, the plugin version it
runs, the version ``installed_plugins.json`` lists, and its last pass. A runner behind the installed version, or one whose
heartbeat names no version, reads stale with the ``desk-runner.py handoff`` command that rotates it; the
command exits 1 while any desk is stale or has no heartbeat.

``worktree`` is the pack's entry point the first time a lane runs a command in a worktree: it posts one
``state`` record ``WORKTREE <lane> <path> <branch>`` with a ``worktree`` attr, keyed
``worktree:<lane>:<path>`` so it posts once. A path outside the drive's repository posts nothing and exits 3.

``release`` is the desk runner's entry point when a lane's ``release --build`` record names a build: it records
the build under ``releases`` in the registry entry of the drive whose cci drive is ``--cci-drive``, with its owner
``<lane>@<drive>``, its release id, and the time, and exits 1 when no registered drive posts to that cci drive.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cci
import lanes
import ledger

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
DRIVE_ENV = ledger.DRIVE_ENV
ORCA_RUN_ENV = "ORCA_LAUNCH_RUN"
TERMINAL_ENV = "ORCA_TERMINAL_HANDLE"
CALLER_VERSION_ENV = "LONG_RUNNING_CALLER_VERSION"
DRIVE_ID_LENGTH = 8
DASHBOARD_CONTEXT = Path("dashboard") / "context.json"
PACK = Path(__file__).resolve().parents[3] / "dashboard"
PR_SPEC = re.compile(r"^(?:(?P<repo>[\w.-]+/[\w.-]+)#)?(?P<pr>\d+)(?:=(?P<head>[0-9a-f]{7,40}))?$")
LANE_RECORDS = timedelta(days=1)
PR_OWED_WINDOW = timedelta(hours=12)
OUTSIDE_REPO = 3
LAUNCH_RECEIPTS = Path(".claude") / "scratch" / "orca-launch"
PLUGIN = Path(__file__).resolve().parents[3]
HEARTBEATS = {"orca": "orca-runner.heartbeat.json", "landing": "landing-runner.heartbeat.json"}
VERSION_PART = re.compile(r"\d+")


def drives_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "long-running" / "drives"


def plugin_manifest() -> dict:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())


def newest_install() -> dict | None:
    """The newest install of this plugin Claude Code lists in ``installed_plugins.json``, or None when it lists none."""
    path = drives_dir().parents[1] / "plugins" / "installed_plugins.json"
    if not path.is_file():
        return None
    name = plugin_manifest()["name"]
    installs = [install for key, entries in load(path)["plugins"].items() if key.partition("@")[0] == name for install in entries]
    return max(installs, key=lambda install: version_key(install["version"]), default=None)


def installed_version() -> str:
    return (newest_install() or {}).get("version", "")


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in VERSION_PART.findall(version.partition("-")[0]))


def behind(running: str, installed: str) -> bool:
    """Whether a runner reporting ``running``, empty for one that reports none, runs older code than the installed version."""
    return bool(installed) and (not running or version_key(running) < version_key(installed))


def run_installed(script: str) -> None:
    """Re-execute `script` from the installed plugin version, as the `bin/` shim does, when it is a copy of an older version, the installed copy's own manifest is newer, and no process of its own version started it, so a path that names an old version still runs current code and never writes a line the current runner cannot read; a process that passes marks its children with its version, so a runner started before an update keeps its own scripts until it re-executes."""
    running = plugin_manifest()["version"]
    if os.environ.get(CALLER_VERSION_ENV) != running and (install := newest_install()) and behind(running, install["version"]):
        manifest = Path(install["installPath"]) / ".claude-plugin" / "plugin.json"
        if manifest.is_file() and behind(running, load(manifest)["version"]):
            fresh = manifest.parents[1] / Path(__file__).resolve().parent.relative_to(PLUGIN) / Path(script).resolve().name
            os.execv(sys.executable, [sys.executable, str(fresh), *sys.argv[1:]])
    os.environ[CALLER_VERSION_ENV] = running


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


def write_atomic(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(json.dumps(payload, indent=2) + "\n")
    staged.replace(path)
    return path


def program_of(entry: dict) -> str:
    return entry.get("program") or Path(entry["state_dir"]).name


def cci_drive(entry: dict) -> str:
    return entry.get("cci_drive") or program_of(entry)


def context_of(entry: dict) -> dict:
    program = program_of(entry)
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
    state = str(args.state_dir or entry.get("state_dir") or Path.home() / ".claude" / "scratch" / drive)
    entry |= {
        "ledger": args.ledger,
        "repo": ledger.origin_repo(ledger.Shell(), cwd),
        "git_common_dir": common_dir(cwd),
        "checkout": str(cwd),
        "sessions": [*entry["sessions"], session] if session not in entry["sessions"] else entry["sessions"],
        "orca_run": args.orca_run or entry["orca_run"],
        "state_dir": state,
        "program": args.program or entry.get("program") or Path(state).name,
        "cci_drive": args.cci_drive or entry.get("cci_drive"),
        "register_lane": args.register_lane or entry.get("register_lane"),
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
    if not args.remove and args.supervisor:
        print(f"{args.name} gets one typed line at its idle prompt; while it holds a wait it reads busy and hears only through: cci watch --drive {cci_drive(entry)} --to {args.name} --for 0")
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
        if args.opened:
            shell.run(["cci", "post", "--drive", cci_drive(entry), "--lane", args.lane, "--kind", "opened", "--pr", spec["pr"], "--text", f"opened #{spec['pr']}"])
    return 0


def cmd_release(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (entry := next((entry for entry in drives() if cci_drive(entry) == args.cci_drive), None)):
        print(f"no registered drive posts to cci drive {args.cci_drive}; build {args.build} not recorded", file=sys.stderr)
        return 1
    entry.setdefault("releases", {})[args.build] = {"owner": args.owner, "release": args.release, "at": stamp()}
    save(entry)
    print(f"build {args.build} owned by {args.owner}")
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


def resolve(args: argparse.Namespace) -> dict | None:
    entry = find(args.drive or current_drive(), getattr(args, "session", None) or session_id())
    if entry is None:
        print("this session belongs to no drive", file=sys.stderr)
    return entry


def receipt_dirs(entry: dict) -> list[Path]:
    dirs = [Path.home() / LAUNCH_RECEIPTS / entry["orca_run"]] if entry.get("orca_run") else []
    return dirs + sorted(Path(entry["state_dir"]).glob("receipts*")) if entry.get("state_dir") else dirs


def trunk_of(shell: ledger.Shell, checkout: str) -> str:
    ref = lanes.output(shell.run, ["git", "-C", checkout, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]) or "origin/main"
    return ref.strip().removeprefix("origin/")


def cmd_lane_status(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if (entry := resolve(args)) is None:
        return 1
    since = (datetime.now(timezone.utc) - LANE_RECORDS).strftime("%Y-%m-%dT%H:%M:%SZ")
    drive = cci_drive(entry)
    records = [record for record in cci.records(shell.run, drive, "--lane", args.lane, since=since) if record["drive"] == drive and record["lane"] == args.lane]
    worktrees = lanes.lane_status(args.lane, records, receipt_dirs(entry), Path(entry["checkout"]), entry["repo"], trunk_of(shell, entry["checkout"]), datetime.now(timezone.utc), shell.run)
    if not worktrees:
        print(f"no worktree found for lane {args.lane}")
        return 1
    print("\n".join(worktree.line() for worktree in worktrees))
    return 0


def cmd_pr_owed(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if (entry := resolve(args)) is None:
        return 1
    if lanes.owes_no_pr(Path(entry["state_dir"]), args.lane):
        print(f"not owed: {args.lane} is an evidence, proof, closeout, desk, or audit lane")
        return 1
    since = (datetime.now(timezone.utc) - PR_OWED_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    drive = cci_drive(entry)
    kinds = sorted({*cci.ANSWER_KINDS, *cci.ROOT_ANSWER_KINDS, *cci.TEXT_KINDS})
    others = cci.records(shell.run, drive, *(f"--kind={kind}" for kind in kinds), since=since)
    own = cci.records(shell.run, drive, "--lane", args.lane, since=since)
    records = list({record["seq"]: record for record in [*others, *own] if record["drive"] == drive}.values())
    if (owed := lanes.owed_mechanism(args.lane, records)) is None:
        print(f"not owed: no mechanism of {args.lane} waits on a PR")
        return 1
    print(f"owed: {cci.mechanism_mark(owed)} #{owed['seq']}")
    return 0


def cmd_digest(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if (entry := resolve(args)) is None:
        return 1
    installed = installed_version()
    current = True
    for desk, name in HEARTBEATS.items():
        path = Path(entry["state_dir"]) / "inbox" / name
        if not path.is_file():
            current = False
            print(f"{desk}: no heartbeat at {path}, so no {desk} runner has finished a pass")
            continue
        beat = load(path)
        running = beat.get("version", "")
        late = behind(running, installed)
        current &= not late
        state = f"stale; rotate it with `desk-runner.py handoff --config {beat['config']} --desk {desk}`, which starts the next one" if late else "current"
        print(f"{desk}: pid {beat['pid']} runs {running or 'a version it does not report'}, installed {installed or 'unknown'}, last pass {beat['at']}: {state}")
    return 0 if current else 1


def cmd_worktree(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if (entry := resolve(args)) is None:
        return 1
    top = lanes.output(shell.run, ["git", "-C", str(args.path), "rev-parse", "--show-toplevel"])
    if top is None or not in_repo(Path(top.strip()), entry):
        return OUTSIDE_REPO
    top = top.strip()
    branch = (lanes.output(shell.run, ["git", "-C", top, "branch", "--show-current"]) or "").strip() or "detached"
    shell.run(
        ["cci", "post", "--drive", cci_drive(entry), "--lane", args.lane, "--kind", "state", "--to", "root", "--id", f"worktree:{args.lane}:{top}", "--attr", f"worktree={top}", "--attr", f"branch={branch}", "--text", f"WORKTREE {args.lane} {top} {branch}"]
    )
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
    start.add_argument("--program", metavar="SLUG", help="the plan slug workers resolve the register by; defaults to the state dir's name on a new entry")
    start.add_argument("--register-lane", metavar="NAME", help="the lane that classifies owner captures; their blockers go to it instead of the root")
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
    record.add_argument("--opened", action="store_true", help="also post each PR's opened record on the drive's cci drive under --lane")
    record.add_argument("--pr", required=True, action="append", type=pr_spec, metavar="[OWNER/NAME#]N[=SHA]")
    record.set_defaults(handler=cmd_record)

    landed = subparsers.add_parser("landed", help="settle PRs a command announced as landed or closed in the drive's ledger")
    landed.add_argument("--session", required=True)
    landed.add_argument("--drive", help="the drive named by the session's CLAUDE_LONG_RUNNING_DRIVE")
    landed.add_argument("--pr", required=True, action="append", type=ledger.pr_number)
    landed.set_defaults(handler=cmd_landed)

    lane_status = subparsers.add_parser("lane-status", help="every worktree a lane owns with its branch, dirty files, push state, and open PR; exit 1 when none")
    lane_status.add_argument("lane")
    lane_status.add_argument("--drive")
    lane_status.set_defaults(handler=cmd_lane_status)

    pr_owed = subparsers.add_parser("pr-owed", help="whether a lane's mechanism still waits on a PR; exit 1 when not")
    pr_owed.add_argument("lane")
    pr_owed.add_argument("--drive")
    pr_owed.set_defaults(handler=cmd_pr_owed)

    digest = subparsers.add_parser("digest", help="each desk runner's running version beside the installed one; exit 1 when one is behind or has no heartbeat")
    digest.add_argument("--drive")
    digest.set_defaults(handler=cmd_digest)

    worktree = subparsers.add_parser("worktree", help="post the WORKTREE record that maps a lane to a worktree it works in")
    worktree.add_argument("--lane", required=True)
    worktree.add_argument("--path", required=True, type=Path)
    worktree.add_argument("--session")
    worktree.add_argument("--drive")
    worktree.set_defaults(handler=cmd_worktree)

    release = subparsers.add_parser("release", help="record which lane owns a release build the drive started")
    release.add_argument("--cci-drive", required=True, metavar="NAME", help="the cci drive the owning lane posts to")
    release.add_argument("--owner", required=True, metavar="LANE@DRIVE")
    release.add_argument("--build", required=True, help="the build id or URL the lane's release record names")
    release.add_argument("--release", default="", metavar="ID", help="the release id, such as releases/<date>/<n>")
    release.set_defaults(handler=cmd_release)

    return parser


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or ledger.Shell())


if __name__ == "__main__":
    run_installed(__file__)
    raise SystemExit(main())
