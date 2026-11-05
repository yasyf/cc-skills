from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound, CompactionState
from .lane_landing import lane_name
from .lane_rotation import DriveActive

ACTIVE = [CompactionState(active=True, coordinator=True)]
LANE = "atooling-misc-3-0123456789abcdef"
SPAWN = {"name": "hook-fix", "description": "fix a hook", "prompt": "Fix the hook."}
BACKGROUND = {**SPAWN, "run_in_background": True}
WARNING = (
    "Warning, nothing was blocked: `{lane}` waits inside a foreground sub-worker. "
    "Prefer `lr launch lane <name> opus high <brief>` or an Agent with `run_in_background`."
)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Agent"), DriveActive()],
    tests={
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE, state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: `tooling-misc-3` waits inside a foreground sub-worker\..*`lr launch lane <name> opus high <brief>`.*`run_in_background`"),
        Input(tool="Agent", tool_input=SPAWN, env={"CLAUDE_LONG_RUNNING_LANE": "orca-lane-2"}, state=ACTIVE): Warn(pattern=r"`orca-lane-2` waits"),
        Input(tool="Agent", tool_input=SPAWN, env={"CLAUDE_LONG_RUNNING_LANE": "root"}, state=ACTIVE): Warn(pattern=r"`root` waits"),
        Input(tool="Agent", tool_input=BACKGROUND, env={"CLAUDE_LONG_RUNNING_LANE": "root"}, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=BACKGROUND, agent_id=LANE, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id="a0123456789abcdef", state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE): Allow(),
    },
)
def lanes_never_spawn(evt: BaseHookEvent) -> HookResult | None:
    if not (lane := lane_name(evt)) or evt.input.raw.get("run_in_background"):
        return None
    return evt.warn(WARNING.format(lane=lane))
