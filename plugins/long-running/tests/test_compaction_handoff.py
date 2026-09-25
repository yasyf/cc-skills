from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from captain_hook.events import StopEvent
from captain_hook.testing.helpers import build_context

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
