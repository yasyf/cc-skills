#!/usr/bin/env python3
"""`drive_supervisor.py`: the process that runs a drive's supervisor, the v2 loop of `lr/loop.py` over the movers `lr.json` names, kept alive by launchd.

    drive_supervisor.py run --config LR_JSON
    drive_supervisor.py install --config LR_JSON
    drive_supervisor.py uninstall --config LR_JSON
    drive_supervisor.py lanes --config LR_JSON [--json]

STDLIB ONLY. `lr.json` sits in the drive's state directory beside the store `lr.sqlite`, and the
lanes mover writes successor briefs and launch logs under `lanes/` there. `run` takes the drive's
lease and ticks until a newer generation holds it. Liveness is one `cci who` read and one Orca
worker list per tick; a successor launches through `desk-launch.py lane`, detached, so a launch
never holds the tick. `install` writes `~/Library/LaunchAgents/com.long-running.<drive>.supervisor.plist`
and bootstraps that one label, booting out only its own earlier copy; launchd restarts the process
whenever it exits. `lanes` prints the registry, one line per lane, and with `--json` one
`lanes.liveness` object per lane: the drive's one liveness answer, read by every desk.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lr import config as lr_config
from lr import loop
from lr.movers import lanes
from lr.store import Store

SCRIPTS = Path(__file__).resolve().parent
BIN = SCRIPTS.parents[2] / "bin" / "drive_supervisor.py"
DESK_LAUNCH = SCRIPTS / "desk-launch.py"
PROJECTS = Path.home() / ".claude" / "projects"
TEAMS = Path.home() / ".claude" / "teams"
TASKS = Path.home() / ".claude" / "tasks"
UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")
SENDER = "long-running"
RECEIPTS = Path.home() / ".claude" / "scratch" / "orca-launch"
SETTINGS = Path.home() / ".claude" / "settings.json"
TIMEOUT = 60
PAGE_BYTES = 4_000_000
MORE = "resume with --since"
INACTIVE = frozenset({"completed", "failed"})
TAIL_BYTES = 1_000_000
MEASURE_EVERY = timedelta(minutes=5)
WINDOW = 1_000_000
OUTPUT_RESERVE = 20_000
AUTOCOMPACT_BUFFER = 13_000
ROTATE_PERCENT = 70
THROTTLE_SECONDS = 60
OPEN_FILES = 65536


def run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise lanes.Unread(f"{argv[0]} {argv[1]}: {error}") from error
    if done.returncode:
        raise lanes.Unread(f"{' '.join(argv[:3])} exited {done.returncode}: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout


def records(drive: str):
    def read(since: int) -> list[dict]:
        rows: list[dict] = []
        start = str(since) if since else lanes.ADOPT_WINDOW
        while True:
            done = subprocess.run(["cci", "tail", "--drive", drive, "--since", start, "--json", "--budget", str(PAGE_BYTES)], capture_output=True, text=True, timeout=TIMEOUT)
            if done.returncode:
                raise lanes.Unread(f"cci tail exited {done.returncode}: {done.stderr.strip()[:300]}")
            page = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
            rows += page
            if not page or MORE not in done.stderr:
                return rows
            start = str(page[-1]["seq"])

    return read


def dispatch_of(receipts: Path, lane: str) -> str:
    try:
        return json.loads((receipts / f"{lane}.json").read_text())["result"]["dispatchId"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return ""


def orca_rows(run_id: str, receipts: Path, names: set[str] | None, now: datetime) -> list[dict]:
    wanted = {path.stem for path in receipts.glob("*.terminal")} if names is None else names
    dispatches = {lane: dispatch for lane in sorted(wanted) if (dispatch := dispatch_of(receipts, lane))}
    if not dispatches:
        return []
    statuses: dict[str, str] = {}
    cursor: list[str] = []
    while True:
        page = json.loads(run(["orca", "orchestration", "worker-list", "--run", run_id, "--include-remote", "--limit", "100", *cursor, "--json"]))
        if not page.get("ok"):
            raise lanes.Unread(f"orca worker-list: {page.get('error')}")
        statuses |= {row["dispatchId"]: row["dispatchStatus"] for row in page["result"]["workers"]}
        if not (after := (page["result"].get("page") or {}).get("nextCursor")):
            break
        cursor = ["--cursor", after]
    seen = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return [{"lane": lane, "live": True, "seen_at": seen, "dispatch": dispatch} for lane, dispatch in dispatches.items() if statuses.get(dispatch, "completed") not in INACTIVE]


def rostered(rows: list[dict], teams: Path) -> list[dict]:
    """`cci who` rows with each in-process teammate of a live lead session read as live and tagged with its team, since SendMessage resumes it however long it has been idle; its own `live` flag only echoes the lead session's."""
    leads = {row["session"] for row in rows if row.get("live") and row.get("session") and not row.get("agent")}
    members = {session: {member["name"] for member in json.loads(path.read_text())["members"]} for session in leads if (path := teams / f"session-{session[:8]}" / "config.json").is_file()}
    return [row | {"live": True, "roster": str(teams / f"session-{row['session'][:8]}")} if row.get("agent") and row.get("lane") in members.get(row.get("session"), ()) else row for row in rows]


