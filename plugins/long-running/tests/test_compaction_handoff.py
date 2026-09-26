from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from captain_hook.app import _state
from captain_hook.events import PostToolUseEvent, SessionStartEvent, StopEvent, UserPromptSubmitEvent
from captain_hook.testing.helpers import build_context, matches_conditions

from hooks import compact_job, nudges
from hooks import compaction_handoff as handoff

FIXTURES = Path(handoff.__file__).parent / "tests" / "fixtures"
SESSION = "0123456789abcdef"


def stop_event(session_dir: Path, **raw) -> StopEvent:
    payload = {"session_id": SESSION, "transcript_path": str(FIXTURES / "usage-460k.jsonl")} | raw
    return StopEvent(_raw=payload, ctx=build_context(session_dir=session_dir))


def tool_event(session_dir: Path, tool_name: str, tool_input: dict, **raw) -> PostToolUseEvent:
    payload = {
        "session_id": SESSION,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "transcript_path": str(FIXTURES / "usage-460k.jsonl"),
        "cwd": str(FIXTURES / "project-600k"),
    } | raw
    return PostToolUseEvent(_raw=payload, ctx=build_context(session_dir=session_dir))


def bash(session_dir: Path, **raw) -> PostToolUseEvent:
    return tool_event(session_dir, "Bash", {"command": "ls"}, **raw)


def pending(session_dir: Path) -> list[str]:
    return nudges.NudgeState.load(bash(session_dir)).pending


def state(session_dir: Path) -> handoff.CompactionState:
    return handoff.CompactionState.load(bash(session_dir))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "ORCA_TERMINAL_HANDLE"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


@pytest.fixture
def plan(home: Path) -> Path:
    path = home / ".claude" / "plans" / "brook.md"
    path.parent.mkdir(parents=True)
    path.write_text("# brook\n")
    return path


@pytest.mark.parametrize(
    ("handler", "tool_name", "tool_input"),
    [
        ("activate_on_skill", "Skill", {"skill": "long-running"}),
        ("track_plan", "Write", {"file_path": "/home/u/.claude/plans/other.md", "content": "# other"}),
        ("archive_at_threshold", "Bash", {"command": "ls"}),
    ],
)
def test_subagent_tool_events_are_skipped(tmp_path: Path, handler: str, tool_name: str, tool_input: dict) -> None:
    entry = next(h for h in _state.hooks if h.handler is getattr(handoff, handler))
    assert not matches_conditions(entry.spec, tool_event(tmp_path, tool_name, tool_input, agent_id="a1b2c3"))
    assert matches_conditions(entry.spec, tool_event(tmp_path, tool_name, tool_input))


@pytest.mark.parametrize("args", ["resume `~/.claude/plans/x.md` now", "resume '~/.claude/plans/x.md'", '"~/.claude/plans/x.md"'])
def test_quoted_plan_arg_records_bare_path(home: Path, args: str) -> None:
    session = home / "session"
    handoff.activate_on_skill(tool_event(session, "Skill", {"skill": "long-running", "args": args}))
    assert state(session).plan_path == str(home / ".claude/plans/x.md")


@pytest.mark.parametrize(
    "prompt",
    ["/long-running:long-running Continue the plan at ~/.claude/plans/x.md", "/long-running `~/.claude/plans/x.md`"],
)
def test_slash_command_records_plan_path(home: Path, prompt: str) -> None:
    session = home / "session"
    raw = {"session_id": SESSION, "prompt": prompt}
    handoff.activate_on_command(UserPromptSubmitEvent(_raw=raw, ctx=build_context(session_dir=session)))
    saved = state(session)
    assert (saved.active, saved.plan_path) == (True, str(home / ".claude/plans/x.md"))


def test_threshold_archives_the_plan_and_queues_one_nudge_without_blocking(home: Path, plan: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))

    for _ in range(3):
        assert handoff.archive_at_threshold(bash(session)) is None
        assert handoff.compact_when_idle(stop_event(session)) is None

    [archive] = plan.parent.glob("brook.*-pre-compact.md")
    assert archive.read_text() == "# brook\n"
    saved = state(session)
    assert (saved.phase, saved.archive_path) == ("archived", str(archive))
    [nudge] = pending(session)
    assert nudge == (
        "Context is at 460,000 of the 567,000-token auto-compaction threshold (81%). When convenient, rewrite "
        f"`{plan}` as the current restart state; the previous version is archived at `{archive}`. The hook links "
        "the archives into it and runs /compact once the input line is empty."
    )
    assert nudges.deliver_nudge(bash(session)).message == nudge
    assert pending(session) == []
    assert nudges.deliver_nudge(bash(session)) is None


def test_fable_without_suffix_uses_the_configured_600k_window(home: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, model="claude-fable-5-1").save(bash(session))

    handoff.archive_at_threshold(bash(session, transcript_path=str(FIXTURES / "usage-262k-fable-5-1.jsonl")))

    assert (state(session).phase, pending(session)) == ("idle", [])


