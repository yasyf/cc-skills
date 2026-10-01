from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from pathlib import Path

from captain_hook import (
    Allow,
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

from .compaction_handoff import CompactionState
from .lane_rotation import IDLE_NOTIFICATION, TEAMMATE_MESSAGE, entries_after, live_lanes
from .nudges import queue_nudge

ASK_CALLS = 3
RECONCILE_TURNS = 20
DONE_COOLDOWN_SECONDS = 30 * 60
IDLE_SECONDS = 30 * 60
EXCERPT = 80
ROOT_NAMES = frozenset({"team-lead", "main"})
TASK_TOOLS = frozenset({"TaskCreate", "TaskUpdate"})
NAME_FLAGS = ("--display-name", "--name", "--task-title")
LANE_IN_SUBJECT = re.compile(r"\blane `?([\w.-]+)")
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
SYSTEM_PREFIXES = ("<", "/", "This session is being continued", "[Request interrupted")


@workflow_state("long_running_task_list")
class TaskListState(WorkflowState):
    unowned: list[str] = []
    asks: list[str] = []
    ask_calls: int = 0
    nudged: dict[str, float] = {}
    turns: int = 0
    scanned: int | None = None


def spawned_names(evt: BaseHookEvent) -> set[str]:
    metas = (evt.transcript_path.with_suffix("") / "subagents").glob("agent-*.meta.json")
    return {name for meta in metas if (name := json.loads(meta.read_text()).get("name"))}


def lane_of(task: Task) -> str | None:
    if task.owner and task.owner not in ROOT_NAMES:
        return task.owner
    return match[1] if (match := LANE_IN_SUBJECT.search(task.subject)) else None


def mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def covered(tasks: Tasks, name: str) -> bool:
    return any(lane_of(task) == name or mentions(task.subject, name) for task in tasks.open)


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


def prompts(entries: list[dict]) -> Iterator[str]:
    for entry in entries:
        if entry.get("type") == "user" and not entry.get("isMeta") and not entry.get("isCompactSummary"):
            content = entry["message"]["content"]
        elif entry.get("type") == "attachment" and entry["attachment"].get("type") == "queued_command":
            content = entry["attachment"]["prompt"]
        else:
            continue
        if isinstance(content, list):
            content = "\n".join(block["text"] for block in content if block.get("type") == "text")
        if content:
            yield content


def excerpt(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= EXCERPT else line[: EXCERPT - 1] + "…"


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


def finished(evt: BaseHookEvent, state: TaskListState, text: str, now: float) -> list[str]:
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
                        f"task #{task.id} ({lane}) may be complete: TaskUpdate it once you have consumed the "
                        "deliverable (PR routed, ruling recorded), never on the lane's word alone"
                    )
    return lines


def ask_line(asks: list[str]) -> str:
    quoted = "; ".join(f'"{ask}"' for ask in asks)
    return f"owner ask has no task: TaskCreate one per ask this turn, owner = the lane you dispatch — {quoted}"


def spawn_line(name: str) -> str:
    return f"lane {name} has no task: TaskCreate one now with owner={name}"


def reconcile_line(evt: BaseHookEvent, tasks: Tasks, now: float) -> str | None:
    lanes = {lane.name: lane for lane in live_lanes(evt)}
    known = spawned_names(evt)
    stale = [
        f"#{task.id} ({lane})"
        for task in tasks.in_progress
        if (lane := lane_of(task)) in known
        and (lane not in lanes or now - lanes[lane].turn.at.timestamp() > IDLE_SECONDS)
    ]
    untracked = sorted(name for name in lanes if not covered(tasks, name))
    parts = []
    if stale:
        parts.append(f"in_progress with no working lane: {', '.join(stale)}")
    if untracked:
        parts.append(f"running lanes with no open task: {', '.join(untracked)}")
    if not parts:
        return None
    return f"task list drift — {'; '.join(parts)}. Complete what you consumed, re-own or delete the rest."


@on(
    Event.PostToolUse | Event.Stop,
    skip_if=[FromSubagent()],
    tests={
        Input(tool="Agent", tool_input={"name": "desk", "prompt": "go"}): Allow(),
        Input(tool="TaskCreate", tool_input={"subject": "x", "description": "y"}): Allow(),
    },
)
def track_task_list(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active:
        return None
    now = time.time()
    lines = []
    with TaskListState.mutate(evt) as state:
        if state.scanned is None:
            state.scanned = evt.transcript_path.stat().st_size
        entries, state.scanned = entries_after(evt.transcript_path, state.scanned)
        for text in prompts(entries):
            lines += finished(evt, state, text, now)
            if is_ask(text):
                state.asks.append(excerpt(text))
                state.ask_calls = 0
        if evt.event == Event.Stop:
            tasks = evt.tasks
            lines += [spawn_line(name) for name in state.unowned if not covered(tasks, name)]
            state.unowned = []
            if state.asks:
                lines.append(ask_line(state.asks))
                state.asks = []
            state.turns += 1
            if state.turns >= RECONCILE_TURNS:
                state.turns = 0
                if line := reconcile_line(evt, tasks, now):
                    lines.append(line)
            for line in lines:
                queue_nudge(evt, line)
            return None
        if evt.tool_name in TASK_TOOLS:
            tasks = evt.tasks
            if not (update := evt.as_input(TaskUpdateCall)) or update.status not in ("completed", "deleted"):
                state.asks = []
            state.unowned = [name for name in state.unowned if not covered(tasks, name)]
        elif name := spawned_lane(evt):
            state.unowned.append(name)
        if state.asks:
            state.ask_calls += 1
            if state.ask_calls >= ASK_CALLS:
                lines.append(ask_line(state.asks))
                state.asks = []
    return evt.context("\n".join(lines)) if lines else None


@on(
    Event.PreToolUse,
    only_if=[Tool("TaskUpdate"), FromSubagent()],
    tests={
        Input(tool="TaskUpdate", agent_id="a1b2c3", tool_input={"taskId": "1", "status": "completed"}): Allow(),
        Input(tool="TaskUpdate", agent_id="a1b2c3", tool_input={"taskId": "1", "status": "in_progress"}): Allow(),
    },
)
def lanes_leave_root_tasks_open(evt: BaseHookEvent) -> HookResult | None:
    call = evt.as_input(TaskUpdateCall)
    if call.status != "completed" or not CompactionState.load(evt).active:
        return None
    if (task := evt.tasks.get(call.task_id)) and (lane := lane_of(task)):
        return evt.block(
            f"Task #{task.id} is the root's record of lane {lane}; the root completes it once it has consumed "
            "your deliverable. SendMessage team-lead the deliverable instead and leave the task open."
        )
    return None
