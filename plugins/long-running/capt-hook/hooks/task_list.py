from __future__ import annotations

import re
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from cc_transcript import AttachmentEvent, UserEvent
from cc_transcript.models import TranscriptEvent

from captain_hook import (
    Allow,
    Block,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    TaskCall,
    TaskUpdateCall,
    Tool,
    WorkflowState,
    on,
    workflow_state,
)
from captain_hook.tasks import Task, Tasks

from . import session_tree
from .compaction_handoff import TURN_WINDOW, CompactionState
from .lane_rotation import DriveActive, live_lanes
from .nudges import queue_nudge
from .session_tree import IDLE_NOTIFICATION, TEAMMATE_MESSAGE, covered, lane_of, mentions

ASK_CALLS = 3
ASK_LINE = "Every owner ask gets a task. Run `TaskCreate` for it."
RECONCILE_TURNS = 20
LISTED_LANES = 5
DONE_COOLDOWN_SECONDS = 30 * 60
IDLE_SECONDS = 30 * 60
HELPER_ROLES = frozenset({"helper", "reader", "watch", "export", "evidence", "handoff", "comms", "triage"})
DRIFT_LINE = "The task list has drifted from the running lanes. Run `TaskUpdate` to complete, re-own, or delete the stale tasks."
TASK_TOOLS = frozenset({"TaskCreate", "TaskUpdate"})
NAME_FLAGS = ("--display-name", "--name", "--task-title")
DESK = re.compile(r"(?:^|-)desk(?:-|$)")
REF = re.compile(r"#\d+|\b[A-Z]\d+\b")
TASK_REF = re.compile(r"\btask #(\d+)", re.IGNORECASE)
STATUS_QUESTION = re.compile(r"\bstatus\b[^?]*\?\s*$", re.IGNORECASE)
DONE = re.compile(r"(?<![\w-])(?:worker_done|READY|GREEN)\b|(?i:\b(?:landed|merged|done|shipped|finished|completed?)\b)")
ASK = re.compile(
    r"https?://|\b(?:fix|make sure|do an?|(?<!status )update|sweep|add|remove|ship|land|merge|deploy|release|"
    r"investigate|look into|figure out|find out|build|change|rename|delete|retire|write|stop|start|"
    r"need to|needs to|have to|should|can you|please)\b",
    re.IGNORECASE,
)
LANE_TASK = {"id": "1", "subject": "Land the stack", "status": "in_progress", "owner": "stack-lander"}
SYSTEM_PREFIXES = ("<", "/", "This session is being continued", "[Request interrupted")


@workflow_state("long_running_task_asks")
class AskState(WorkflowState):
    pending: bool = False
    calls: int = 0
    cursor: str | None = None


@workflow_state("long_running_task_unowned")
class UnownedState(WorkflowState):
    lanes: list[str] = []


@workflow_state("long_running_task_done")
class DoneState(WorkflowState):
    nudged: dict[str, float] = {}
    cursor: str | None = None


@workflow_state("long_running_task_drift")
class DriftState(WorkflowState):
    turns: int = 0
    flagged: list[str] = []


@workflow_state("long_running_task_untracked")
class UntrackedState(WorkflowState):
    turns: int = 0
    flagged: list[str] = []


def spawned_names(evt: BaseHookEvent) -> set[str]:
    return {agent.name for agent in session_tree.subagents(evt) if agent.name}


def flag_value(args: tuple[str, ...], names: tuple[str, ...]) -> str | None:
    for name in names:
        for index, arg in enumerate(args):
            if arg == name and index + 1 < len(args):
                return args[index + 1]
            if arg.startswith(f"{name}="):
                return arg.removeprefix(f"{name}=")
    return None


def spawned_lane(evt: BaseHookEvent) -> str | None:
    if call := evt.as_input(TaskCall):
        return call.agent_name
    for call in evt.command.calls():
        if Path(call.name).name == "orca-launch.sh" and call.args:
            return call.args[0]
        if call.name == "orca" and call.args[:2] == ("orchestration", "worker-start"):
            return flag_value(call.args, NAME_FLAGS)
    return None


def prompts(events: Iterable[TranscriptEvent]) -> Iterator[str]:
    for event in events:
        match event:
            case UserEvent() if not event.meta.is_meta and not event.meta.is_compact_summary and event.text:
                yield event.text
            case AttachmentEvent(attachment_type="queued_command") if event.detail.prompt:
                yield event.detail.prompt


