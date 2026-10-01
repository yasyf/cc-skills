from __future__ import annotations

import json

from captain_hook import (
    BaseHookEvent,
    Event,
    HookResult,
    Tool,
    on,
)

from . import common


@on(Event.SubagentStart, skip_planning_agents=False)
def agent_start(evt: BaseHookEvent) -> None:
    common.call_bin(evt, "agent-start")


@on(Event.PreToolUse)
def agent_inject(evt: BaseHookEvent) -> HookResult | None:
    if not common.directive_pending(evt):
        return None
    if not (out := common.call_bin(evt, "agent-inject", timeout=5)):
        return None
    return evt.context(json.loads(out)["hookSpecificOutput"]["additionalContext"])


@on(Event.SubagentStop, skip_planning_agents=False)
def agent_stop(evt: BaseHookEvent) -> HookResult | None:
    if not common.subject_in_scope(evt):
        return None
    if not (out := common.call_bin(evt, "agent-stop", timeout=15)):
        return None
    return evt.block(json.loads(out)["reason"])


@on(Event.PostToolUse, only_if=[Tool("Task", "Agent")], async_=True)
def agent_report(evt: BaseHookEvent) -> None:
    if common.subject_in_scope(evt):
        common.call_bin(evt, "agent-report")
