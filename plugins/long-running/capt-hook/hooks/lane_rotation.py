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
from .nudges import queue_nudge
from .turns import Turn, latest_turn, rotation_line

FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "rotation"
ROOT = FIXTURES / "projects" / "p" / "root.jsonl"
SENDER = "long-running"
ROTATE = (
    'ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ledger id>" to team-lead, then stop.'
)
DORMANT = timedelta(hours=1)
PACE_SECONDS = 15 * 60
PACE_LIMIT = 3
MAX_ASKS = 2
ASK_GAP_SECONDS = 30 * 60
LOCK_STALE_SECONDS = 10
LOCK_RETRIES = 10
LOCK_MIN_DELAY = 0.005
LOCK_MAX_DELAY = 0.1
UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")
TEAMMATE_MESSAGE = re.compile(r'<teammate-message teammate_id="([^"]+)"[^>]*>\n(.*?)\n</teammate-message>', re.DOTALL)
IDLE_NOTIFICATION = '{"type":"idle_notification"'
FLUSHED = re.compile(r"flushed((?:[ ,]+[0-9a-f]{6,40}\b)+)")
SLEEPY = {"id": "t-sleepy", "type": "teammate", "status": "running", "description": "Sleepy lane"}
POLLER = {"id": "t-poller", "type": "teammate", "status": "running", "description": "PR poller"}
REVIEWER = {"id": "areviewer-3c3c3c3c3c3c3c3c", "type": "subagent", "status": "running", "description": "Reviewer"}


@workflow_state("long_running_rotation")
class RotationState(WorkflowState):
    asks: dict[str, list[float]] = {}
    names: dict[str, str] = {}
    flushed: list[str] = []


@dataclass(frozen=True)
class Lane:
    name: str
    agent_id: str
    team: str | None
    turn: Turn
    line: int


def spawned_at(transcript: Path) -> str | None:
    if not transcript.is_file():
        return None
    with transcript.open() as lines:
        first = next(lines, None)
    return json.loads(first)["timestamp"] if first else None


def live_lanes(evt: BaseHookEvent) -> list[Lane]:
    live_subagents = {task.id for task in evt.background_tasks if task.type == "subagent"}
    teammate_tasks = Counter(task.description for task in evt.background_tasks if task.type == "teammate")
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
    lanes = []
    for name, (_, transcript, meta) in sorted(newest.items(), key=lambda item: item[1][0], reverse=True):
        if meta.get("teamName"):
            if not teammate_tasks[meta["description"]]:
                continue
            teammate_tasks[meta["description"]] -= 1
        if turn := latest_turn(transcript, sidechain=True):
            lanes.append(
                Lane(
                    name=name,
                    agent_id=transcript.stem.removeprefix("agent-"),
                    team=meta.get("teamName"),
                    turn=turn,
                    line=rotation_line(turn.model, meta.get("model"), evt.cwd),
                )
            )
    return lanes


def due(lane: Lane, state: RotationState, root: Turn, now: float) -> bool:
    asks = state.asks.get(lane.agent_id, [])
    return (
        lane.agent_id not in state.flushed
        and lane.turn.tokens >= lane.line
        and root.at - lane.turn.at <= DORMANT
        and len(asks) < MAX_ASKS
        and (not asks or now - asks[-1] >= ASK_GAP_SECONDS)
    )


def inbox_path(evt: BaseHookEvent, lane: Lane) -> Path:
    team_dir = evt.transcript_path.parents[2] / "teams" / UNSAFE_NAME.sub("-", lane.team)
    return team_dir / "inboxes" / f"{UNSAFE_NAME.sub('-', lane.name)}.json"


def rotate_message() -> dict:
    return {
        "from": SENDER,
        "text": ROTATE,
        "summary": "ROTATE: flush and stop",
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
    inbox.parent.mkdir(exist_ok=True)
    with suppress(FileExistsError), inbox.open("x") as fresh:
        fresh.write("[]")
    lock = inbox.with_name(f"{inbox.name}.lock")
    if not acquire(lock):
        return False
    try:
        messages = json.loads(inbox.read_text())
        staged = inbox.with_name(f"{inbox.name}.tmp.{secrets.token_hex(4)}")
        staged.write_text(json.dumps([*messages, message], indent=2, ensure_ascii=False))
        staged.replace(inbox)
    finally:
        lock.rmdir()
    return True


def listed(lanes: list[Lane]) -> str:
    return ", ".join(f"`{lane.name}` ({lane.turn.tokens:,})" for lane in lanes)


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
            state=[CompactionState(active=True), RotationState(asks={"areviewer-3c3c3c3c3c3c3c3c": [0.0, 1.0]})],
        ): Allow(),
    },
)
def rotate_lanes(evt: BaseHookEvent) -> HookResult | None:
    if not CompactionState.load(evt).active or not (root := latest_turn(evt.transcript_path)):
        return None
    now = time.time()
    with RotationState.mutate(evt) as state:
        recent = sum(now - at < PACE_SECONDS for asks in state.asks.values() for at in asks)
        candidates = sorted(
            (lane for lane in live_lanes(evt) if due(lane, state, root, now)),
            key=lambda lane: lane.turn.tokens,
            reverse=True,
        )
        unreachable = []
        for lane in candidates[: max(PACE_LIMIT - recent, 0)]:
            if lane.team is None:
                unreachable.append(lane)
            elif not append_inbox(inbox_path(evt, lane), rotate_message()):
                continue
            state.asks.setdefault(lane.agent_id, []).append(now)
            state.names[lane.agent_id] = lane.name
        if unreachable:
            queue_nudge(
                evt,
                f"Lanes over their rotation line with no teammate inbox: {listed(unreachable)}. "
                f"SendMessage each `{ROTATE}` when convenient.",
            )
    return None


def flushed_replies(prompt: str) -> list[tuple[str, list[str]]]:
    replies = []
    for name, body in TEAMMATE_MESSAGE.findall(prompt):
        text = json.loads(body).get("result") or "" if body.startswith(IDLE_NOTIFICATION) else body
        if match := FLUSHED.match(text.strip()):
            replies.append((name, re.split(r"[ ,]+", match[1].strip())))
    return replies


@on(
    Event.UserPromptSubmit,
    skip_if=[FromSubagent()],
    tests={
        Input(
            prompt='Another Claude session sent a message:\n<teammate-message teammate_id="landing-desk">\n'
            "flushed b037decf c2b3a6c\n</teammate-message>",
            state=[RotationState(asks={"alanding-desk-1a1a1a1a1a1a1a1a": [1.0]}, names={"alanding-desk-1a1a1a1a1a1a1a1a": "landing-desk"})],
        ): Allow(),
        Input(prompt="continue"): Allow(),
    },
)
def nudge_flushed(evt: BaseHookEvent) -> HookResult | None:
    if not (replies := flushed_replies(evt.user_prompt or "")):
        return None
    with RotationState.mutate(evt) as state:
        for name, ids in replies:
            asked = [agent_id for agent_id, lane in state.names.items() if lane == name and agent_id not in state.flushed]
            if asked:
                state.flushed.extend(asked)
                queue_nudge(
                    evt,
                    f"lane {name} flushed ({', '.join(ids)}): TaskStop it and respawn it from its handoff note "
                    "at a natural pause",
                )
    return None
