from __future__ import annotations

import json
import re
import secrets
import time
import uuid
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

from .compaction_handoff import CompactionState
from .nudges import NudgeState, queue_nudge
from .turns import Turn, latest_turn, rotation_line

FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "rotation"
ROOT = FIXTURES / "projects" / "p" / "root.jsonl"
SENDER = "long-running"
ROTATE = (
    'ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ids>" to team-lead, then keep working.'
)
ROOT_ACTION = "ROOT-ACTION"
DORMANT = timedelta(hours=1)
PACE_SECONDS = 15 * 60
PACE_LIMIT = 3
ASK_GAP_SECONDS = 30 * 60
ACK_WINDOW_SECONDS = 10 * 60
NUMBERED = re.compile(r"(.+)-(\d+)")
LOCK_STALE_SECONDS = 10
LOCK_RETRIES = 10
LOCK_MIN_DELAY = 0.005
LOCK_MAX_DELAY = 0.1
UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")
TASK_LABEL_CHARS = 50
TEAMMATE_MESSAGE = re.compile(r'<teammate-message teammate_id="([^"]+)"[^>]*>\n(.*?)\n</teammate-message>', re.DOTALL)
IDLE_NOTIFICATION = '{"type":"idle_notification"'
FLUSHED = re.compile(r"flushed:?((?:[ ,]+[0-9a-f]{6,40}\b)+)", re.IGNORECASE)
SLEEPY = {"id": "t-sleepy", "type": "teammate", "status": "running", "description": "Sleepy lane"}
POLLER = {"id": "t-poller", "type": "teammate", "status": "running", "description": "PR poller"}
REVIEWER = {"id": "areviewer-3c3c3c3c3c3c3c3c", "type": "subagent", "status": "running", "description": "Reviewer"}


@workflow_state("long_running_rotation")
class RotationState(WorkflowState):
    asks: dict[str, list[float]] = {}
    names: dict[str, str] = {}
    flushed: list[str] = []
    scanned: int | None = None
    timeline: list[dict] = []
    asked_size: dict[str, int] = {}
    frozen: dict[str, int] = {}


@dataclass(frozen=True)
class Lane:
    name: str
    agent_id: str
    stop_id: str
    team: str | None
    turn: Turn
    line: int
    transcript: Path
    spawned: datetime


def spawned_at(transcript: Path) -> str | None:
    if not transcript.is_file():
        return None
    with transcript.open() as lines:
        return next((stamp for line in lines if (stamp := json.loads(line).get("timestamp"))), None)


def task_label(transcript: Path) -> str | None:
    with transcript.open() as lines:
        prompt = next((entry["message"]["content"] for line in lines if (entry := json.loads(line)).get("type") == "user"), None)
    if not isinstance(prompt, str):
        return None
    if message := TEAMMATE_MESSAGE.match(prompt):
        prompt = message[2]
    return prompt[:TASK_LABEL_CHARS] + "..." if len(prompt) > TASK_LABEL_CHARS else prompt


def team_dir(evt: BaseHookEvent, team: str) -> Path:
    return evt.transcript_path.parents[2] / "teams" / UNSAFE_NAME.sub("-", team)


def team_members(evt: BaseHookEvent, team: str) -> dict[str, str]:
    config = team_dir(evt, team) / "config.json"
    if not config.is_file():
        return {}
    return {member["name"]: member["agentId"] for member in json.loads(config.read_text())["members"]}


