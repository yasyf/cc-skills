from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .compaction_handoff import CompactionState
from .lane_landing import lane_name
from .lane_rotation import DriveActive

ACTIVE = [CompactionState(active=True)]
LANE = "atooling-misc-3-0123456789abcdef"
SPAWN = {"name": "hook-fix", "description": "fix a hook", "prompt": "Fix the hook."}
REFUSAL = (
    "A lane never spawns a sub-worker with Agent: a teammate cannot background the call, so lane `{lane}` would wait inside it. "
    "Write the brief to a file and post one ask to root, "
    "`cci post --lane {lane} --kind ask --to root --text 'spawn <name> brief=<absolute path> model=<opus or sonnet>'`, then keep working; "
    "root spawns it in the background within one turn. Codex sub-work goes through `codex-ask --dispatch`."
)


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent"), DriveActive()],
    tests={
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE, state=ACTIVE): Block(pattern=r"^A lane never spawns a sub-worker with Agent: .* lane `tooling-misc-3` would wait inside it\..*--lane tooling-misc-3 --kind ask --to root"),
        Input(tool="Agent", tool_input=SPAWN, env={"CLAUDE_LONG_RUNNING_LANE": "orca-lane-2"}, state=ACTIVE): Block(pattern=r"lane `orca-lane-2`"),
        Input(tool="Agent", tool_input=SPAWN, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id="a0123456789abcdef", state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE): Allow(),
    },
)
def lanes_never_spawn(evt: BaseHookEvent) -> HookResult | None:
    if not (lane := lane_name(evt)):
        return None
    return evt.block(REFUSAL.format(lane=lane))
