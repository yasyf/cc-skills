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
    out = common.call_bin(evt, "agent-inject", timeout=5)
    if not out:
        return None
    try:
        envelope = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(envelope, dict):
        return None
    specific = envelope.get("hookSpecificOutput")
    if not isinstance(specific, dict) or specific.get("hookEventName") != "PreToolUse":
        return None
    text = specific.get("additionalContext")
    if not isinstance(text, str) or not text:
        return None
    return evt.context(text)


@on(Event.SubagentStop, skip_planning_agents=False)
def agent_stop(evt: BaseHookEvent) -> HookResult | None:
    out = common.call_bin(evt, "agent-stop", timeout=15)
    if not out:
        return None
    try:
        decision = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decision, dict):
        return None
    if decision.get("decision") != "block" or not isinstance(decision.get("reason"), str):
        return None
    return evt.block(decision["reason"])


@on(Event.PostToolUse, only_if=[Tool("Task", "Agent")], async_=True)
def agent_report(evt: BaseHookEvent) -> None:
    common.call_bin(evt, "agent-report")
