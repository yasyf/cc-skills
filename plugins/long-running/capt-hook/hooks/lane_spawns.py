from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .compaction_handoff import CompactionState
from .lane_landing import lane_name
from .lane_rotation import DriveActive

ACTIVE = [CompactionState(active=True)]
LANE = "atooling-misc-3-0123456789abcdef"
SPAWN = {"name": "hook-fix", "description": "fix a hook", "prompt": "Fix the hook."}
REFUSAL = (
    "A lane never spawns a sub-worker with Agent, since a teammate cannot background the call and `{lane}` would wait inside it. "
    "Write the brief to a file, post `cci post --lane {lane} --kind ask --to root --text 'spawn <name> brief=<path> model=<opus|sonnet>'`, and keep working."
)


@on(  # capt-hook: no-escape root spawns every sub-worker, root #235843
    Event.PreToolUse,
    only_if=[Tool("Agent"), DriveActive()],
    tests={
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE, state=ACTIVE): Block(pattern=r"^A lane never spawns a sub-worker with Agent, since .* `tooling-misc-3` would wait inside it\..*--lane tooling-misc-3 --kind ask --to root"),
        Input(tool="Agent", tool_input=SPAWN, env={"CLAUDE_LONG_RUNNING_LANE": "orca-lane-2"}, state=ACTIVE): Block(pattern=r"`orca-lane-2` would wait"),
        Input(tool="Agent", tool_input=SPAWN, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id="a0123456789abcdef", state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE): Allow(),
    },
)
def lanes_never_spawn(evt: BaseHookEvent) -> HookResult | None:
    if not (lane := lane_name(evt)):
        return None
    return evt.block(REFUSAL.format(lane=lane))
