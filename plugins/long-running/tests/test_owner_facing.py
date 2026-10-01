from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from cc_transcript.query import Session
from captain_hook.events import PostToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context

from hooks import nudges, owner_facing
from hooks.compaction_handoff import CompactionState


def entry(kind: str, text: str, *, sidechain: bool = False) -> dict:
    message = {"role": kind, "content": [{"type": "text", "text": text}]}
    if kind == "assistant":
        usage = {"input_tokens": 2, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        message |= {"model": "claude-opus-5-5", "usage": usage}
    return {
        "type": kind,
        "isSidechain": sidechain,
        "timestamp": "2026-10-01T12:00:00.000Z",
        "uuid": str(uuid.uuid4()),
        "parentUuid": None,
        "sessionId": "0123456789abcdef",
        "message": message,
    }


class Root:
    def __init__(self, home: Path, *, active: bool = True) -> None:
        self.transcript = home / "root.jsonl"
        self.session_dir = home / "state"
        seed = PostToolUseEvent(
            _raw={"session_id": "0123456789abcdef", "tool_name": "Bash"}, ctx=build_context(session_dir=self.session_dir)
        )
        CompactionState(active=active).save(seed)

    def event(self, cls, **raw):
        payload = {"session_id": "0123456789abcdef", "transcript_path": str(self.transcript), "cwd": str(self.transcript.parent)}
        transcript = Session.from_path(self.transcript)
        return cls(_raw=payload | raw, ctx=build_context(transcript=transcript, session_dir=self.session_dir))

    def reply(self, *entries: dict) -> list[str]:
        self.transcript.write_text("".join(json.dumps(item) + "\n" for item in entries))
        evt = self.event(StopEvent)
        owner_facing.nudge_utc_in_owner_replies(evt)
        with nudges.NudgeState.mutate(evt) as state:
            pending, state.pending = state.pending, []
        return pending


@pytest.fixture
def root(tmp_path: Path) -> Root:
    return Root(tmp_path)


@pytest.mark.parametrize(
    "text",
    ["Fix lane spawned at 17:35Z; mechanism by 17:45Z.", "Owner asks landed 17:3xZ.", "Red since 16:52 UTC."],
)
def test_utc_times_in_the_last_reply_nudge(root: Root, text: str) -> None:
    assert root.reply(entry("user", "status?"), entry("assistant", text)) == [owner_facing.UTC_IN_REPLY]


@pytest.mark.parametrize("text", ["Fix lane spawned at 10:35am; mechanism by 10:45.", "Build 72755 is red."])
def test_pacific_or_timeless_replies_are_quiet(root: Root, text: str) -> None:
    assert root.reply(entry("assistant", text)) == []


def test_only_the_root_reply_counts(root: Root) -> None:
    assert root.reply(entry("assistant", "Pacific 10:35am."), entry("assistant", "lane at 17:35Z", sidechain=True)) == []


def test_outside_a_drive_is_quiet(tmp_path: Path) -> None:
    assert Root(tmp_path, active=False).reply(entry("assistant", "at 17:35Z")) == []
