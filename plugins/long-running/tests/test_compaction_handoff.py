from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from captain_hook.app import _state
from captain_hook.events import PostToolUseEvent, SessionStartEvent, StopEvent, UserPromptSubmitEvent
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


@pytest.mark.parametrize(
    "prompt",
    ["/long-running:long-running Continue the plan at ~/.claude/plans/x.md", "/long-running `~/.claude/plans/x.md`"],
)
def test_slash_command_records_plan_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session_dir = tmp_path / "session"
    raw = {"session_id": "0123456789abcdef", "prompt": prompt}
    handoff.activate_on_command(UserPromptSubmitEvent(_raw=raw, ctx=build_context(session_dir=session_dir)))
    saved = handoff.CompactionState.load(stop_event(session_dir))
    assert (saved.active, saved.plan_path) == (True, str(tmp_path / ".claude/plans/x.md"))


@pytest.mark.parametrize(
    ("phase", "kept"),
    [
        ("rewriting", ("rewriting", "/p/brook.2026-09-24-163000-pre-compact.md")),
        ("compacting", ("idle", "/p/brook.2026-09-24-163000-pre-compact.md")),
        ("declined", ("idle", "/p/brook.2026-09-24-163000-pre-compact.md")),
    ],
)
def test_compaction_resets_only_a_finished_handoff(tmp_path: Path, phase: str, kept: tuple) -> None:
    evt = SessionStartEvent(
        _raw={"session_id": "0123456789abcdef", "source": "compact"}, ctx=build_context(session_dir=tmp_path / "session")
    )
    handoff.CompactionState(
        active=True,
        plan_path="/p/brook.md",
        archive_path="/p/brook.2026-09-24-163000-pre-compact.md",
        phase=phase,
    ).save(evt)

    handoff.reground_after_compact(evt)

    saved = handoff.CompactionState.load(evt)
    assert (saved.phase, saved.archive_path) == kept


def test_archive_collision_raises_before_state_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = datetime(2026, 9, 24, 16, 30, 5, tzinfo=UTC)
    monkeypatch.setattr(handoff, "datetime", type("Frozen", (datetime,), {"now": staticmethod(lambda tz: frozen)}))
    plan = tmp_path / "brook.md"
    plan.write_text("# new\n")
    archive = tmp_path / "brook.2026-09-24-163005-pre-compact.md"
    archive.write_text("# first snapshot\n")
    evt = stop_event(tmp_path / "session")
    handoff.CompactionState(active=True, model="claude-opus-5-5[1m]", plan_path=str(plan)).save(evt)

    with pytest.raises(FileExistsError):
        handoff.compaction_handoff(evt)

    assert archive.read_text() == "# first snapshot\n"
    saved = handoff.CompactionState.load(evt)
    assert (saved.phase, saved.archive_path) == ("idle", None)


def test_unrewritten_plan_declines_once_and_never_refires_before_compaction(tmp_path: Path) -> None:
    plan = tmp_path / "brook.md"
    plan.write_text("# brook\n")
    evt = stop_event(tmp_path / "session")
    handoff.CompactionState(active=True, model="claude-opus-5-5[1m]", plan_path=str(plan)).save(evt)

    assert handoff.compaction_handoff(evt).action == "block"
    declined = handoff.compaction_handoff(evt)
    assert (declined.action, declined.system_message.startswith("Long-running compaction handoff skipped")) == (
        "allow",
        True,
    )
    for _ in range(3):
        assert handoff.compaction_handoff(evt) is None
    assert len(list(tmp_path.glob("*-pre-compact.md"))) == 1
    assert handoff.CompactionState.load(evt).phase == "declined"


def calm_stop(session_dir: Path, *tasks: dict) -> StopEvent:
    return stop_event(
        session_dir, transcript_path=str(FIXTURES / "lanes/projects/p/calm.jsonl"), background_tasks=list(tasks)
    )


def deliver(session_dir: Path) -> str | None:
    result = handoff.deliver_nudge(tool_event(session_dir, "Bash", {"command": "ls"}))
    return result.message if result else None


ALL_LANES = (handoff.DESK, handoff.REVIEWER, handoff.WATCHER, handoff.ARCHIVIST, handoff.SLEEPER)


