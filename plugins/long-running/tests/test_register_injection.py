from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import PostToolUseEvent, SessionStartEvent, SubagentStartEvent
from captain_hook.testing.helpers import build_context

from hooks import compaction_handoff as handoff
from hooks import register_injection as injection

FIXTURES = Path(handoff.__file__).parent / "tests" / "fixtures"
TRANSCRIPT = str(FIXTURES / "usage-460k.jsonl")
SESSION = "0123456789abcdef"
REGISTER_ID = "0cf17c9" + "0" * 33
REGISTER_BODY = "# brook register\n\n1. Pulumi state is the only truth (ec2881e).\n"
FAKE_CCN = f"""#!/usr/bin/env python3
import json, sys
args = sys.argv[3:]
if args[:2] == ["doc", "list"]:
    print(json.dumps([{{"id": "{REGISTER_ID}", "updated_at": "2026-10-05T00:00:00Z"}}]))
if args[:2] == ["doc", "show"]:
    print(json.dumps({{"id": args[2], "body": {REGISTER_BODY!r}}}))
"""
@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(injection.DRIVE_ENV, raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("ccn", FAKE_CCN),):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return tmp_path


def raw(**fields: object) -> dict:
    return {"session_id": SESSION, "transcript_path": TRANSCRIPT, "cwd": str(FIXTURES / "project-600k")} | fields


def ctx(session: Path):
    return build_context(transcript=Session.from_path(Path(TRANSCRIPT)), session_dir=session)


def start(session: Path, agent_id: str, agent_type: str = "long-running:lane"):
    return injection.brief_subagent(SubagentStartEvent(_raw=raw(agent_id=agent_id, agent_type=agent_type), ctx=ctx(session)))


def fenced(message: str) -> str:
    header, _, rest = message.partition(f"\n{handoff.REGISTER_FENCE}\n")
    body, end, tail = rest.partition(handoff.REGISTER_FENCE)
    assert end and not tail
    return body


def test_every_subagent_lane_receives_the_register_once_at_start(tools: Path) -> None:
    session = tools / "session"
    handoff.CompactionState(active=True, slug="brook").save(PostToolUseEvent(_raw=raw(tool_name="Bash", tool_input={}), ctx=ctx(session)))

    for agent_type in ("long-running:lane", "general-purpose", "Explore"):
        started = start(session, "a1", agent_type)
        assert started.message.startswith("Standing rules register `0cf17c9`, verbatim;")
        assert fenced(started.message) == REGISTER_BODY


def test_a_register_over_the_injection_budget_is_named_not_truncated(tools: Path) -> None:
    event = SubagentStartEvent(_raw=raw(agent_id="a9", agent_type="Explore"), ctx=ctx(tools / "session"))
    result = handoff.register_context(event, {"id": REGISTER_ID, "body": "1. rule\n" * 2000})

    assert result.message == (
        "Standing rules register `0cf17c9` is over the injection budget and binds this session; "
        "read it in full with `ccn doc show 0cf17c9` before acting."
    )


def test_a_session_outside_any_drive_injects_nothing(tools: Path) -> None:
    assert start(tools / "session", "a1") is None


def test_an_orca_worker_receives_the_register_once_at_start(tools: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    drives = tools / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "state_dir": str(tools / ".claude" / "scratch" / "brook")}))
    monkeypatch.setenv(injection.DRIVE_ENV, "d1")

    started = injection.brief_drive_worker(SessionStartEvent(_raw=raw(source="startup"), ctx=ctx(tools / "worker")))

    assert fenced(started.message) == REGISTER_BODY
