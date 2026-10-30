from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cc_transcript import UserEvent
from cc_transcript.models import TranscriptEvent

from captain_hook import (
    Allow,
    BaseHookEvent,
    Block,
    Event,
    FromSubagent,
    HookResult,
    Input,
    Tool,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)
from captain_hook.util import reqenv
from captain_hook.util.caching import StampedCache

from . import session_tree
from .compaction_handoff import TURN_WINDOW, CompactionState
from .nudges import NudgeState, cancel_root_action, queue_nudge, root_action_key, stopped_lane
from .session_tree import IDLE_NOTIFICATION, TEAMMATE_MESSAGE, Subagent, covered
from .tests.rotation_fixtures import POLLER, REVIEWER, ROOT, SLEEPY
from .turns import Turn, rotation_line, turn_of

SENDER = "long-running"
MENTIONS_STAND_DOWN = re.compile(r"\bstand[\s-]?down\b", re.IGNORECASE)
REPLACES = re.compile(r"^Replaces: `?([\w.-]+)`?\s*$", re.MULTILINE)
ROTATE = (
    'ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ids>" to team-lead, then keep working.'
)
HANDOFF_BRIEF = Path(__file__).parents[2] / "skills" / "long-running" / "reference" / "handoff-subagent-brief.md"
DORMANT = timedelta(hours=1)
PACE_SECONDS = 15 * 60
PACE_LIMIT = 3
ASK_GAP_SECONDS = 30 * 60
ACK_WINDOW_SECONDS = 10 * 60
ROOT_ACTION_GAP_SECONDS = 2 * 60 * 60
TASK_LABEL_CHARS = 50
FLUSHED = re.compile(r"flushed\b", re.IGNORECASE)
FLUSHED_ID = re.compile(r"[\w#/-]*\d[\w#/-]*")
NUMBERED = re.compile(r"(.+)-(\d+)")
HANDOFF = "-handoff"
LIVE_LANES = "long-running.live-lanes"
SCAN_CACHE_SIZE = 4096
SCANS: StampedCache[Path, tuple[int, int, int, int], Scan | None] = StampedCache(SCAN_CACHE_SIZE)


@workflow_state("long_running_rotation")
class RotationState(WorkflowState):
    asks: dict[str, list[float]] = {}
    names: dict[str, str] = {}
    flushed: list[str] = []
    cursor: str | None = None
    timeline: list[dict] = []
    asked_events: dict[str, int] = {}
    frozen: dict[str, int] = {}
    root_actions: dict[str, float] = {}
    retired: dict[str, str | None] = {}
    blocked: list[str] = []


@dataclass(frozen=True)
class Lane:
    name: str
    agent_id: str
    team: str | None
    turn: Turn
    line: int
    events: int
    told_to_stand_down: bool


@dataclass(frozen=True)
class Scan:
    started: datetime
    turn: Turn | None
    events: int
    told_to_stand_down: bool
    label: str | None


@dataclass(frozen=True)
class Candidate:
    agent: Subagent
    scan: Scan


def task_label(events: Sequence[TranscriptEvent]) -> str | None:
    prompt = next((event.text for event in events if isinstance(event, UserEvent)), None)
    if prompt is None:
        return None
    if message := TEAMMATE_MESSAGE.match(prompt):
        prompt = message[2]
    return prompt[:TASK_LABEL_CHARS] + "..." if len(prompt) > TASK_LABEL_CHARS else prompt


def task_keys(candidate: Candidate) -> Iterator[str | None]:
    yield candidate.agent.meta["description"]
    yield candidate.scan.label


def team_roster(evt: BaseHookEvent, team: str, rosters: dict[str, dict[str, str]]) -> dict[str, str]:
    if team not in rosters:
        rosters[team] = session_tree.team_members(evt, team)
    return rosters[team]


def scan(agent: Subagent) -> Scan | None:
    reqenv.checkpoint()
    events = agent.read_events()
    reqenv.checkpoint()
    if not (started := session_tree.started(events)):
        return None
    return Scan(
        started=started,
        turn=turn_of(events, sidechain=True),
        events=len(events),
        told_to_stand_down=session_tree.stand_down_delivered(events),
        label=task_label(events),
    )


def scanned(agent: Subagent) -> Scan | None:
    stat = agent.transcript.stat()
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return SCANS.get(agent.transcript, identity, math.inf, lambda: scan(agent))


def newer(agent: Subagent, incumbent: Candidate | None) -> Candidate | None:
    if not (found := scanned(agent)) or (incumbent is not None and found.started <= incumbent.scan.started):
        return None
    return Candidate(agent, found)


