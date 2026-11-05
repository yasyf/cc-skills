from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on

from .compaction_handoff import Bound, CompactionState
from .lane_landing import lane_name
from .lane_rotation import DriveActive

ACTIVE = [CompactionState(active=True, coordinator=True)]
LANE = "atooling-misc-3-0123456789abcdef"
SPAWN = {"name": "hook-fix", "description": "fix a hook", "prompt": "Fix the hook."}
SHIP = "ship-pr"
ADMITTED = frozenset({SHIP, "long-running:orca-doctor", "orca:orca-doctor"})
REFUSAL = (
    "A lane never spawns a sub-worker with Agent, since `{lane}` would wait inside it. "
    "Post `cci post --lane {lane} --kind ask --to root --text 'spawn <name> brief=<path> model=<model>'` and keep working, or for a hook blocking you run `lr launch lane hook-fix-<hook> opus xhigh <brief>`."
)


@on(  # capt-hook: no-escape the coordinator spawns every sub-worker, coordinator #235843; the submit-pr skill's ship-pr agent is one ship call, coordinator #336914; orca-doctor only reads a stuck lane and reports
    Event.PreToolUse,
    only_if=[Bound(), Tool("Agent"), DriveActive()],
    tests={
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE, state=ACTIVE): Block(pattern=r"^A lane never spawns a sub-worker with Agent, since `tooling-misc-3` would wait inside it\..*--lane tooling-misc-3 --kind ask --to root.*`lr launch lane hook-fix-<hook> opus xhigh <brief>`"),
        Input(tool="Agent", tool_input=SPAWN, env={"CLAUDE_LONG_RUNNING_LANE": "orca-lane-2"}, state=ACTIVE): Block(pattern=r"`orca-lane-2` would wait"),
        Input(tool="Agent", tool_input=SPAWN, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id="a0123456789abcdef", state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input=SPAWN, agent_id=LANE): Allow(),
        Input(tool="Agent", tool_input={**SPAWN, "subagent_type": SHIP}, agent_id=LANE, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input={**SPAWN, "subagent_type": "long-running:orca-doctor"}, agent_id=LANE, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input={**SPAWN, "subagent_type": "orca:orca-doctor"}, env={"CLAUDE_LONG_RUNNING_LANE": "orca-lane-2"}, state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input={**SPAWN, "subagent_type": "long-running:lane"}, agent_id=LANE, state=ACTIVE): Block(pattern=r"^A lane never spawns a sub-worker"),
    },
)
def lanes_never_spawn(evt: BaseHookEvent) -> HookResult | None:
    if not (lane := lane_name(evt)) or evt.input.raw.get("subagent_type") in ADMITTED:
        return None
    return evt.block(REFUSAL.format(lane=lane))