def test_legacy_model_fires_at_the_200k_window(home: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True).save(bash(session))

    handoff.archive_at_threshold(bash(session, transcript_path=str(FIXTURES / "usage-170k-sonnet-4-6.jsonl")))

    saved = state(session)
    assert (saved.phase, saved.plan_path) == ("archived", str(home / ".claude/plans/long-running-01234567.md"))
    [nudge] = pending(session)
    assert nudge.startswith("Context is at 170,000 of the 167,000-token auto-compaction threshold (102%). ")
    assert "archived at" not in nudge


def test_plan_write_links_every_archive_once(home: Path, plan: Path) -> None:
    session = home / "session"
    older = plan.with_name("brook.2026-09-24-163000-pre-compact.md")
    newer = plan.with_name("brook.2026-09-25-220650-pre-compact.md")
    for archive in (older, newer):
        archive.write_text("# old\n")
    plan.write_text(f"# brook\n\n{handoff.ARCHIVE_HEADING}\n- `{older}`\n")
    handoff.CompactionState(active=True, plan_path=str(plan), phase="archived", archive_path=str(newer)).save(
        bash(session)
    )
    write = tool_event(session, "Write", {"file_path": str(plan), "content": "# brook"})

    handoff.track_plan(write)
    handoff.track_plan(write)

    assert plan.read_text() == f"# brook\n\n{handoff.ARCHIVE_HEADING}\n- `{newer}`\n- `{older}`\n"
    assert state(session).phase == "rewritten"


def test_plan_write_appends_the_archive_section(home: Path, plan: Path) -> None:
    session = home / "session"
    archive = plan.with_name("brook.2026-09-25-220650-pre-compact.md")
    archive.write_text("# old\n")
    handoff.CompactionState(active=True, plan_path=str(plan), phase="archived").save(bash(session))

    handoff.track_plan(tool_event(session, "Edit", {"file_path": str(plan), "old_string": "a", "new_string": "b"}))

    assert plan.read_text() == f"# brook\n\n{handoff.ARCHIVE_HEADING}\n- `{archive}`\n"


def test_plan_heading_on_the_last_line_still_gets_links(home: Path, plan: Path) -> None:
    session = home / "session"
    archive = plan.with_name("brook.2026-09-25-220650-pre-compact.md")
    archive.write_text("# old\n")
    plan.write_text(f"# brook\n\n{handoff.ARCHIVE_HEADING}")
    handoff.CompactionState(active=True, plan_path=str(plan), phase="archived").save(bash(session))

    handoff.track_plan(tool_event(session, "Write", {"file_path": str(plan), "content": "# brook"}))

    assert plan.read_text() == f"# brook\n\n{handoff.ARCHIVE_HEADING}\n- `{archive}`\n"


def test_plan_write_identical_to_its_archive_is_not_a_rewrite(home: Path, plan: Path) -> None:
    session = home / "session"
    archive = plan.with_name("brook.2026-09-25-220650-pre-compact.md")
    archive.write_text(plan.read_text())
    handoff.CompactionState(active=True, plan_path=str(plan), phase="archived", archive_path=str(archive)).save(
        bash(session)
    )

    handoff.track_plan(tool_event(session, "Write", {"file_path": str(plan), "content": "# brook"}))

    assert (plan.read_text(), state(session).phase) == ("# brook\n", "archived")


def test_archives_of_another_plan_are_not_linked(home: Path, plan: Path) -> None:
    plan.with_name("brook.v2.2026-09-25-220650-pre-compact.md").write_text("# other\n")
    own = plan.with_name("brook.2026-09-25-220650-pre-compact.md")
    own.write_text("# old\n")

    assert handoff.archives_of(plan) == [own]


def test_missing_plan_file_is_nudged_without_an_archive(home: Path) -> None:
    session = home / "session"
    missing = home / ".claude" / "plans" / "x.md"
    handoff.CompactionState(active=True, plan_path=str(missing)).save(bash(session))

    handoff.archive_at_threshold(bash(session))

    saved = state(session)
    assert (saved.phase, saved.plan_path, saved.archive_path) == ("archived", str(missing), None)
    [nudge] = pending(session)
    assert f"rewrite `{missing}` as the current restart state. " in nudge


def test_plan_write_before_the_threshold_only_tracks_the_path(home: Path, plan: Path) -> None:
    session = home / "session"
    plan.with_name("brook.2026-09-25-220650-pre-compact.md").write_text("# old\n")
    handoff.CompactionState(active=True).save(bash(session))

    handoff.track_plan(tool_event(session, "Write", {"file_path": str(plan), "content": "# brook"}))

    assert plan.read_text() == "# brook\n"
    assert (state(session).phase, state(session).plan_path) == ("idle", str(plan))


