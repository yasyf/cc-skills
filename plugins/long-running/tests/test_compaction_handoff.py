from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from captain_hook.app import _state
from captain_hook.events import PostToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context, matches_conditions

HOOK = Path(__file__).resolve().parents[1] / "capt-hook" / "hooks" / "compaction_handoff.py"
FIXTURES = HOOK.parent / "tests" / "fixtures"

spec = importlib.util.spec_from_file_location("compaction_handoff", HOOK)
handoff = sys.modules["compaction_handoff"] = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


def test_rewritten_plan_types_compact_through_orca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = FIXTURES / "plans" / "changed.md"
    evt = StopEvent(_raw={"session_id": "0123456789abcdef"}, ctx=build_context(session_dir=tmp_path / "session"))
    handoff.CompactionState(
        active=True,
        plan_path=str(plan),
        archive_path=str(FIXTURES / "plans" / "changed.2026-09-24-1630-pre-compact.md"),
        phase="rewriting",
    ).save(evt)
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term-7")
    monkeypatch.setenv("ORCA_USER_DATA_PATH", "/orca/data")
    spawned = []
    monkeypatch.setattr(handoff.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)))

    assert handoff.compaction_handoff(evt) is None

    [(argv, kw)] = spawned
    assert argv[:2] == ["sh", "-c"]
    assert argv[2] == (
        'orca terminal wait --terminal "$1" --for tui-idle --timeout-ms 120000 '
        '&& orca terminal send --terminal "$1" --text "$2" --enter'
    )
    assert argv[4:] == [
        "term-7",
        f"/compact Long-running compaction handoff. `{plan}` is the authoritative restart state; "
        "keep only in-flight details from the last turn that it lacks.",
    ]
    assert kw["env"]["ORCA_USER_DATA_PATH"] == "/orca/data"
    assert kw["start_new_session"]
    assert kw["stdin"] is kw["stdout"] is kw["stderr"] is subprocess.DEVNULL
    assert handoff.CompactionState.load(evt).phase == "compacting"


def stop_event(session_dir: Path, **raw) -> StopEvent:
    payload = {
        "session_id": "0123456789abcdef",
        "transcript_path": str(FIXTURES / "usage-460k.jsonl"),
        "cwd": str(FIXTURES / "project-600k"),
    } | raw
    return StopEvent(_raw=payload, ctx=build_context(session_dir=session_dir))


def tool_event(session_dir: Path, tool_name: str, tool_input: dict, **raw) -> PostToolUseEvent:
    payload = {"session_id": "0123456789abcdef", "tool_name": tool_name, "tool_input": tool_input} | raw
    return PostToolUseEvent(_raw=payload, ctx=build_context(session_dir=session_dir))


@pytest.mark.parametrize(
    ("handler", "tool_name", "tool_input"),
    [
        ("activate_on_skill", "Skill", {"skill": "long-running"}),
        ("track_plan", "Write", {"file_path": "/home/u/.claude/plans/other.md", "content": "# other"}),
    ],
)
def test_subagent_tool_events_are_skipped(tmp_path: Path, handler: str, tool_name: str, tool_input: dict) -> None:
    entry = next(h for h in _state.hooks if h.handler is getattr(handoff, handler))
    assert not matches_conditions(entry.spec, tool_event(tmp_path, tool_name, tool_input, agent_id="a1b2c3"))
    assert matches_conditions(entry.spec, tool_event(tmp_path, tool_name, tool_input))


@pytest.mark.parametrize("args", ["resume `~/.claude/plans/x.md` now", "resume '~/.claude/plans/x.md'", '"~/.claude/plans/x.md"'])
def test_quoted_plan_arg_records_bare_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: str) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session_dir = tmp_path / "session"
    handoff.activate_on_skill(tool_event(session_dir, "Skill", {"skill": "long-running", "args": args}))
    assert handoff.CompactionState.load(stop_event(session_dir)).plan_path == str(tmp_path / ".claude/plans/x.md")


def test_archive_collision_raises_before_state_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = datetime(2026, 9, 24, 16, 30, tzinfo=UTC)
    monkeypatch.setattr(handoff, "datetime", type("Frozen", (), {"now": staticmethod(lambda tz: frozen)}))
    plan = tmp_path / "brook.md"
    plan.write_text("# new\n")
    archive = tmp_path / "brook.2026-09-24-1630-pre-compact.md"
    archive.write_text("# first snapshot\n")
    evt = stop_event(tmp_path / "session")
    handoff.CompactionState(active=True, model="claude-opus-5-5[1m]", plan_path=str(plan)).save(evt)

    with pytest.raises(FileExistsError):
        handoff.compaction_handoff(evt)

    assert archive.read_text() == "# first snapshot\n"
    saved = handoff.CompactionState.load(evt)
    assert (saved.phase, saved.archive_path) == ("idle", None)