def scan_lanes(evt: BaseHookEvent) -> tuple[Lane, ...]:
    reqenv.checkpoint()
    live_subagents = {task.id for task in evt.background_tasks if task.type == "subagent"}
    teammate_tasks = Counter(task.description for task in evt.background_tasks if task.type == "teammate")
    if not live_subagents and not teammate_tasks:
        return ()
    rosters: dict[str, dict[str, str]] = {}
    agents = [
        agent
        for agent in session_tree.subagents(evt)
        if agent.name and (agent.meta.get("teamName") or agent.agent_id in live_subagents)
    ]
    plausible = {
        agent.name
        for agent in agents
        if not (team := agent.meta.get("teamName")) or (teammate_tasks and agent.name in team_roster(evt, team, rosters))
    }
    newest: dict[str, Candidate] = {}
    for agent in agents:
        reqenv.checkpoint()
        if agent.name in plausible and (candidate := newer(agent, newest.get(agent.name))):
            newest[agent.name] = candidate
    lanes = []
    for candidate in sorted(newest.values(), key=lambda lane: lane.scan.turn.at if lane.scan.turn else lane.scan.started, reverse=True):
        reqenv.checkpoint()
        agent = candidate.agent
        if team := agent.meta.get("teamName"):
            if agent.name not in team_roster(evt, team, rosters):
                continue
            if not (label := next((key for key in task_keys(candidate) if teammate_tasks[key]), None)):
                continue
            teammate_tasks[label] -= 1
        if turn := candidate.scan.turn:
            lanes.append(
                Lane(
                    name=agent.name,
                    agent_id=agent.agent_id,
                    team=team,
                    turn=turn,
                    line=rotation_line(turn.model, agent.meta.get("model"), evt.cwd),
                    events=candidate.scan.events,
                    told_to_stand_down=candidate.scan.told_to_stand_down,
                )
            )
    return tuple(lanes)


def live_lanes(evt: BaseHookEvent) -> list[Lane]:
    return list(reqenv.shared(LIVE_LANES, lambda: scan_lanes(evt)))


def finished(evt: BaseHookEvent, lanes: list[Lane]) -> set[str]:
    return {lane.agent_id for lane in lanes if not covered(evt.tasks, lane.name)}


def over_line(lane: Lane, state: RotationState, root: Turn) -> bool:
    return lane.turn.tokens >= lane.line and root.at - lane.turn.at <= DORMANT


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


def unread(lane: Lane, state: RotationState, now: float) -> bool:
    return (
        lane.agent_id in state.asked_events
        and now - state.asks[lane.agent_id][-1] >= ACK_WINDOW_SECONDS
        and lane.events == state.asked_events[lane.agent_id]
    )


def successors(name: str) -> set[str]:
    names = {f"{name}-2"}
    if numbered := NUMBERED.fullmatch(name):
        names.add(f"{numbered[1]}-{int(numbered[2]) + 1}")
    return names


def predecessors(name: str) -> set[str]:
    if not (numbered := NUMBERED.fullmatch(name)) or (number := int(numbered[2])) < 2:
        return set()
    return {f"{numbered[1]}-{number - 1}"} | ({numbered[1]} if number == 2 else set())


def handed_off(evt: BaseHookEvent) -> set[str]:
    return {
        agent.name.removesuffix(HANDOFF) for agent in session_tree.subagents(evt) if agent.name and agent.name.endswith(HANDOFF)
    }


def stood_down(evt: BaseHookEvent, lane: Lane, live: set[str], rotated: set[str]) -> bool:
    return (
        lane.told_to_stand_down
        or (lane.name in rotated and not successors(lane.name).isdisjoint(live))
        or (lane.team is not None and session_tree.stood_down(session_tree.inbox_path(evt, lane.team, lane.name)))
    )


def awake(evt: BaseHookEvent, lanes: list[Lane], state: RotationState) -> list[Lane]:
    for lane in lanes:
        if lane.agent_id in state.frozen and lane.events != state.frozen[lane.agent_id]:
            del state.frozen[lane.agent_id]
    live = {lane.name for lane in lanes}
    rotated = handed_off(evt)
    return [
        lane
        for lane in lanes
        if lane.agent_id not in state.frozen
        and lane.name not in state.flushed
        and lane.name not in state.retired
        and not stood_down(evt, lane, live, rotated)
    ]


def forget_ask(state: RotationState, agent_id: str) -> int | None:
    del state.asks[agent_id]
    return state.asked_events.pop(agent_id, None)