def test_stop_after_rewrite_spawns_one_compact_job_and_retries_after_30_minutes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1_790_000_000.0]
    monkeypatch.setattr(handoff, "time", type("Clock", (), {"time": staticmethod(lambda: clock[0])}))
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term-7")
    monkeypatch.setenv("ORCA_USER_DATA_PATH", "/orca/data")
    spawned = []
    monkeypatch.setattr(handoff.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)))
    session = home / "session"
    handoff.CompactionState(active=True, plan_path="/p/brook.md", phase="rewritten").save(bash(session))

    assert handoff.compact_when_idle(stop_event(session)) is None
    clock[0] += handoff.COMPACT_RETRY_SECONDS - 1
    assert handoff.compact_when_idle(stop_event(session)) is None
    assert len(spawned) == 1
    clock[0] += 1
    assert handoff.compact_when_idle(stop_event(session)) is None
    assert len(spawned) == 2

    argv, kw = spawned[0]
    assert argv == [
        sys.executable,
        str(handoff.COMPACT_JOB),
        "term-7",
        "/compact Long-running compaction handoff. `/p/brook.md` is the authoritative restart state; "
        "keep only in-flight details from the last turn that it lacks.",
        str(FIXTURES / "usage-460k.jsonl"),
    ]
    assert kw["env"]["ORCA_USER_DATA_PATH"] == "/orca/data"
    assert kw["start_new_session"]
    assert kw["stdin"] is kw["stdout"] is kw["stderr"] is subprocess.DEVNULL
    assert (state(session).phase, state(session).compacting_since) == ("compacting", clock[0])


def test_stop_without_orca_tells_the_owner_once(home: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path="/p/brook.md", phase="rewritten").save(bash(session))

    first = handoff.compact_when_idle(stop_event(session))

    assert (first.action, first.system_message.startswith("Long-running plan `/p/brook.md` is rewritten")) == (
        "allow",
        True,
    )
    assert handoff.compact_when_idle(stop_event(session)) is None


@pytest.mark.parametrize(
    ("phase", "kept"),
    [("archived", "idle"), ("rewritten", "idle"), ("compacting", "idle"), ("idle", "idle")],
)
def test_compaction_resets_only_a_finished_handoff(tmp_path: Path, phase: str, kept: str) -> None:
    evt = SessionStartEvent(
        _raw={"session_id": SESSION, "source": "compact"}, ctx=build_context(session_dir=tmp_path / "session")
    )
    handoff.CompactionState(
        active=True, plan_path="/p/brook.md", phase=phase, compacting_since=1.0 if phase == "compacting" else None
    ).save(evt)

    handoff.reground_after_compact(evt)

    saved = handoff.CompactionState.load(evt)
    assert (saved.phase, saved.compacting_since) == (kept, None)


def test_archive_collision_raises_before_state_change(
    home: Path, plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = datetime(2026, 9, 24, 16, 30, 5, tzinfo=UTC)
    monkeypatch.setattr(handoff, "datetime", type("Frozen", (datetime,), {"now": staticmethod(lambda tz: frozen)}))
    archive = plan.with_name("brook.2026-09-24-163005-pre-compact.md")
    archive.write_text("# first snapshot\n")
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))

    with pytest.raises(FileExistsError):
        handoff.archive_at_threshold(bash(session))

    assert archive.read_text() == "# first snapshot\n"
    assert (state(session).phase, state(session).archive_path, pending(session)) == ("idle", None, [])


RULE = "─" * 40
EMPTY_BOX = [RULE, "❯", RULE, "  ⏵⏵ bypass permissions on · 1 shell"]


@pytest.mark.parametrize(
    ("terminal", "empty"),
    [
        ({"source": "screen", "tail": ["⏺ done", *EMPTY_BOX]}, True),
        ({"source": "screen", "tail": ["❯ update the plan again", "  ⎿ ok", *EMPTY_BOX]}, True),
        ({"source": "screen", "tail": ["⏺ done", RULE, "❯\xa0", RULE]}, True),
        ({"source": "screen", "tail": [RULE, "❯ half-typed ask", RULE]}, False),
        ({"source": "screen", "tail": [RULE, "❯ first line", "  second line", RULE]}, False),
        ({"source": "screen", "tail": ["❯ 1. My other session owns it", "  2. Go ahead"]}, False),
        ({"source": "screen", "tail": EMPTY_BOX, "draft": "/compact  <optional custom summarization instructions>"}, False),
        ({"source": "screen-unavailable", "tail": EMPTY_BOX}, False),
    ],
)
def test_input_empty_reads_the_rendered_prompt_box(terminal: dict, empty: bool) -> None:
    assert compact_job.input_empty(terminal) is empty


def test_compact_job_stops_once_the_session_compacts(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"assistant"}\n')
    offset = transcript.stat().st_size
    assert not compact_job.compacted_since(transcript, offset)
    with transcript.open("a") as file:
        file.write('{"type":"system","subtype":"compact_boundary","content":"Conversation compacted"}\n')
    assert compact_job.compacted_since(transcript, offset)


def test_retry_outlives_the_previous_job() -> None:
    assert handoff.COMPACT_RETRY_SECONDS > compact_job.MAX_LIFETIME_SECONDS
