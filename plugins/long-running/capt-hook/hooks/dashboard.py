from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState
from .lane_rotation import DriveActive

DASHBOARD = Path(__file__).parents[2] / "skills" / "long-running" / "scripts" / "lr-dashboard.py"
DRIVES = Path(".claude") / "long-running" / "drives"


def drive_of(evt: BaseHookEvent) -> str | None:
    if drive := reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE"):
        return drive
    for path in (Path.home() / DRIVES).glob("*.json"):
        entry = json.loads(path.read_text())
        if evt.session_id in entry["sessions"]:
            return entry["drive"]
    return None


def serve(drive: str) -> None:
    subprocess.Popen(
        [sys.executable, str(DASHBOARD), "start", "--drive", drive],
        env=dict(reqenv.env_map()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def starts_drive(evt: BaseHookEvent) -> bool:
    for call in evt.cmd.calls():
        argv = (Path(call.name).name, *call.args)
        if argv[0].startswith("python") and len(argv) > 1:
            argv = (Path(argv[1]).name, *argv[2:])
        if argv[:2] == ("drive.py", "start"):
            return True
    return False


@on(
    Event.SessionStart,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(
            source="resume",
            session_id="900424b6-0000",
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(source="startup"): Allow(),
    },
)
def serve_on_session_start(evt: BaseHookEvent) -> HookResult | None:
    if drive := drive_of(evt):
        serve(drive)
    return None


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            command="drive.py start --ledger 1a2b3c4d --orca-run run_1",
            output="drive 900424b6 on ledger 1a2b3c4d",
            session_id="900424b6-0000",
        ): Allow(),
        Input(command="drive.py list", output="900424b6 ledger=1a2b3c4d"): Allow(),
    },
)
def serve_on_drive_start(evt: BaseHookEvent) -> HookResult | None:
    if starts_drive(evt) and (drive := drive_of(evt)):
        serve(drive)
    return None
