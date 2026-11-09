from __future__ import annotations

import subprocess
import time

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, Input, WorkflowState, on, workflow_state

from .compaction_handoff import Communicator, communicator_tests, CompactionState
from .owner_answers import read

SUBAGENT = "Waiting for a subagent…"
CI = "Waiting for CI…"
READING = "Reading the thread…"
RECORD = "Reading a record…"
WRITING = "Writing the reply…"
WORKING = "lr owner working"
ACTIVE = [CompactionState(active=True, communicator=True)]
REPEAT_SECONDS = 20.0


BASH_STATES = (
    (CI, (("gh", "pr", "checks"), ("ccx", "vcs", "pr"), ("gh", "run", "watch"))),
    (READING, (("cc-slack", "thread"), ("cc-slack", "history"))),
    (RECORD, (("cci",), ("ccn",))),
    (WRITING, (("lr", "say"), ("lr", "reply"))),
)


@workflow_state("long_running_working_line")
class WorkingLine(WorkflowState):
    said: str = ""
    at: float = 0.0


def state_of(evt: BaseHookEvent) -> str | None:
    tool = evt.tool_name or ""
    if tool in ("Agent", "TaskOutput") or tool.endswith("__await"):
        return SUBAGENT
    if tool.endswith(("__reply", "__say")):
        return WRITING
    if tool.endswith(("__slack_thread", "__slack_history")):
        return READING
    if tool.startswith("mcp__plugin_cc-inbox_cci__") or tool == "Read":
        return RECORD
    if tool != "Bash" or evt.command is None:
        return None
    for state, runs in BASH_STATES:
        if any(tuple(call.args[: len(words) - 1]) == tuple(words[1:]) for words in runs for call in evt.command.calls(words[0])):
            return state
    return None


@on(
    Event.PreToolUse,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    tests=communicator_tests({
        Input(tool="Agent", tool_input={"description": "read", "prompt": "find it"}, state=ACTIVE, commands={f"{WORKING} {SUBAGENT}": ""}): Allow(),
        Input(command="gh pr checks 12 --watch", state=ACTIVE, commands={f"{WORKING} {CI}": ""}): Allow(),
        Input(command="cci show 12", state=[*ACTIVE, WorkingLine(said=RECORD, at=time.time())]): Allow(),
        Input(command="cci show 12", state=[*ACTIVE, WorkingLine(said=RECORD)], commands={f"{WORKING} {RECORD}": ""}): Allow(),
        Input(command="ls", state=ACTIVE): Allow(),
        Input(tool="Agent", tool_input={"description": "read", "prompt": "find it"}, state=ACTIVE, commands={f"{WORKING} {SUBAGENT}": (1, "no drive")}): Allow(),
    }),
)
def working_line_names_the_state(evt: BaseHookEvent) -> None:
    if (state := state_of(evt)) is None:
        return
    now = time.time()
    with WorkingLine.mutate(evt) as line:
        if line.said == state and now - line.at < REPEAT_SECONDS:
            return
        line.said, line.at = state, now
    try:
        read([*WORKING.split(), state])
    except (OSError, subprocess.SubprocessError):
        return