def task_open(tasks: Path, lane: str) -> bool:
    """Whether the root's task list holds an in-progress task the lane owns or whose subject opens with its name."""
    rows = [json.loads(path.read_text()) for path in tasks.glob("*.json")] if tasks.is_dir() else []
    return any(row.get("status") == "in_progress" and (row.get("owner") == lane or row.get("subject", "").startswith(f"{lane}:")) for row in rows)


def append_inbox(inbox: Path, message: dict) -> bool:
    """Append one message to a teammate inbox under the `<inbox>.lock` directory lock Claude Code takes, so its own writes never interleave; a held lock leaves the wake to the next tick."""
    inbox.parent.mkdir(parents=True, exist_ok=True)
    lock = inbox.with_name(f"{inbox.name}.lock")
    try:
        lock.mkdir()
    except FileExistsError:
        return False
    try:
        text = inbox.read_text() if inbox.is_file() else ""
        staged = inbox.with_name(f"{inbox.name}.tmp.{uuid.uuid4().hex[:8]}")
        staged.write_text(json.dumps([*(json.loads(text) if text.strip() else []), message], indent=2, ensure_ascii=False))
        staged.replace(inbox)
    finally:
        lock.rmdir()
    return True


class Waker:
    """Wakes an idle roster lane by teammate-inbox mail: with the blocker paged to it about one of its PRs, or else once per interval while the root's task for it is in progress; a lane whose task is done is idle on purpose."""

    def __init__(self, drive: str, clock, tasks: Path = TASKS):
        self.drive, self.clock, self.tasks = drive, clock, tasks
        self.woken: dict[str, datetime] = {}

    def __call__(self, lane: str, row: dict, page: dict | None) -> bool:
        now = self.clock()
        if page is None and ((last := self.woken.get(lane)) and now - last < lanes.STALE_AFTER or not task_open(self.tasks / f"session-{row['session'][:8]}", lane)):
            return False
        why = page["why"] if page else f"the lane supervisor has seen no record from you in {lanes.STALE_AFTER.seconds // 60} minutes while your root task is in progress."
        text = f"WAKE {lane}: {why} Read your mail with `cci tail --drive {self.drive} --reader {lane} --to {lane}`, then continue the work in hand from your ledger; do not restart your brief."
        message = {"from": SENDER, "text": text, "summary": f"WAKE {lane}: continue the work in hand", "timestamp": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"), "msgV": 1, "msg_id": str(uuid.uuid4()), "type": "message", "read": False}
        if not append_inbox(Path(row["roster"]) / "inboxes" / f"{UNSAFE_NAME.sub('-', lane)}.json", message):
            return False
        self.woken[lane] = now
        return True


def transcript(row: dict) -> Path | None:
    session, agent = row.get("session"), row.get("agent")
    if not session:
        return None
    return next(iter(sorted(PROJECTS.glob(f"*/{session}/subagents/agent-{agent}.jsonl" if agent else f"*/{session}.jsonl"))), None)


def last_tokens(path: Path) -> int | None:
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - TAIL_BYTES))
        lines = handle.read().decode(errors="replace").splitlines()
    for line in reversed(lines):
        if '"compact_boundary"' in line:
            return None
        if '"usage"' not in line or '"assistant"' not in line:
            continue
        try:
            usage = json.loads(line)["message"]["usage"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        return usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
    return None


class Tokens:
    def __init__(self, clock):
        self.clock = clock
        self.measured: dict[str, tuple[datetime, int | None]] = {}

    def __call__(self, row: dict) -> int | None:
        now, lane = self.clock(), row["lane"]
        if (last := self.measured.get(lane)) and now - last[0] < MEASURE_EVERY:
            return last[1]
        path = transcript(row)
        value = last_tokens(path) if path else None
        self.measured[lane] = (now, value)
        return value


def rotate_at() -> int:
    window = json.loads(SETTINGS.read_text()).get("autoCompactWindow") if SETTINGS.is_file() else None
    usable = min(window or WINDOW, WINDOW) - OUTPUT_RESERVE - AUTOCOMPACT_BUFFER
    return usable * ROTATE_PERCENT // 100


def desk_launch(briefs: Path, name: str, *argv: str) -> None:
    with (briefs / f"{name}.launch.log").open("ab") as log:
        subprocess.Popen([sys.executable, str(DESK_LAUNCH), *argv], stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)


def clock() -> datetime:
    return datetime.now(UTC)


def lanes_deps(config: lr_config.Config, state: Path) -> lanes.Deps:
    receipts = RECEIPTS / config.orca_run

    def who(names: set[str] | None) -> list[dict]:
        rows = rostered([json.loads(line) for line in run(["cci", "who", "--drive", config.drive, "--all", "--json"]).splitlines() if line.strip()], TEAMS)
        return rows + (orca_rows(config.orca_run, receipts, names, clock()) if config.orca_run else [])

    briefs = state / "lanes"
    briefs.mkdir(parents=True, exist_ok=True)
    return lanes.Deps(
        drive=config.drive,
        checkout=config.checkout,
        briefs=briefs,
        records=records(config.drive),
        who=who,
        tokens=Tokens(clock),
        transcript=transcript,
        cci=lambda argv: run(["cci", *argv]),
        launch=lambda name, model, effort, brief: desk_launch(briefs, name, "lane", name, model, effort, str(brief), "--drive", config.drive, "--lane", lanes.SELF),
        withdraw=lambda name: desk_launch(briefs, name, "withdraw", name, "--drive", config.drive, "--lane", lanes.SELF),
        wake=Waker(config.drive, clock),
        rotate_at=rotate_at(),
    )


def label(config: lr_config.Config) -> str:
    return f"com.long-running.{config.drive}.supervisor"


def cmd_run(config: lr_config.Config, path: Path) -> int:
    store = Store.open(path.parent / "lr.sqlite")
    deps = loop.Deps(movers=frozenset(config.movers), clock=clock, sleep=time.sleep, lane_deps=lanes_deps(config, path.parent) if "lanes" in config.movers else None)
    version = SCRIPTS.parents[2].name
    ticks = loop.run(store, config.drive, version, os.getpid(), deps)
    print(f"drive_supervisor: generation moved after {len(ticks)} ticks; exiting for the newer supervisor", file=sys.stderr)
    return 0


def launchctl(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *argv], capture_output=True, text=True, timeout=TIMEOUT)