def is_ask(text: str) -> bool:
    stripped = text.strip()
    return (
        not stripped.startswith(SYSTEM_PREFIXES)
        and not TEAMMATE_MESSAGE.search(stripped)
        and not STATUS_QUESTION.search(stripped)
        and ASK.search(stripped) is not None
    )


def done_reports(text: str) -> list[tuple[str | None, str]]:
    if text.lstrip().startswith("<task-notification>"):
        return [(None, text)] if DONE.search(text) else []
    return [
        (None if DESK.search(sender) else sender, body)
        for sender, body in TEAMMATE_MESSAGE.findall(text)
        if not body.startswith(IDLE_NOTIFICATION) and DONE.search(body)
    ]


def reported(tasks: tuple[Task, ...], lane: str, body: str) -> list[Task]:
    owned = [task for task in tasks if lane_of(task) == lane]
    if len(owned) == 1:
        return owned
    if named := set(TASK_REF.findall(body)):
        return [task for task in owned if task.id in named]
    refs = set(REF.findall(body))
    return [task for task in owned if refs & set(REF.findall(task.subject))]


def finished(evt: BaseHookEvent, state: DoneState, text: str, now: float) -> list[str]:
    if not (reports := done_reports(text)):
        return []
    tasks = evt.tasks.in_progress
    lanes = {lane for task in tasks if (lane := lane_of(task)) and not DESK.search(lane)}
    lines = []
    for sender, body in reports:
        for lane in [sender] if sender else [lane for lane in lanes if mentions(body, lane)]:
            for task in reported(tasks, lane, body):
                if now - state.nudged.get(task.id, 0.0) >= DONE_COOLDOWN_SECONDS:
                    state.nudged[task.id] = now
                    lines.append(
                        f"Lane `{lane}` reported its task done. Consume its deliverable, then run `TaskUpdate`."
                    )
    return lines


def drifted(evt: BaseHookEvent, tasks: Tasks, now: float) -> list[str]:
    lanes = {lane.name: lane for lane in live_lanes(evt)}
    known = spawned_names(evt)
    return sorted(
        task.id
        for task in tasks.in_progress
        if (lane := lane_of(task)) in known
        and (lane not in lanes or now - lanes[lane].turn.at.timestamp() > IDLE_SECONDS)
    )


def untracked(evt: BaseHookEvent, tasks: Tasks, now: float) -> list[str]:
    return [
        lane.name
        for lane in live_lanes(evt)
        if now - lane.turn.at.timestamp() <= IDLE_SECONDS and not covered(tasks, lane.name)
    ]


def untracked_line(names: list[str]) -> str:
    shown = ", ".join(f"`{name}`" for name in names[:LISTED_LANES])
    more = f" (+{len(names) - LISTED_LANES} more)" if len(names) > LISTED_LANES else ""
    return f"Busy lanes have no open task: {shown}{more}. Run `TaskCreate` with `owner=<lane>` for each."


def deliver(evt: BaseHookEvent, lines: list[str]) -> HookResult | None:
    if evt.event == Event.Stop:
        for line in lines:
            queue_nudge(evt, line)
        return None
    return evt.context("\n".join(lines)) if lines else None


def fresh_prompts(evt: BaseHookEvent, cursor: str | None) -> tuple[list[str], str | None]:
    events, cursor = session_tree.events_after(evt.ctx.t.events, cursor)
    return list(prompts(events)), cursor


@on(
    Event.PostToolUse | Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    transcript_events=TURN_WINDOW,
    tests={
        Input(tool="Bash", tool_input={"command": "ls"}): Allow(),
        Input(tool="TaskCreate", tool_input={"subject": "x", "description": "y"}): Allow(),
    },
)
def require_task_for_owner_ask(evt: BaseHookEvent) -> HookResult | None:
    with AskState.mutate(evt) as state:
        texts, state.cursor = fresh_prompts(evt, state.cursor)
        if any(is_ask(text) for text in texts):
            state.pending, state.calls = True, 0
        if evt.event == Event.Stop:
            asked, state.pending = state.pending, False
            return deliver(evt, [ASK_LINE] * asked)
        if evt.tool_name in TASK_TOOLS and (
            not (update := evt.as_input(TaskUpdateCall)) or update.status not in ("completed", "deleted")
        ):
            state.pending = False
        if state.pending:
            state.calls += 1
            if state.calls >= ASK_CALLS:
                state.pending = False
                return deliver(evt, [ASK_LINE])
    return None


