from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cc_transcript import UserEvent

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    WorkflowState,
    on,
    workflow_state,
)

from . import session_tree
from .compaction_handoff import TURN_WINDOW, CompactionState
from .nudges import NudgeState, queue_nudge
from .session_tree import IDLE_NOTIFICATION, TEAMMATE_MESSAGE, Subagent
from .tests.rotation_fixtures import POLLER, REVIEWER, ROOT, SLEEPY
from .turns import Turn, rotation_line, turn_of

SENDER = "long-running"
ROTATE = (
    'ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ids>" to team-lead, then keep working.'
)
ROOT_ACTION = "ROOT-ACTION"
HANDOFF_BRIEF = Path(__file__).parents[2] / "skills" / "long-running" / "reference" / "handoff-subagent-brief.md"
DORMANT = timedelta(hours=1)
PACE_SECONDS = 15 * 60
PACE_LIMIT = 3
ASK_GAP_SECONDS = 30 * 60
ACK_WINDOW_SECONDS = 10 * 60
ESCALATE_GAP_SECONDS = 15 * 60
TASK_LABEL_CHARS = 50
FLUSHED = re.compile(r"flushed:?((?:[ ,]+[0-9a-f]{6,40}\b)+)", re.IGNORECASE)


@workflow_state("long_running_rotation")
class RotationState(WorkflowState):
    asks: dict[str, list[float]] = {}
    names: dict[str, str] = {}
    flushed: list[str] = []
    cursor: str | None = None
    timeline: list[dict] = []
    asked_events: dict[str, int] = {}
    frozen: dict[str, int] = {}
    escalated: dict[str, tuple[float, int]] = {}


@dataclass(frozen=True)
class Lane:
    name: str
    agent_id: str
    stop_id: str
    team: str | None
    turn: Turn
    line: int
    events: int


def task_label(agent: Subagent) -> str | None:
    prompt = next((event.text for event in agent.events if isinstance(event, UserEvent)), None)
    if prompt is None:
        return None
    if message := TEAMMATE_MESSAGE.match(prompt):
        prompt = message[2]
    return prompt[:TASK_LABEL_CHARS] + "..." if len(prompt) > TASK_LABEL_CHARS else prompt


def live_lanes(evt: BaseHookEvent) -> list[Lane]:
    live_subagents = {task.id for task in evt.background_tasks if task.type == "subagent"}
    teammate_tasks = Counter(task.description for task in evt.background_tasks if task.type == "teammate")
    rosters: dict[str, dict[str, str]] = {}
    newest: dict[str, Subagent] = {}
    for agent in session_tree.subagents(evt):
        if not agent.name or (not agent.meta.get("teamName") and agent.agent_id not in live_subagents):
            continue
        if agent.started and (agent.name not in newest or agent.started > newest[agent.name].started):
            newest[agent.name] = agent
    active = sorted(
        ((agent, turn_of(agent.events, sidechain=True)) for agent in newest.values()),
        key=lambda lane: lane[1].at if lane[1] else lane[0].started,
        reverse=True,
    )
    lanes = []
    for agent, turn in active:
        stop_id = agent.agent_id
        if team := agent.meta.get("teamName"):
            roster = rosters.setdefault(team, session_tree.team_members(evt, team))
            if agent.name not in roster:
                continue
            if not (label := next((key for key in (agent.meta["description"], task_label(agent)) if teammate_tasks[key]), None)):
                continue
            teammate_tasks[label] -= 1
            stop_id = roster[agent.name]
        if turn:
            lanes.append(
                Lane(
                    name=agent.name,
                    agent_id=agent.agent_id,
                    stop_id=stop_id,
                    team=team,
                    turn=turn,
                    line=rotation_line(turn.model, agent.meta.get("model"), evt.cwd),
                    events=len(agent.events),
                )
            )
    return lanes


def over_line(lane: Lane, state: RotationState, root: Turn) -> bool:
    return lane.agent_id not in state.flushed and lane.turn.tokens >= lane.line and root.at - lane.turn.at <= DORMANT


def due(lane: Lane, state: RotationState, root: Turn, now: float) -> bool:
    asks = state.asks.get(lane.agent_id, [])
    return over_line(lane, state, root) and (not asks or now - asks[-1] >= ASK_GAP_SECONDS)


