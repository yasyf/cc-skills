from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cc_transcript import UserEvent, parse
from cc_transcript.discovery import subagent_paths
from cc_transcript.models import TranscriptEvent

from captain_hook import BaseHookEvent
from captain_hook.tasks import Task, Tasks
from captain_hook.util import reqenv

__capt_hook_skip__ = True

UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")
STAND_DOWN = re.compile(r"\s*(?:[^.;:!?\n]{1,40}:\s*)?stand[\s-]?down\b", re.IGNORECASE)
STAND_DOWN_SENDER = "team-lead"
TEAMMATE_MESSAGE = re.compile(r'<teammate-message teammate_id="([^"]+)"[^>]*>\n(.*?)\n</teammate-message>', re.DOTALL)
IDLE_NOTIFICATION = '{"type":"idle_notification"'
ROOT_NAMES = frozenset({"team-lead", "main"})
LANE_IN_SUBJECT = re.compile(r"\blane `?([\w.-]+)")
LOCK_STALE_SECONDS = 10
LOCK_RETRIES = 10
LOCK_MIN_DELAY = 0.005
LOCK_MAX_DELAY = 0.1


@dataclass(frozen=True)
class Subagent:
    agent_id: str
    meta: dict
    transcript: Path

    @property
    def name(self) -> str | None:
        return self.meta.get("name")

    def read_events(self) -> Sequence[TranscriptEvent]:
        return parse(self.transcript).events


def started(events: Sequence[TranscriptEvent]) -> datetime | None:
    return next((event.meta.timestamp for event in events if hasattr(event, "meta")), None)


def lane_of(task: Task) -> str | None:
    if task.owner and task.owner not in ROOT_NAMES:
        return task.owner
    return match[1] if (match := LANE_IN_SUBJECT.search(task.subject)) else None


def mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def covered(tasks: Tasks, name: str) -> bool:
    return any(
        lane_of(task) == name or mentions(task.subject, name) or mentions(task.description, name) for task in tasks.open
    )


def subagents(evt: BaseHookEvent) -> list[Subagent]:
    return [
        Subagent(path.stem.removeprefix("agent-"), json.loads(meta.read_text()), path)
        for path in subagent_paths(evt.ctx.t.path)
        if (meta := path.with_suffix(".meta.json")).is_file()
    ]


def drive_lane(evt: BaseHookEvent, skip: Callable[[str | None], bool] = lambda _: False) -> str | None:
    """The drive lane `evt` runs in: an Orca lane session's own lane, which owns every worker it spawns, else the outermost named agent above it, so a lane's named worker acts as its lane."""
    if lane := reqenv.getenv("CLAUDE_LONG_RUNNING_LANE"):
        return lane
    if not evt.agent_id or not (transcript := evt._raw.get("transcript_path")):
        return None
    lanes = Path(transcript).with_suffix("") / "subagents"
    agent_id, outermost = evt.agent_id, None
    while agent_id and (meta := lanes / f"agent-{agent_id}.meta.json").is_file():
        fields = json.loads(meta.read_text())
        if (name := fields.get("name")) and not skip(fields.get("agentType")):
            outermost = name
        agent_id = fields.get("parentAgentId")
    return outermost


def team_dir(evt: BaseHookEvent, team: str) -> Path:
    return evt.ctx.t.path.parents[2] / "teams" / UNSAFE_NAME.sub("-", team)


def team_members(evt: BaseHookEvent, team: str) -> dict[str, str]:
    config = team_dir(evt, team) / "config.json"
    if not config.is_file():
        return {}
    return {member["name"]: member["agentId"] for member in json.loads(config.read_text())["members"]}


def inbox_path(evt: BaseHookEvent, team: str, lane: str) -> Path:
    return team_dir(evt, team) / "inboxes" / f"{UNSAFE_NAME.sub('-', lane)}.json"


def orders_stand_down(text: str) -> bool:
    return STAND_DOWN.match(text) is not None


def is_stand_down(sender: str, text: str) -> bool:
    return sender == STAND_DOWN_SENDER and orders_stand_down(text)


def stood_down(inbox: Path) -> bool:
    try:
        messages = json.loads(inbox.read_text() or "[]")
    except (OSError, ValueError):
        return False
    return any(is_stand_down(message["from"], message["text"]) for message in messages)


def stand_down_delivered(events: Sequence[TranscriptEvent]) -> bool:
    return any(
        is_stand_down(sender, body)
        for event in events
        if isinstance(event, UserEvent)
        for sender, body in TEAMMATE_MESSAGE.findall(event.text)
    )


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
        text = inbox.read_text()
        messages = json.loads(text) if text.strip() else []
        staged = inbox.with_name(f"{inbox.name}.tmp.{secrets.token_hex(4)}")
        staged.write_text(json.dumps([*messages, message], indent=2, ensure_ascii=False))
        staged.replace(inbox)
    finally:
        lock.rmdir()
    return True


def last_uuid(events: Sequence[TranscriptEvent]) -> str | None:
    return next((str(event.meta.uuid) for event in reversed(events) if hasattr(event, "meta")), None)


def events_after(events: Sequence[TranscriptEvent], cursor: str | None) -> tuple[list[TranscriptEvent], str | None]:
    seen = [i for i, event in enumerate(events) if hasattr(event, "meta") and str(event.meta.uuid) == cursor]
    fresh = list(events[seen[0] + 1 :]) if seen else list(events) if cursor else []
    return fresh, last_uuid(events) or cursor