def live_lanes(evt: BaseHookEvent) -> list[Lane]:
    live_subagents = {task.id for task in evt.background_tasks if task.type == "subagent"}
    teammate_tasks = Counter(task.description for task in evt.background_tasks if task.type == "teammate")
    rosters: dict[str, dict[str, str]] = {}
    newest: dict[str, tuple[str, Path, dict]] = {}
    for meta_path in sorted((evt.transcript_path.with_suffix("") / "subagents").glob("agent-*.meta.json")):
        meta = json.loads(meta_path.read_text())
        if not (name := meta.get("name")):
            continue
        transcript = meta_path.with_name(meta_path.name.removesuffix(".meta.json") + ".jsonl")
        if not meta.get("teamName") and transcript.stem.removeprefix("agent-") not in live_subagents:
            continue
        if (started := spawned_at(transcript)) and (name not in newest or started > newest[name][0]):
            newest[name] = (started, transcript, meta)
    active = sorted(
        ((name, transcript, meta, latest_turn(transcript, sidechain=True)) for name, (_, transcript, meta) in newest.items()),
        key=lambda lane: lane[3].at if lane[3] else datetime.fromisoformat(newest[lane[0]][0]),
        reverse=True,
    )
    lanes = []
    for name, transcript, meta, turn in active:
        agent_id = transcript.stem.removeprefix("agent-")
        stop_id = agent_id
        if team := meta.get("teamName"):
            roster = rosters.setdefault(team, team_members(evt, team))
            if name not in roster:
                continue
            if not (label := next((key for key in (meta["description"], task_label(transcript)) if teammate_tasks[key]), None)):
                continue
            teammate_tasks[label] -= 1
            stop_id = roster[name]
        if turn:
            lanes.append(
                Lane(
                    name=name,
                    agent_id=agent_id,
                    stop_id=stop_id,
                    team=meta.get("teamName"),
                    turn=turn,
                    line=rotation_line(turn.model, meta.get("model"), evt.cwd),
                    transcript=transcript,
                    spawned=datetime.fromisoformat(newest[name][0]),
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


def unread(lane: Lane, state: RotationState, now: float) -> bool:
    return (
        lane.agent_id in state.asked_size
        and now - state.asks[lane.agent_id][-1] >= ACK_WINDOW_SECONDS
        and lane.transcript.stat().st_size == state.asked_size[lane.agent_id]
    )


def awake(lanes: list[Lane], state: RotationState) -> list[Lane]:
    for lane in lanes:
        if lane.agent_id in state.frozen and lane.transcript.stat().st_size != state.frozen[lane.agent_id]:
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
        size = state.asked_size.pop(agent_id, None)
        if not lane:
            record(state, name, agent_id, "gone", now)
        elif lane.turn.tokens < lane.line:
            record(state, name, agent_id, "compacted", now, tokens=lane.turn.tokens)
        else:
            state.frozen[agent_id] = size
            record(state, name, agent_id, "gone", now, reason="transcript unchanged since the ROTATE ask")


def successor(name: str) -> str:
    if match := NUMBERED.fullmatch(name):
        return f"{match[1]}-{int(match[2]) + 1}"
    return f"{name}-2"


def span(seconds: float) -> str:
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f"{hours}h{minutes:02}m"


def escalation(lane: Lane, state: RotationState, now: float) -> str:
    asks = state.asks[lane.agent_id]
    megabytes = lane.transcript.stat().st_size / 1_000_000
    since = datetime.fromtimestamp(asks[0], UTC).strftime("%H:%MZ")
    return (
        f"rotate it by hand now. It holds {lane.turn.tokens:,} tokens against its {lane.line:,} line "
        f"(transcript {megabytes:.1f} MB, running {span(now - lane.spawned.timestamp())}) and has not replied "
        f"`flushed` to {len(asks)} ROTATE ask(s) since {since}. "
        f"1. Spawn `{successor(lane.name)}` from `{lane.name}`'s brief plus its handoff (ledger rows, cc-notes, cursor). "
        f"2. Once `{successor(lane.name)}` reports, TaskStop `{lane.stop_id}` to stop `{lane.name}`. "
        f"A `flushed <ids>` reply from `{lane.name}` cancels this."
    )


def queue_root_action(evt: BaseHookEvent, lane: Lane, text: str) -> None:
    key = f"{ROOT_ACTION} `{lane.name}`"
    with NudgeState.mutate(evt) as nudges:
        nudges.pending = [line for line in nudges.pending if not line.startswith(key)] + [f"{key}: {text}"]


def inbox_path(evt: BaseHookEvent, lane: Lane) -> Path:
    return team_dir(evt, lane.team) / "inboxes" / f"{UNSAFE_NAME.sub('-', lane.name)}.json"


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


def acquire(lock: Path) -> bool:
    delay = LOCK_MIN_DELAY
    for _ in range(LOCK_RETRIES + 1):
        try:
            lock.mkdir()
            return True
        except FileExistsError:
            with suppress(FileNotFoundError):
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.rmdir()
                    continue
        time.sleep(delay)
        delay = min(delay * 2, LOCK_MAX_DELAY)
    return False


def append_inbox(inbox: Path, message: dict) -> bool:
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with suppress(FileExistsError), inbox.open("x") as fresh:
        fresh.write("[]")
    lock = inbox.with_name(f"{inbox.name}.lock")
    if not acquire(lock):
        return False
    try:
        try:
            messages = json.loads(inbox.read_text())
        except json.JSONDecodeError:
            messages = []
        staged = inbox.with_name(f"{inbox.name}.tmp.{secrets.token_hex(4)}")
        staged.write_text(json.dumps([*messages, message], indent=2, ensure_ascii=False))
        staged.replace(inbox)
    finally:
        lock.rmdir()
    return True


def flushed_replies(text: str) -> list[tuple[str, list[str]]]:
    replies = []
    for name, body in TEAMMATE_MESSAGE.findall(text):
        reply = json.loads(body).get("result") or "" if body.startswith(IDLE_NOTIFICATION) else body
        if match := FLUSHED.match(reply.strip()):
            replies.append((name, re.split(r"[ ,]+", match[1].strip())))
    return replies


def entries_after(transcript: Path, offset: int) -> tuple[list[dict], int]:
    start = max(offset - 1, 0)
    with transcript.open("rb") as file:
        file.seek(start)
        data = file.read()
    if offset:
        if not (cut := data.find(b"\n") + 1):
            return [], offset
        start, data = start + cut, data[cut:]
    end = data.rfind(b"\n") + 1
    return [json.loads(line) for line in data[:end].splitlines() if line.strip()], start + end


def nudge_flushed(evt: BaseHookEvent, state: RotationState, now: float) -> None:
    pending = {agent_id: name for agent_id, name in state.names.items() if agent_id not in state.flushed}
    if not pending:
        state.scanned = evt.transcript_path.stat().st_size
        return
    entries, state.scanned = entries_after(evt.transcript_path, state.scanned)
    for entry in entries:
        if entry.get("type") != "user" or not isinstance(content := entry["message"]["content"], str):
            continue
        for name, ids in flushed_replies(content):
            if asked := [agent_id for agent_id, lane in pending.items() if lane == name and agent_id not in state.flushed]:
                state.flushed.extend(asked)
                for agent_id in asked:
                    record(state, name, agent_id, "flushed", now, ids=ids)
                queue_nudge(
                    evt,
                    f"lane {name} flushed ({', '.join(ids)}) and keeps running in place; nothing to do",
                )


@on(
    Event.Stop,
    skip_if=[FromSubagent()],
    tests={
        Input(transcript=ROOT, background_tasks=[REVIEWER]): Allow(),
        Input(transcript=ROOT, agent_id="a1b2c3", background_tasks=[REVIEWER], state=[CompactionState(active=True)]): Allow(),
        Input(transcript=ROOT, state=[CompactionState(active=True)]): Allow(),
        Input(transcript=ROOT, background_tasks=[SLEEPY, POLLER], state=[CompactionState(active=True)]): Allow(),
        Input(transcript=ROOT, background_tasks=[REVIEWER], state=[CompactionState(active=True)]): Allow(),
        Input(
            transcript=ROOT,
            background_tasks=[REVIEWER],
            state=[
                CompactionState(active=True),
                RotationState(
                    asks={"areviewer-3c3c3c3c3c3c3c3c": [0.0, 1.0]},
                    names={"areviewer-3c3c3c3c3c3c3c3c": "reviewer"},
                    scanned=0,
                ),
            ],
        ): Allow(),
        Input(
            transcript=ROOT,
            state=[
                CompactionState(active=True),
                RotationState(
                    asks={"alanding-desk-1a1a1a1a1a1a1a1a": [0.0]},
                    names={"alanding-desk-1a1a1a1a1a1a1a1a": "landing-desk"},
                    scanned=0,
                ),
            ],
        ): Allow(),
    },
)
def rotate_lanes(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (root := latest_turn(evt.transcript_path)):
        return None
    now = time.time()
    state = RotationState.load(evt)
    nudge_flushed(evt, state, now)
    lanes = awake(live_lanes(evt), state)
    settle(state, lanes, now)
    lanes = [lane for lane in lanes if lane.agent_id not in state.frozen]
    state.save(evt)
    recent = sum(now - asks[0] < PACE_SECONDS for asks in state.asks.values())
    fresh = sorted(
        (lane for lane in lanes if lane.agent_id not in state.asks and due(lane, state, root, now)),
        key=lambda lane: lane.turn.tokens,
        reverse=True,
    )
    repeat = [lane for lane in lanes if lane.agent_id in state.asks and due(lane, state, root, now)]
    for lane in [*fresh[: max(PACE_LIMIT - recent, 0)], *repeat]:
        if lane.team is None:
            queue_root_action(
                evt, lane, f"SendMessage it now; it has no teammate inbox and holds {lane.turn.tokens:,} tokens: `{ROTATE}`"
            )
        elif append_inbox(inbox_path(evt, lane), rotate_message()):
            state.asked_size[lane.agent_id] = lane.transcript.stat().st_size
        else:
            continue
        state.asks.setdefault(lane.agent_id, []).append(now)
        state.names[lane.agent_id] = lane.name
        via = "root" if lane.team is None else "inbox"
        record(state, lane.name, lane.agent_id, "ask", now, via=via, tokens=lane.turn.tokens, line=lane.line)
        state.save(evt)
    for lane in lanes:
        if overdue(lane, state, root, now):
            queue_root_action(evt, lane, escalation(lane, state, now))
            record_escalation(state, lane, now)
    state.save(evt)
    return None
