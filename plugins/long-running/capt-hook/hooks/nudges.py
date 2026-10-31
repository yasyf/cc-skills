from __future__ import annotations

import time
from collections.abc import Iterable

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)

from . import session_tree

NAG_SECONDS = 60 * 60


def stopped_lane(evt: BaseHookEvent) -> str | None:
    if evt.tool_name != "TaskStop" or not (task_id := evt.input.raw.get("task_id")):
        return None
    if "@" in task_id:
        return task_id.split("@", 1)[0]
    return next((agent.name for agent in session_tree.subagents(evt) if task_id in (agent.agent_id, agent.name)), None)


@workflow_state("long_running_nudges")
class NudgeState(WorkflowState):
    pending: list[str] = []


@workflow_state("long_running_nag_clock")
class NagClock(WorkflowState):
    said: dict[str, float] = {}


def once_per_hour(evt: BaseHookEvent, keys: Iterable[str]) -> list[str]:
    now = time.time()
    with NagClock.mutate(evt) as clock:
        due = [key for key in dict.fromkeys(keys) if now - clock.said.get(key, 0.0) >= NAG_SECONDS]
        clock.said |= dict.fromkeys(due, now)
    return due


def queue_nudge(evt: BaseHookEvent, text: str) -> None:
    with NudgeState.mutate(evt) as state:
        state.pending.append(text)


@on(
    Event.UserPromptSubmit | Event.PostToolUse,
    skip_if=[FromSubagent()],
    tests={
        Input(prompt="continue", state=[NudgeState(pending=["first", "second"])]): Warn(pattern=r"^first\n\nsecond$"),
        Input(tool="Bash", tool_input={"command": "ls"}, state=[NudgeState(pending=["only"])]): Warn(
            pattern=r"^only$"
        ),
        Input(prompt="continue", state=[NudgeState()]): Allow(),
        Input(
            tool="Bash", tool_input={"command": "ls"}, agent_id="a1b2c3", state=[NudgeState(pending=["only"])]
        ): Allow(),
    },
)
def deliver_nudge(evt: BaseHookEvent) -> HookResult | None:
    with NudgeState.mutate(evt) as state:
        pending, state.pending = state.pending, []
    return evt.context("\n\n".join(pending)) if pending else None