def settle(state: RotationState, lanes: list[Lane], done: set[str], now: float) -> None:
    live = {lane.agent_id: lane for lane in lanes}
    for agent_id in list(state.asks):
        lane = live.get(agent_id)
        if lane and agent_id not in done and lane.turn.tokens >= lane.line and not unread(lane, state, now):
            continue
        name = state.names.pop(agent_id)
        asked = forget_ask(state, agent_id)
        if agent_id in done:
            record(state, name, agent_id, "finished", now)
        elif not lane:
            record(state, name, agent_id, "gone", now)
        elif lane.turn.tokens < lane.line:
            record(state, name, agent_id, "compacted", now, tokens=lane.turn.tokens)
        else:
            state.frozen[agent_id] = asked
            record(state, name, agent_id, "gone", now, reason="transcript unchanged since the ROTATE ask")


def escalation(lane: Lane) -> str:
    return (
        f"Rotate it by hand: `TaskStop` `{lane.name}`, spawn a handoff subagent from `{HANDOFF_BRIEF.name}`, then its "
        f"successor from the lane brief plus that handoff. Never `SendMessage` `{lane.name}`: it resumes on its stale brief."
    )


def root_action_due(state: RotationState, lane: Lane, now: float) -> bool:
    if now - state.root_actions.get(lane.name, 0.0) < ROOT_ACTION_GAP_SECONDS:
        return False
    state.root_actions[lane.name] = now
    return True


def queue_root_action(evt: BaseHookEvent, lane: Lane, text: str) -> None:
    key = root_action_key(lane.name)
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
        reply = (json.loads(body).get("result") or "" if body.startswith(IDLE_NOTIFICATION) else body).strip()
        if FLUSHED.match(reply):
            replies.append((name, FLUSHED_ID.findall(reply.splitlines()[0])))
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
        replies = [reply for event in fresh if isinstance(event, UserEvent) for reply in flushed_replies(event.text)]
        for name, ids in replies:
            if name not in state.flushed:
                state.flushed.append(name)
            cancel_root_action(evt, name)
            if asked := [agent_id for agent_id, lane in state.names.items() if lane == name]:
                for agent_id in asked:
                    del state.names[agent_id]
                    forget_ask(state, agent_id)
                    record(state, name, agent_id, "flushed", now, ids=ids)
                queue_nudge(evt, f"Lane `{name}` flushed its context. Leave it running; do not `TaskStop` it.")
    return None


@on(Event.Stop, only_if=[DriveActive()], skip_if=[FromSubagent()], transcript_events=TURN_WINDOW, tests=ACTIVE_ROOT_TESTS)
def ask_lanes_to_rotate(evt: BaseHookEvent) -> HookResult | None:
    if (root := drive_root(evt)) is None:
        return None
    now = time.time()
    with RotationState.mutate(evt) as state:
        lanes = awake(evt, live_lanes(evt), state)
        done = finished(evt, lanes)
        settle(state, lanes, done, now)
        lanes = [lane for lane in lanes if lane.agent_id not in state.frozen and lane.agent_id not in done]
        recent = sum(now - asks[0] < PACE_SECONDS for asks in state.asks.values())
        fresh = sorted(
            (lane for lane in lanes if lane.agent_id not in state.asks and due(lane, state, root, now)),
            key=lambda lane: lane.turn.tokens,
            reverse=True,
        )
        repeat = [lane for lane in lanes if lane.agent_id in state.asks and due(lane, state, root, now)]
    for lane in [*fresh[: max(PACE_LIMIT - recent, 0)], *repeat]:
        if lane.team is None:
            with RotationState.mutate(evt) as state:
                allowed = root_action_due(state, lane, now)
            if not allowed:
                continue
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
        lanes = awake(evt, live_lanes(evt), state)
        done = finished(evt, lanes)
        for lane in lanes:
            if lane.agent_id not in done and overdue(lane, state, root, now) and root_action_due(state, lane, now):
                queue_root_action(evt, lane, escalation(lane))
                record_escalation(state, lane, now)
    return None


def heir(retired: dict[str, str | None], name: str) -> str | None:
    successor = retired[name]
    while successor in retired:
        successor = retired[successor]
    return successor


def orders_stand_down(message: object) -> bool:
    return isinstance(message, str) and session_tree.orders_stand_down(message)


def mentions_stand_down(message: object) -> bool:
    return isinstance(message, str) and MENTIONS_STAND_DOWN.search(message) is not None


