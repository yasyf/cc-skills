#!/usr/bin/env python3
"""`drive_supervisor.py`: the process that runs a drive's supervisor, the v2 loop of `lr/loop.py` over the movers `lr.json` names, kept alive by launchd.

    drive_supervisor.py run --config LR_JSON
    drive_supervisor.py install --config LR_JSON
    drive_supervisor.py uninstall --config LR_JSON
    drive_supervisor.py lanes --config LR_JSON

STDLIB ONLY. `lr.json` sits in the drive's state directory beside the store `lr.sqlite`, and the
lanes mover writes successor briefs and launch logs under `lanes/` there. `run` takes the drive's
lease and ticks until a newer generation holds it. Liveness is one `cci who` read and one Orca
worker list per tick; a successor launches through `desk-launch.py lane`, detached, so a launch
never holds the tick. `install` writes `~/Library/LaunchAgents/com.long-running.<drive>.supervisor.plist`
and bootstraps that one label, booting out only its own earlier copy; launchd restarts the process
whenever it exits. `lanes` prints the registry, one line per lane.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
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


def launcher(drive: str, briefs: Path):
    def launch(name: str, model: str, effort: str, brief: Path) -> None:
        with (briefs / f"{name}.launch.log").open("ab") as log:
            subprocess.Popen([sys.executable, str(DESK_LAUNCH), "lane", name, model, effort, str(brief), "--drive", drive, "--lane", lanes.SELF], stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)

    return launch


def clock() -> datetime:
    return datetime.now(UTC)


def lanes_deps(config: lr_config.Config, state: Path) -> lanes.Deps:
    receipts = RECEIPTS / config.orca_run

    def who(names: set[str] | None) -> list[dict]:
        rows = [json.loads(line) for line in run(["cci", "who", "--drive", config.drive, "--all", "--json"]).splitlines() if line.strip()]
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
        launch=launcher(config.drive, briefs),
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


def registry_lines(store: Store) -> list[str]:
    return [
        f"{lane.name} {lane.state} since {lane.since:%H:%MZ} handoff {lane.handoff or '-'} prs {' '.join(f'#{n}' for n in lane.prs) or '-'}"
        + (f" after {lane.predecessor}: {lane.reason}" if lane.predecessor else "")
        + (f" retired by {lane.retired_by}" if lane.retired_by else "")
        for lane in store.all_lanes()
    ]


def cmd_lanes(config: lr_config.Config, path: Path) -> int:
    print("\n".join(registry_lines(Store.open(path.parent / "lr.sqlite"))))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="drive_supervisor.py")
    parser.add_argument("verb", choices=("run", "install", "uninstall", "lanes"))
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    path = args.config.resolve()
    verbs = {"run": cmd_run, "install": cmd_install, "uninstall": cmd_uninstall, "lanes": cmd_lanes}
    return verbs[args.verb](lr_config.load(path), path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