def test_rotation_nudges_top_lanes_once_and_never_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1_790_000_000.0]
    monkeypatch.setattr(handoff, "time", type("Clock", (), {"time": staticmethod(lambda: clock[0])}))
    session = tmp_path / "session"
    evt = calm_stop(session, *ALL_LANES)
    handoff.CompactionState(active=True, rotated={"gone": 0.0}, overdue=["gone"]).save(evt)

    assert handoff.compaction_handoff(evt) is None
    assert deliver(session) == (
        "Lane rotation (advisory): `archivist` (440,000), `landing-desk` (430,000), `reviewer` (420,000) passed the "
        "400,000-token rotation line. When each reaches a natural pause, rotate it per the long-running skill's Lane "
        "rotation protocol, starting with its ROTATE message; a lane that has not replied `flushed` keeps running. "
        "The hook names at most 3 lanes per nudge and repeats a lane at most once, 30 minutes later."
    )
    assert deliver(session) is None
    saved = handoff.CompactionState.load(evt)
    assert (sorted(saved.rotated), saved.overdue) == (
        ["a0d0d0d0d0d0d0d0d", "aarchivist-0j0j0j0j0j0j0j0j", handoff.DESK_ID],
        [],
    )

    clock[0] += handoff.NUDGE_INTERVAL_SECONDS - 1
    assert handoff.compaction_handoff(evt) is None
    assert deliver(session) is None

    clock[0] += 1
    assert handoff.compaction_handoff(evt) is None
    assert deliver(session).startswith("Lane rotation (advisory): `pr-watch-9` (410,000) passed")

    clock[0] += handoff.ROTATE_GRACE_SECONDS
    assert handoff.compaction_handoff(evt) is None
    assert deliver(session).startswith(
        "Lane rotation (advisory): `archivist` (440,000), `landing-desk` (430,000), `reviewer` (420,000) passed"
    )

    clock[0] += handoff.ROTATE_GRACE_SECONDS
    assert handoff.compaction_handoff(evt) is None
    assert deliver(session).startswith("Lane rotation (advisory): `pr-watch-9` (410,000) passed")

    clock[0] += handoff.ROTATE_GRACE_SECONDS
    assert handoff.compaction_handoff(evt) is None
    assert deliver(session) is None
    assert sorted(handoff.CompactionState.load(evt).overdue) == sorted(handoff.CompactionState.load(evt).rotated)


def test_rotation_line_is_configurable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LONG_RUNNING_LANE_ROTATE_TOKENS", "435000")
    session = tmp_path / "session"
    evt = calm_stop(session, *ALL_LANES)
    handoff.CompactionState(active=True).save(evt)

    handoff.compaction_handoff(evt)

    assert deliver(session).startswith(
        "Lane rotation (advisory): `archivist` (440,000) passed the 435,000-token rotation line."
    )


def test_stopped_rotated_lane_is_pruned(tmp_path: Path) -> None:
    session = tmp_path / "session"
    handoff.CompactionState(active=True, rotated={handoff.DESK_ID: 0.0}, overdue=[handoff.DESK_ID]).save(
        calm_stop(session)
    )

    assert handoff.compaction_handoff(calm_stop(session, handoff.REVIEWER)) is None

    saved = handoff.CompactionState.load(calm_stop(session))
    assert (list(saved.rotated), saved.overdue) == (["a0d0d0d0d0d0d0d0d"], [])


def test_dead_and_dormant_lanes_are_never_named(tmp_path: Path) -> None:
    session = tmp_path / "session"
    evt = calm_stop(session, handoff.SLEEPER)
    handoff.CompactionState(active=True).save(evt)

    assert handoff.compaction_handoff(evt) is None
    assert deliver(session) is None
    saved = handoff.CompactionState.load(evt)
    assert (saved.rotated, saved.nudged_at) == ({}, 0.0)


def test_handoff_stop_neither_scans_nor_lists_lanes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    def unreadable(evt: StopEvent) -> list:
        raise OSError("scan")

    monkeypatch.setattr(handoff, "live_lanes", unreadable)
    evt = stop_event(
        tmp_path / "session",
        transcript_path=str(FIXTURES / "lanes/projects/p/full.jsonl"),
        background_tasks=[handoff.DESK],
    )
    handoff.CompactionState(active=True).save(evt)

    result = handoff.compaction_handoff(evt)

    assert result.action == "block"
    assert "landing-desk" not in result.message
    saved = handoff.CompactionState.load(evt)
    assert (saved.phase, saved.rotated, saved.nudge) == ("rewriting", {}, None)


def test_reversed_lines_crosses_block_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handoff, "TAIL_BLOCK", 7)
    path = tmp_path / "t.jsonl"
    lines = [b"first", b"", b"a much longer second line", b"3"]
    path.write_bytes(b"\n".join(lines) + b"\n")

    assert list(handoff.reversed_lines(path)) == [b"", *reversed(lines)]


@pytest.mark.parametrize(
    ("model", "hint", "window"),
    [
        ("claude-fable-5-1", None, 1_000_000),
        ("claude-opus-5-5", "claude-fable-5-1", 1_000_000),
        ("claude-opus-4-7", None, 1_000_000),
        ("claude-sonnet-5", None, 1_000_000),
        ("claude-opus-4-6", None, 200_000),
        ("claude-opus-4-20250514", None, 200_000),
        ("claude-opus-4-1-20250805", None, 200_000),
        ("claude-sonnet-4-5-20250929", None, 200_000),
        ("claude-haiku-4-5-20251001", None, 200_000),
        ("claude-3-5-sonnet-20241022", None, 200_000),
        ("claude-sonnet-4-6", "claude-sonnet-4-6[1m]", 1_000_000),
        ("claude-sonnet-4-6", "claude-opus-5-5[1m]", 200_000),
        ("gpt-5", None, 200_000),
    ],
)
def test_model_window(model: str, hint: str | None, window: int) -> None:
    assert handoff.model_window(model, hint) == window