@on(
    Event.PostToolUse,
    only_if=[Tool("Agent", "TaskStop", "SendMessage")],
    skip_if=[FromSubagent()],
    tests={
        Input(tool="TaskStop", tool_input={"task_id": "desk@session-root"}): Allow(),
        Input(tool="Agent", tool_input={"name": "landing-desk-2", "prompt": "go"}, transcript=ROOT): Allow(),
        Input(tool="Agent", tool_input={"name": "token-retry-2", "prompt": "Replaces: token-retry\ngo"}, transcript=ROOT): Allow(),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "STAND-DOWN: desk-2 owns it"}): Allow(),
    },
)
def record_retired_lanes(evt: BaseHookEvent) -> HookResult | None:
    raw = evt.input.raw
    if evt.tool_name == "Agent":
        if not (name := raw.get("name")):
            return None
        rotated = (predecessors(name) & handed_off(evt)) | (set(REPLACES.findall(raw.get("prompt") or "")) - {name})
        with RotationState.mutate(evt) as state:
            state.retired.pop(name, None)
            state.blocked = [lane for lane in state.blocked if lane != name]
            state.retired |= dict.fromkeys(rotated, name)
        return None
    retired = stopped_lane(evt) or (raw.get("to") if orders_stand_down(raw.get("message")) else None)
    if retired:
        with RotationState.mutate(evt) as state:
            state.retired.setdefault(retired, None)
        cancel_root_action(evt, retired)
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("SendMessage")],
    tests={
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, state=[RotationState(retired={"desk": "desk-2"})]): Block(
            pattern=r"^`desk` rotated to `desk-2`.*`TaskStop` `desk`, and send its work to `desk-2`\.$"
        ),
        Input(
            tool="SendMessage", tool_input={"to": "desk", "message": "STAND-DOWN: desk-2 owns it"}, state=[RotationState(retired={"desk": "desk-2"})]
        ): Block(),
        Input(
            tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, agent_id="a1b2c3", state=[RotationState(retired={"desk": "desk-2"})]
        ): Block(pattern=r"^(?!.*TaskStop).*Send its work to `desk-2`\.$"),
        Input(
            tool="SendMessage",
            tool_input={"to": "desk", "message": "status?"},
            agent_id="a1b2c3",
            state=[RotationState(retired={"desk": "desk-2", "desk-2": "desk-3"})],
        ): Block(pattern="Send its work to `desk-3`"),
        Input(
            tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, state=[RotationState(retired={"desk": "desk-2"}, blocked=["desk"])]
        ): Warn(pattern=r"^Warning, nothing was blocked: `desk` rotated to `desk-2`.*`TaskStop` `desk`"),
        Input(
            tool="SendMessage",
            tool_input={"to": "desk", "message": "Lane desk: stand down now, desk-2 took over at 11:09 AM; send nothing further."},
            state=[RotationState(retired={"desk": "desk-2"}, blocked=["desk"])],
        ): Warn(),
        Input(
            tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, agent_id="a1b2c3", state=[RotationState(retired={"desk": None})]
        ): Warn(pattern="Send the next one to `main`"),
        Input(tool="SendMessage", tool_input={"to": "desk", "message": "status?"}, state=[RotationState(retired={"desk": None})]): Allow(),
        Input(tool="SendMessage", tool_input={"to": "desk-2", "message": "status?"}, state=[RotationState(retired={"desk": "desk-2"})]): Allow(),
    },
)
def redirect_retired_lanes(evt: BaseHookEvent) -> HookResult | None:
    raw = evt.input.raw
    retired = RotationState.load(evt).retired
    if (to := raw.get("to")) not in retired:
        return None
    if successor := heir(retired, to):
        with RotationState.mutate(evt) as state:
            if first := to not in state.blocked:
                state.blocked.append(to)
        redirect = f"Stand it down with `TaskStop` `{to}`, and send" if evt.agent_id is None else "Send"
        if first:
            return evt.block(
                f"`{to}` rotated to `{successor}`, which owns its work now, so this `SendMessage` is blocked: any message to `{to}`, "
                f"a stand-down included, resumes it on its stale brief. {redirect} its work to `{successor}`."
            )
        return evt.context(
            f"Warning, nothing was blocked: `{to}` rotated to `{successor}`, which owns its work now, and this message resumes the old lane "
            f"on its stale brief. {redirect} the next one to `{successor}`."
        )
    if mentions_stand_down(raw.get("message")) or evt.agent_id is None:
        return None
    return evt.context(
        f"Warning, nothing was blocked: `{to}` was stopped and has no successor, and this message resumes the stopped lane on its stale brief. "
        "Send the next one to `main`."
    )
