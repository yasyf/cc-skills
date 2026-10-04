from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from cc_transcript import parse
from cc_transcript.discovery import subagent_paths
from cc_transcript.models import TranscriptEvent

from captain_hook import BaseHookEvent
from captain_hook.snapshots.client import EvidenceIncomplete
from captain_hook.tasks import Task, Tasks

__capt_hook_skip__ = True

UNSAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]")
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

    @cached_property
    def events(self) -> tuple[TranscriptEvent, ...]:
        return parse(self.transcript).events

    @cached_property
    def started(self):
        return next((event.meta.timestamp for event in self.events if hasattr(event, "meta")), None)


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


def own_name(evt: BaseHookEvent) -> str | None:
    try:
        meta = evt.ctx.t.path.with_suffix(".meta.json")
    except EvidenceIncomplete:
        return None
    return json.loads(meta.read_text()).get("name") if meta.is_file() else None


def team_dir(evt: BaseHookEvent, team: str) -> Path:
    return evt.ctx.t.path.parents[2] / "teams" / UNSAFE_NAME.sub("-", team)


def team_members(evt: BaseHookEvent, team: str) -> dict[str, str]:
    config = team_dir(evt, team) / "config.json"
    if not config.is_file():
        return {}
    return {member["name"]: member["agentId"] for member in json.loads(config.read_text())["members"]}


def inbox_path(evt: BaseHookEvent, team: str, lane: str) -> Path:
    return team_dir(evt, team) / "inboxes" / f"{UNSAFE_NAME.sub('-', lane)}.json"


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
