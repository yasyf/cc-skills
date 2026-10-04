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


ROOT_ACTION = "ROOT-ACTION"


def root_action_key(lane: str) -> str:
    return f"{ROOT_ACTION} `{lane}`"


def stopped_lane(evt: BaseHookEvent) -> str | None:
    if evt.tool_name != "TaskStop" or not (task_id := evt.input.raw.get("task_id")):
        return None
    return task_id.split("@", 1)[0]


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
            tool="TaskStop",
            tool_input={"task_id": "desk@session-root"},
            state=[NudgeState(pending=["ROOT-ACTION `desk`: Rotate it by hand.", "ROOT-ACTION `desk-2`: Rotate it by hand."])],
        ): Warn(pattern=r"^ROOT-ACTION `desk-2`: Rotate it by hand\.$"),
        Input(
            tool="TaskStop",
            tool_input={"task_id": "desk@session-root"},
            state=[NudgeState(pending=["ROOT-ACTION `desk`: Rotate it by hand."])],
        ): Allow(),
        Input(
            tool="Bash", tool_input={"command": "ls"}, agent_id="a1b2c3", state=[NudgeState(pending=["only"])]
        ): Allow(),
    },
)
def deliver_nudge(evt: BaseHookEvent) -> HookResult | None:
    with NudgeState.mutate(evt) as state:
        pending, state.pending = state.pending, []
    if lane := stopped_lane(evt):
        pending = [line for line in pending if not line.startswith(root_action_key(lane))]
    return evt.context("\n\n".join(pending)) if pending else None
