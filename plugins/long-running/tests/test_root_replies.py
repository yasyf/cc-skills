from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from cc_transcript.query import Session
from captain_hook.events import PostToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context
from captain_hook.transcripts import lazy_transcript

from hooks import root_replies
from hooks.compaction_handoff import CompactionState

UNPARSEABLE = '{"type":"user","uuid":"u1","sessionId":"s1","timestamp":"yesterday","message":{"role":"user","content":"stop all cuts now"}}\n'


@pytest.fixture
def unreadable(tmp_path: Path) -> Callable[..., object]:
    transcript = tmp_path / "root.jsonl"
    transcript.write_text(UNPARSEABLE)
    session_dir = tmp_path / "state"
    raw = {"session_id": "0123456789abcdef", "cwd": str(tmp_path)}
    CompactionState(active=True, coordinator=True).save(PostToolUseEvent(_raw=raw | {"tool_name": "Bash"}, ctx=build_context(session_dir=session_dir)))

    def event(cls: type, **fields: object) -> object:
        ctx = build_context(session_dir=session_dir)
        ctx.transcript = lazy_transcript(transcript, loader=Session.from_path)
        return cls(_raw=raw | fields, ctx=ctx)

    return event


def test_an_unparseable_transcript_passes_a_reply(unreadable: Callable[..., object]) -> None:
    assert root_replies.owner_labels_carry_the_turn_stamp(unreadable(StopEvent)) is None