@on(
    Event.PostToolUse | Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(tool="Agent", tool_input={"name": "desk", "prompt": "go"}): Allow(),
        Input(tool="Bash", tool_input={"command": "ls"}): Allow(),
    },
)
def require_task_for_dispatched_lane(evt: BaseHookEvent) -> HookResult | None:
    with UnownedState.mutate(evt) as state:
        if evt.event == Event.Stop:
            tasks = evt.tasks
            lines = [f"Lane `{name}` has no task. Run `TaskCreate` with `owner={name}`." for name in state.lanes if not covered(tasks, name)]
            state.lanes = []
            return deliver(evt, lines)
        if evt.tool_name in TASK_TOOLS:
            tasks = evt.tasks
            state.lanes = [name for name in state.lanes if not covered(tasks, name)]
        elif (name := spawned_lane(evt)) and evt.annotations.get("role") not in HELPER_ROLES:
            state.lanes.append(name)
    return None


@on(
    Event.PostToolUse | Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    transcript_events=TURN_WINDOW,
    tests={
        Input(tool="Bash", tool_input={"command": "ls"}): Allow(),
        Input(tool="TaskUpdate", tool_input={"taskId": "1", "status": "completed"}): Allow(),
    },
)
def flag_lane_done_reports(evt: BaseHookEvent) -> HookResult | None:
    now = time.time()
    with DoneState.mutate(evt) as state:
        texts, state.cursor = fresh_prompts(evt, state.cursor)
        lines = [line for text in texts for line in finished(evt, state, text, now)]
    return deliver(evt, lines)


@on(
    Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={Input(tool="Bash", tool_input={"command": "ls"}): Allow()},
)
def flag_task_list_drift(evt: BaseHookEvent) -> HookResult | None:
    with DriftState.mutate(evt) as state:
        state.turns += 1
        if state.turns < RECONCILE_TURNS:
            return None
        state.turns = 0
    stale = drifted(evt, evt.tasks, time.time())
    with DriftState.mutate(evt) as state:
        if stale and stale != state.flagged:
            queue_nudge(evt, DRIFT_LINE)
        state.flagged = stale
    return None


@on(
    Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={Input(tool="Bash", tool_input={"command": "ls"}): Allow()},
)
def flag_untracked_lanes(evt: BaseHookEvent) -> HookResult | None:
    with UntrackedState.mutate(evt) as state:
        state.turns += 1
        if state.turns < RECONCILE_TURNS:
            return None
        state.turns = 0
    names = untracked(evt, evt.tasks, time.time())
    with UntrackedState.mutate(evt) as state:
        if names and sorted(names) != state.flagged:
            queue_nudge(evt, untracked_line(names))
        state.flagged = sorted(names)
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("TaskUpdate"), FromSubagent(), DriveActive()],
    tests={
        Input(
            tool="TaskUpdate",
            agent_id="a1b2c3",
            tool_input={"taskId": "1", "status": "completed"},
            tasks=[LANE_TASK],
            state=[CompactionState(active=True)],
        ): Block(pattern="leave the task open"),
        Input(
            tool="TaskUpdate",
            agent_id="a1b2c3",
            tool_input={"taskId": "1", "status": "in_progress"},
            tasks=[LANE_TASK],
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(
            tool="TaskUpdate",
            agent_id="a1b2c3",
            tool_input={"taskId": "2", "status": "completed"},
            tasks=[LANE_TASK],
            state=[CompactionState(active=True)],
        ): Allow(),
    },
)
def lanes_leave_root_tasks_open(evt: BaseHookEvent) -> HookResult | None:
    call = evt.as_input(TaskUpdateCall)
    if call.status != "completed":
        return None
    if (task := evt.tasks.get(call.task_id)) and (lane := lane_of(task)):
        return evt.block(
            f"Task #{task.id} is the root's record of lane {lane}; the root completes it once it has consumed "
            "your deliverable. SendMessage team-lead the deliverable instead and leave the task open."
        )
    return None
