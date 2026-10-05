from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, SessionStartEvent, SubagentStartEvent, UserPromptSubmitEvent
from captain_hook.testing.helpers import build_context

from hooks import compaction_handoff as handoff
from hooks import register_injection as injection

FIXTURES = Path(handoff.__file__).parent / "tests" / "fixtures"
TRANSCRIPT = str(FIXTURES / "usage-460k.jsonl")
SESSION = "0123456789abcdef"
REGISTER_ID = "0cf17c9" + "0" * 33
REGISTER_BODY = "# brook register\n\n1. Pulumi state is the only truth (ec2881e).\n"
ANSWERS = [
    {"id": "1984bf6" + "0" * 33, "title": "May a migration ship behind a flag?", "body": "No.\nDelete or replace.", "tags": ["scope:durable"]},
    {"id": "ec2881e" + "0" * 33, "title": "Is any ledger a source of truth?", "body": "No: Pulumi state is.", "tags": ["scope:durable"]},
]
FAKE_CCN = f"""#!/usr/bin/env python3
import json, sys
args = sys.argv[3:]
if args[:2] == ["doc", "list"]:
    print(json.dumps([{{"id": "{REGISTER_ID}", "updated_at": "2026-10-05T00:00:00Z"}}]))
if args[:2] == ["doc", "show"]:
    print(json.dumps({{"id": args[2], "body": {REGISTER_BODY!r}}}))
if args[:2] == ["answer", "list"]:
    print(json.dumps({ANSWERS!r}))
"""
FAKE_CCX = """#!/usr/bin/env python3
print("# semantic (native)")
print("ec2881e.md:1-3#abcd (score=0.02)")
print("1984bf6.md:1-3#efgh (score=0.01)")
"""


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(injection.DRIVE_ENV, raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("ccn", FAKE_CCN), ("ccx", FAKE_CCX)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return tmp_path


def raw(**fields: object) -> dict:
    return {"session_id": SESSION, "transcript_path": TRANSCRIPT, "cwd": str(FIXTURES / "project-600k")} | fields


def ctx(session: Path):
    return build_context(transcript=Session.from_path(Path(TRANSCRIPT)), session_dir=session)


def spawn(session: Path, prompt: str, subagent_type: str = "long-running:lane") -> None:
    payload = raw(tool_name="Agent", tool_input={"prompt": prompt, "subagent_type": subagent_type})
    assert injection.match_lane_brief(PreToolUseEvent(_raw=payload, ctx=ctx(session))) is None


def start(session: Path, agent_id: str, agent_type: str = "long-running:lane"):
    return injection.brief_subagent(SubagentStartEvent(_raw=raw(agent_id=agent_id, agent_type=agent_type), ctx=ctx(session)))


def tool(session: Path, agent_id: str | None = None):
    payload = raw(tool_name="Bash", tool_input={"command": "ls"}) | ({"agent_id": agent_id} if agent_id else {})
    return injection.deliver_lane_rulings(PostToolUseEvent(_raw=payload, ctx=ctx(session)))


def started_event(session: Path) -> SubagentStartEvent:
    return SubagentStartEvent(_raw=raw(agent_id="a9", agent_type="Explore"), ctx=ctx(session))


def fenced(message: str) -> str:
    header, _, rest = message.partition(f"\n{handoff.REGISTER_FENCE}\n")
    body, end, tail = rest.partition(handoff.REGISTER_FENCE)
    assert end and not tail
    return body


def test_a_subagent_lane_receives_the_register_then_the_rulings_its_brief_matched(tools: Path) -> None:
    session = tools / "session"
    handoff.CompactionState(active=True, slug="brook").save(PostToolUseEvent(_raw=raw(tool_name="Bash", tool_input={}), ctx=ctx(session)))

    spawn(session, "Lane brief: add a --skip-tenant flag for HSBC.")
    spawn(session, "Lane brief: summarize the census.", subagent_type="Explore")
    started = start(session, "a1")

    assert started.message.startswith("Standing rules register `0cf17c9`, verbatim; it binds this lane")
    assert fenced(started.message) == REGISTER_BODY
    assert tool(session) is None
    assert tool(session, "a2") is None
    delivered = tool(session, "a1")
    assert delivered.message.startswith("Durable owner answers matched to this lane's brief")
    assert fenced(delivered.message) == "- 1984bf6 May a migration ship behind a flag?\n  > No.\n  > Delete or replace.\n"
    assert tool(session, "a1") is None
    assert [entry["type"] for entry in injection.LaneRulings.load(started_event(session)).spawns] == ["Explore"]


def test_a_session_outside_any_drive_injects_nothing(tools: Path) -> None:
    session = tools / "session"

    spawn(session, "Lane brief.")

    assert start(session, "a1") is None
    assert injection.LaneRulings.load(started_event(session)).spawns == []


def test_an_orca_worker_receives_the_register_at_start_and_rulings_for_its_first_prompt(
    tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drives = tools / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "state_dir": str(tools / ".claude" / "scratch" / "brook")}))
    monkeypatch.setenv(injection.DRIVE_ENV, "d1")
    session = tools / "worker"

    started = injection.brief_drive_worker(SessionStartEvent(_raw=raw(source="startup"), ctx=ctx(session)))
    first = injection.match_worker_brief(UserPromptSubmitEvent(_raw=raw(prompt="Lane brief: add a flag."), ctx=ctx(session)))
    second = injection.match_worker_brief(UserPromptSubmitEvent(_raw=raw(prompt="continue"), ctx=ctx(session)))

    assert fenced(started.message) == REGISTER_BODY
    assert fenced(first.message).startswith("- 1984bf6 May a migration ship behind a flag?")
    assert "ec2881e" not in first.message
    assert second is None