def overdue(lane: Lane, state: RotationState, root: Turn, now: float) -> bool:
    asks = state.asks.get(lane.agent_id)
    return bool(asks) and over_line(lane, state, root) and now - asks[0] >= ACK_WINDOW_SECONDS


def iso(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def record(state: RotationState, name: str, agent_id: str, event: str, now: float, **fields: object) -> None:
    state.timeline.append({"at": iso(now), "lane": name, "agent_id": agent_id, "event": event, **fields})


def record_escalation(state: RotationState, lane: Lane, now: float) -> None:
    last = next((entry for entry in reversed(state.timeline) if entry["agent_id"] == lane.agent_id), None)
    if last and last["event"] == "escalate":
        last |= {"last": iso(now), "count": last["count"] + 1, "tokens": lane.turn.tokens}
    else:
        record(state, lane.name, lane.agent_id, "escalate", now, last=iso(now), count=1, tokens=lane.turn.tokens)


def escalation_due(lane: Lane, state: RotationState, now: float) -> bool:
    if (last := state.escalated.get(lane.agent_id)) is None:
        return True
    at, asks = last
    return asks != len(state.asks[lane.agent_id]) or now - at >= ESCALATE_GAP_SECONDS


def unread(lane: Lane, state: RotationState, now: float) -> bool:
    return (
        lane.agent_id in state.asked_events
        and now - state.asks[lane.agent_id][-1] >= ACK_WINDOW_SECONDS
        and lane.events == state.asked_events[lane.agent_id]
    )


def awake(lanes: list[Lane], state: RotationState) -> list[Lane]:
    for lane in lanes:
        if lane.agent_id in state.frozen and lane.events != state.frozen[lane.agent_id]:
            del state.frozen[lane.agent_id]
    return [lane for lane in lanes if lane.agent_id not in state.frozen]


def settle(state: RotationState, lanes: list[Lane], now: float) -> None:
    live = {lane.agent_id: lane for lane in lanes}
    for agent_id in [agent_id for agent_id in state.asks if agent_id not in state.flushed]:
        lane = live.get(agent_id)
        if lane and lane.turn.tokens >= lane.line and not unread(lane, state, now):
            continue
        name = state.names.pop(agent_id)
        del state.asks[agent_id]
        state.escalated.pop(agent_id, None)
        asked = state.asked_events.pop(agent_id, None)
        if not lane:
            record(state, name, agent_id, "gone", now)
        elif lane.turn.tokens < lane.line:
            record(state, name, agent_id, "compacted", now, tokens=lane.turn.tokens)
        else:
            state.frozen[agent_id] = asked
            record(state, name, agent_id, "gone", now, reason="transcript unchanged since the ROTATE ask")


def escalation(lane: Lane) -> str:
    return (
        f"Rotate it by hand: spawn a handoff subagent from `{HANDOFF_BRIEF.name}`, spawn its successor from the lane "
        f"brief plus that handoff, then `TaskStop` `{lane.stop_id}`."
    )


def queue_root_action(evt: BaseHookEvent, lane: Lane, text: str) -> None:
    key = f"{ROOT_ACTION} `{lane.name}`"
    with NudgeState.mutate(evt) as nudges:
        nudges.pending = [line for line in nudges.pending if not line.startswith(key)] + [f"{key}: {text}"]


def rotate_message() -> dict:
    return {
        "from": SENDER,
        "text": ROTATE,
        "summary": "ROTATE: flush and keep working",
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "msgV": 1,
        "msg_id": str(uuid.uuid4()),
        "type": "message",
        "read": False,
    }


def flushed_replies(text: str) -> list[tuple[str, list[str]]]:
    replies = []
    for name, body in TEAMMATE_MESSAGE.findall(text):
        reply = json.loads(body).get("result") or "" if body.startswith(IDLE_NOTIFICATION) else body
        if match := FLUSHED.match(reply.strip()):
            replies.append((name, re.split(r"[ ,]+", match[1].strip())))
    return replies


class DriveActive:
    def check(self, evt: BaseHookEvent) -> bool:
        return CompactionState.load(evt).active


def drive_root(evt: BaseHookEvent) -> Turn | None:
    return turn_of(evt.ctx.t.events)


ACTIVE_ROOT_TESTS = {
    Input(transcript=ROOT, background_tasks=[REVIEWER]): Allow(),
    Input(transcript=ROOT, agent_id="a1b2c3", background_tasks=[REVIEWER], state=[CompactionState(active=True)]): Allow(),
    Input(transcript=ROOT, state=[CompactionState(active=True)]): Allow(),
    Input(transcript=ROOT, background_tasks=[SLEEPY, POLLER], state=[CompactionState(active=True)]): Allow(),
}


@on(Event.Stop, only_if=[DriveActive()], skip_if=[FromSubagent()], transcript_events=TURN_WINDOW, tests=ACTIVE_ROOT_TESTS)
def note_flushed_lanes(evt: BaseHookEvent) -> HookResult | None:
    if drive_root(evt) is None:
        return None
    now = time.time()
    with RotationState.mutate(evt) as state:
        fresh, state.cursor = session_tree.events_after(evt.ctx.t.events, state.cursor)
        pending = {agent_id: name for agent_id, name in state.names.items() if agent_id not in state.flushed}
        for event in fresh if pending else []:
            if not isinstance(event, UserEvent):
                continue
            for name, ids in flushed_replies(event.text):
                if asked := [
                    agent_id for agent_id, lane in pending.items() if lane == name and agent_id not in state.flushed
                ]:
                    state.flushed.extend(asked)
                    for agent_id in asked:
                        record(state, name, agent_id, "flushed", now, ids=ids)
                    queue_nudge(evt, f"Lane `{name}` flushed its context. Leave it running; do not `TaskStop` it.")
    return None


@on(Event.Stop, only_if=[DriveActive()], skip_if=[FromSubagent()], transcript_events=TURN_WINDOW, tests=ACTIVE_ROOT_TESTS)
def ask_lanes_to_rotate(evt: BaseHookEvent) -> HookResult | None:
    if (root := drive_root(evt)) is None:
        return None
    now = time.time()
    with RotationState.mutate(evt) as state:
        lanes = awake(live_lanes(evt), state)
        settle(state, lanes, now)
        lanes = [lane for lane in lanes if lane.agent_id not in state.frozen]
        recent = sum(now - asks[0] < PACE_SECONDS for asks in state.asks.values())
        fresh = sorted(
            (lane for lane in lanes if lane.agent_id not in state.asks and due(lane, state, root, now)),
            key=lambda lane: lane.turn.tokens,
            reverse=True,
        )
        repeat = [lane for lane in lanes if lane.agent_id in state.asks and due(lane, state, root, now)]
    for lane in [*fresh[: max(PACE_LIMIT - recent, 0)], *repeat]:
        if lane.team is None:
            queue_root_action(evt, lane, f"`SendMessage` it now, since it has no teammate inbox: `{ROTATE}`")
            asked_events = None
        elif session_tree.append_inbox(session_tree.inbox_path(evt, lane.team, lane.name), rotate_message()):
            asked_events = lane.events
        else:
            continue
        with RotationState.mutate(evt) as state:
            if asked_events is not None:
                state.asked_events[lane.agent_id] = asked_events
            state.asks.setdefault(lane.agent_id, []).append(now)
            state.names[lane.agent_id] = lane.name
            via = "root" if lane.team is None else "inbox"
            record(state, lane.name, lane.agent_id, "ask", now, via=via, tokens=lane.turn.tokens, line=lane.line)
    return None


@on(Event.Stop, only_if=[DriveActive()], skip_if=[FromSubagent()], transcript_events=TURN_WINDOW, tests=ACTIVE_ROOT_TESTS)
def escalate_unrotated_lanes(evt: BaseHookEvent) -> HookResult | None:
    if (root := drive_root(evt)) is None:
        return None
    now = time.time()
    with RotationState.mutate(evt) as state:
        for lane in awake(live_lanes(evt), state):
            if overdue(lane, state, root, now) and escalation_due(lane, state, now):
                queue_root_action(evt, lane, escalation(lane))
                record_escalation(state, lane, now)
                state.escalated[lane.agent_id] = (now, len(state.asks[lane.agent_id]))
    return None
