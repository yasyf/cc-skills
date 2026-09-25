from __future__ import annotations

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


@workflow_state("long_running_nudges")
class NudgeState(WorkflowState):
    pending: list[str] = []


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
