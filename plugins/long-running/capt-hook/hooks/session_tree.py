from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from cc_transcript import parse
from cc_transcript.discovery import subagent_paths
from cc_transcript.models import TranscriptEvent

from captain_hook import BaseHookEvent
from captain_hook.tasks import Task
from captain_hook.util import reqenv

__capt_hook_skip__ = True

STAND_DOWN = re.compile(r"\s*(?:[^.;:!?\n]{1,40}:\s*)?stand[\s-]?down\b", re.IGNORECASE)
TEAMMATE_MESSAGE = re.compile(r'<teammate-message teammate_id="([^"]+)"[^>]*>\n(.*?)\n</teammate-message>', re.DOTALL)
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


def lane_of(task: Task) -> str | None:
    if task.owner and task.owner not in ROOT_NAMES:
        return task.owner
    return match[1] if (match := LANE_IN_SUBJECT.search(task.subject)) else None


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


def orders_stand_down(text: str) -> bool:
    return STAND_DOWN.match(text) is not None


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


def last_uuid(events: Sequence[TranscriptEvent]) -> str | None:
    return next((str(event.meta.uuid) for event in reversed(events) if hasattr(event, "meta")), None)


def events_after(events: Sequence[TranscriptEvent], cursor: str | None) -> tuple[list[TranscriptEvent], str | None]:
    seen = [i for i, event in enumerate(events) if hasattr(event, "meta") and str(event.meta.uuid) == cursor]
    fresh = list(events[seen[0] + 1 :]) if seen else list(events) if cursor else []
    return fresh, last_uuid(events) or cursor
