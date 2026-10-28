from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, script
from .lane_rotation import DriveActive

FIXTURES = Path(__file__).parent / "tests" / "fixtures"
DRIVE_ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6", "CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config")}


def config_dir() -> Path:
    return Path(reqenv.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def entries() -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((config_dir() / "long-running" / "drives").glob("*.json"))]


def entry_of(evt: BaseHookEvent) -> dict | None:
    named = reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")
    return next((entry for entry in entries() if entry["drive"] == named or (not named and evt.session_id in entry["sessions"])), None)


def watch_prompts(evt: BaseHookEvent, entry: dict) -> None:
    argv = [sys.executable, str(script("prompt_watch.py")), "start", "--drive", entry["drive"]]
    if evt.session_id in entry["sessions"] and (handle := reqenv.getenv("ORCA_TERMINAL_HANDLE")):
        argv += ["--root-terminal", handle]
    subprocess.Popen(argv, env=dict(reqenv.env_map()), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


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
def watch_prompts_on_session_start(evt: BaseHookEvent) -> HookResult | None:
    if entry := entry_of(evt):
        watch_prompts(evt, entry)
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
def watch_prompts_on_drive_start(evt: BaseHookEvent) -> HookResult | None:
    if starts_drive(evt) and (entry := entry_of(evt)):
        watch_prompts(evt, entry)
    return None
