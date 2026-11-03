from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, Input, Or, Tool, hook

from .compaction_handoff import CompactionState, Communicator, Coordinator

COMMUNICATING = [CompactionState(active=True)]
COORDINATING = [CompactionState(active=True, coordinator=True)]
ROLE = r"(?:landing-desk|progress-desk|drive-watchdog|fidelity-desk|fidelity-sweep|red-sweep|alerts-desk|board-refresher|meta(?:-desk)?|chores|signal)(?:-\d+)?"
NAMED = re.compile(ROLE)
OPENS_AS_DESK = re.compile(rf"(?i)^(?:#\s*)?(?:(?:the|lane)\s+)?`?{ROLE}(?![\w-])")
STANDING_LINE = re.compile(r"(?m)^Standing:")
WATCHDOG_BRIEF = "# The drive-watchdog lane\n\nWatch every lane against the plan."
STANDING_BRIEF = "# landing-shard: queue shard\n\nStanding: keep the landing queue moving\nRun forever."
READ = "Why did landing-desk-3 stop posting? Read its last cci records and report."


def spawn(state: list, name: str, prompt: str, **extra: object) -> Input:
    return Input(tool="Agent", tool_input={"name": name, "prompt": prompt, "subagent_type": "long-running:lane"}, state=state, **extra)


class SpawnsDesk:
    def check(self, evt: BaseHookEvent) -> bool:
        call = evt.input.raw
        prompt = call.get("prompt") or ""
        return bool(NAMED.fullmatch(call.get("name") or "") or OPENS_AS_DESK.match(prompt) or STANDING_LINE.search(prompt))


hook(  # capt-hook: no-escape a standing desk spawned with Agent dies with the window that spawned it, and lr launch is the one placement that survives it
    Event.PreToolUse,
    block=True,
    only_if=[Tool("Agent"), Or(Communicator(), Coordinator()), SpawnsDesk()],
    message="A standing desk is an lr-launched lane the coordinator owns, never an Agent subagent, which ends with this window. Start it with `lr desks start`, or `lr launch lane <name> <model> <effort> <brief>` on a Sprite.",
    tests={
        spawn(COMMUNICATING, "queue-shard", STANDING_BRIEF): Block(pattern=r"^A standing desk is an lr-launched lane .*`lr desks start`.*`lr launch lane "),
        spawn(COMMUNICATING, "landing-desk-4", "Run the landing desk."): Block(),
        spawn(COMMUNICATING, "meta-desk-1", "Run the meta lane."): Block(),
        spawn(COMMUNICATING, "watchdog", WATCHDOG_BRIEF): Block(),
        spawn(COORDINATING, "landing-desk-4", "Run the landing desk."): Block(),
        spawn(COORDINATING, "queue-shard", STANDING_BRIEF): Block(),
        Input(tool="Agent", tool_input={"name": "desk-reader", "prompt": READ, "subagent_type": "Explore"}, state=COMMUNICATING): Allow(),
        Input(tool="Agent", tool_input={"prompt": "Summarize the standing owner rules.\n\n## Standing owner rules\n- R1", "subagent_type": "general-purpose"}, state=COMMUNICATING): Allow(),
        spawn(COMMUNICATING, "landing-desk-fix", "Fix the landing desk's ledger read."): Allow(),
        spawn([], "landing-desk-4", STANDING_BRIEF): Allow(),
        spawn(COMMUNICATING, "landing-desk-4", STANDING_BRIEF, agent_id="a1b2c3"): Allow(),
    },
)