def cmd_install(config: lr_config.Config, path: Path) -> int:
    name = label(config)
    plist = Path.home() / "Library" / "LaunchAgents" / f"{name}.plist"
    log = str(path.parent / "supervisor.log")
    agent = plistlib.dumps({
        "Label": name,
        "ProgramArguments": [str(BIN), "run", "--config", str(path)],
        "EnvironmentVariables": {"PATH": os.environ["PATH"], "CLAUDE_LONG_RUNNING_DRIVE": config.drive},
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "SoftResourceLimits": {"NumberOfFiles": OPEN_FILES},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    })
    target = f"gui/{os.getuid()}/{name}"
    if launchctl("print", target).returncode == 0:
        if plist.is_file() and plist.read_bytes() == agent:
            print(f"drive_supervisor: launchd already runs {name} from {plist}")
            return 0
        if (out := launchctl("bootout", target)).returncode:
            print(f"drive_supervisor: launchctl bootout {target} failed: {(out.stderr or out.stdout).strip()}", file=sys.stderr)
            return 1
        for _ in range(TIMEOUT):
            if launchctl("print", target).returncode:
                break
            time.sleep(1)
        else:
            print(f"drive_supervisor: {target} was still loaded {TIMEOUT} s after its bootout", file=sys.stderr)
            return 1
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(agent)
    if (out := launchctl("bootstrap", f"gui/{os.getuid()}", str(plist))).returncode:
        print(f"drive_supervisor: launchctl bootstrap {plist} failed: {(out.stderr or out.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"drive_supervisor: launchd runs {name} from {plist}, logging to {log}")
    return 0


def cmd_uninstall(config: lr_config.Config, path: Path) -> int:
    name = label(config)
    plist = Path.home() / "Library" / "LaunchAgents" / f"{name}.plist"
    target = f"gui/{os.getuid()}/{name}"
    if launchctl("print", target).returncode == 0 and (out := launchctl("bootout", target)).returncode:
        print(f"drive_supervisor: launchctl bootout {target} failed: {(out.stderr or out.stdout).strip()}", file=sys.stderr)
        return 1
    plist.unlink(missing_ok=True)
    print(f"drive_supervisor: {name} booted out and {plist} removed")
    return 0


def registry_lines(store: Store, now: datetime) -> list[str]:
    live = {row["lane"] for row in lanes.liveness(store, now) if row["live"]}
    return [
        f"{lane.name} {lane.state} {'live' if lane.name in live else 'silent'} heard {lanes.pacific(lane.heard) if lane.heard else 'never'} since {lane.since:%H:%MZ} handoff {lane.handoff or '-'} prs {' '.join(f'#{n}' for n in lane.prs) or '-'}"
        + (f" after {lane.predecessor}: {lane.reason}" if lane.predecessor else "")
        + (f" retired by {lane.retired_by}" if lane.retired_by else "")
        for lane in store.all_lanes()
    ]


def cmd_lanes(config: lr_config.Config, path: Path, as_json: bool) -> int:
    store, now = Store.open(path.parent / "lr.sqlite"), clock()
    print("\n".join(json.dumps(row) for row in lanes.liveness(store, now)) if as_json else "\n".join(registry_lines(store, now)))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="drive_supervisor.py")
    parser.add_argument("verb", choices=("run", "install", "uninstall", "lanes"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = args.config.resolve()
    if args.verb == "lanes":
        return cmd_lanes(lr_config.load(path), path, args.json)
    verbs = {"run": cmd_run, "install": cmd_install, "uninstall": cmd_uninstall}
    return verbs[args.verb](lr_config.load(path), path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
