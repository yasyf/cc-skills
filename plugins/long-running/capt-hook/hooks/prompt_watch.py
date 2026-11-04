from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, on
from captain_hook.util import reqenv

from .compaction_handoff import LR, CompactionState, Coordinator, coordinator_tests, drive_entry
from .lane_rotation import DriveActive

FIXTURES = Path(__file__).parent / "tests" / "fixtures"
DRIVE_ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6", "CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config")}


@on(
    Event.SessionStart,
    only_if=[Coordinator(), DriveActive()],
    skip_if=[FromSubagent()],
    tests=coordinator_tests({
        Input(
            source="resume",
            session_id="900424b6-0000",
            state=[CompactionState(active=True, coordinator=True)],
        ): Allow(),
        Input(source="startup"): Allow(),
    }),
)
def bind_prompt_watch_to_this_terminal(evt: BaseHookEvent) -> HookResult | None:
    entry = drive_entry(evt)
    handle = reqenv.getenv("ORCA_TERMINAL_HANDLE")
    if entry and handle and evt.session_id in entry["sessions"] and handle != entry.get("root_terminal"):
        subprocess.Popen([str(LR), "watch", "prompts", "bind", "--drive", entry["drive"], "--root-terminal", handle], env=dict(reqenv.env_map()), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return None
