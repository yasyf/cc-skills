from __future__ import annotations

import re
import time
from collections.abc import Iterable, Iterator

from cc_transcript import AttachmentEvent, UserEvent
from cc_transcript.models import TranscriptEvent

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    TaskUpdateCall,
    Tool,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)

from . import session_tree
from .compaction_handoff import TURN_WINDOW, CompactionState
from .lane_rotation import DriveActive
from .nudges import once_per_hour, queue_nudge
from .session_tree import TEAMMATE_MESSAGE, lane_of

ASK_CALLS = 3
ASK_LINE = "Every owner ask gets a task. Run `TaskCreate` for it."
ASK_GRACE_SECONDS = 10 * 60
TASK_TOOLS = frozenset({"TaskCreate", "TaskUpdate"})
STATUS_QUESTION = re.compile(r"\bstatus\b[^?]*\?\s*$", re.IGNORECASE)
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
    created_at: float = 0.0


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
    now = time.time()
    with AskState.mutate(evt) as state:
        texts, state.cursor = fresh_prompts(evt, state.cursor)
        if any(is_ask(text) for text in texts):
            state.pending, state.calls = True, 0
        if evt.tool_name == "TaskCreate":
            state.created_at = now
        recent = now - state.created_at < ASK_GRACE_SECONDS
        if evt.event == Event.Stop:
            asked, state.pending = state.pending, False
            return deliver(evt, once_per_hour(evt, [ASK_LINE] * (asked and not recent)))
        if evt.tool_name in TASK_TOOLS and (
            not (update := evt.as_input(TaskUpdateCall)) or update.status not in ("completed", "deleted")
        ):
            state.pending = False
        if state.pending:
            state.calls += 1
            if state.calls >= ASK_CALLS:
                state.pending = False
                return None if recent else deliver(evt, once_per_hour(evt, [ASK_LINE]))
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("TaskUpdate"), FromSubagent(), DriveActive()],
    max_fires=1,
    tests={
        Input(
            tool="TaskUpdate",
            agent_id="a1b2c3",
            tool_input={"taskId": "1", "status": "completed"},
            tasks=[LANE_TASK],
            state=[CompactionState(active=True)],
        ): Warn(pattern="leave the task open"),
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
    if (task := evt.task(call.task_id)) and (lane := lane_of(task)):
        return evt.context(
            f"Warning, nothing was blocked: Task #{task.id} is the root's record of lane {lane}; the root completes it once it has consumed "
            "your deliverable. SendMessage team-lead the deliverable instead and leave the task open."
        )
    return None
