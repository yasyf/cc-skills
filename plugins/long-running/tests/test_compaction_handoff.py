from __future__ import annotations

import json
import os
import subprocess
import sys
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
    payload = {
        "session_id": SESSION,
        "transcript_path": str(FIXTURES / "usage-460k.jsonl"),
        "cwd": str(FIXTURES / "project-600k"),
    } | raw
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
        ("nudge_at_threshold", "Bash", {"command": "ls"}),
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
    raw = {"session_id": SESSION, "prompt": prompt, "cwd": str(FIXTURES / "project-600k")}
    handoff.activate_on_command(UserPromptSubmitEvent(_raw=raw, ctx=build_context(session_dir=session)))
    saved = state(session)
    assert (saved.active, saved.plan_path) == (True, str(home / ".claude/plans/x.md"))


FAKE_CCN = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_CCN"]
args = sys.argv[3:]
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
if args[:2] == ["doc", "list"]:
    print(open(os.path.join(state, "docs.json")).read())
"""


@pytest.fixture
def docs(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = home / "ccn"
    bin_dir = home / "bin"
    state_dir.mkdir()
    bin_dir.mkdir()
    (bin_dir / "ccn").write_text(FAKE_CCN)
    (bin_dir / "ccn").chmod(0o755)
    (state_dir / "docs.json").write_text("[]")
    monkeypatch.setenv("FAKE_CCN", str(state_dir))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return state_dir


def ccn_calls(docs: Path) -> list[list[str]]:
    path = docs / "calls"
    return [json.loads(line)[2:] for line in path.read_text().splitlines()] if path.exists() else []


def doc(doc_id: str, updated: str) -> dict:
    return {"id": doc_id, "title": f"brook: progress {updated}", "tags": ["progress:brook"], "updated_at": updated}


def test_threshold_queues_one_doc_nudge_without_touching_the_plan(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))

    for _ in range(3):
        assert handoff.nudge_at_threshold(bash(session)) is None
        assert handoff.compact_when_idle(stop_event(session)) is None

    assert plan.read_text() == "# brook\n"
    assert list(plan.parent.iterdir()) == [plan]
    saved = state(session)
    assert (saved.phase, saved.store, saved.slug) == ("due", "ccn", "brook")
    [nudge] = pending(session)
    assert nudge.startswith(
        "Context is at 460,000 of the 567,000-token auto-compaction threshold (81%). When convenient, write the "
        'drive\'s whole execution state as a new cc-notes doc: `ccn doc add "<drive>: progress '
    )
    assert "--label progress:brook" in nudge
    assert "with sections: how the drive runs; owner asks and state; lanes and binding rulings; landed; " in nudge
    assert f"Never rewrite `{plan}`." in nudge
    assert nudges.deliver_nudge(bash(session)).message == nudge
    assert pending(session) == []


def test_the_slug_comes_from_the_plans_progress_line(home: Path, plan: Path, docs: Path) -> None:
    plan.write_text(f"# brook\n\n{handoff.POINTER_PREFIX} the active doc labelled `progress:release-v3`, now `d473abdd`.\n")
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))

    handoff.nudge_at_threshold(bash(session))

    assert state(session).slug == "release-v3"
    assert "--label progress:release-v3" in pending(session)[0]


def test_a_new_doc_supersedes_the_old_one_and_repoints_the_plan_once(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z")]))
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))
    handoff.nudge_at_threshold(bash(session))
    assert state(session).prior == ["a" * 40]

    assert handoff.compact_when_idle(stop_event(session)) is None
    assert state(session).phase == "due"
    assert plan.read_text() == "# brook\n"

    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z"), doc("b" * 40, "2026-09-30T05:44:08Z")]))
    result = handoff.compact_when_idle(stop_event(session))

    assert result.system_message.startswith("Long-running progress for ")
    assert ["doc", "supersede", "a" * 40, "--by", "b" * 40] in ccn_calls(docs)
    assert pending(session)
    lines = plan.read_text().splitlines()
    assert lines[:2] == ["# brook", ""]
    assert lines[2].startswith(handoff.POINTER_PREFIX) and "now `bbbbbbbb`" in lines[2]

    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due", prior=["b" * 40]).save(
        bash(session)
    )
    (docs / "docs.json").write_text(json.dumps([doc("b" * 40, "2026-09-30T05:44:08Z"), doc("c" * 40, "2026-09-30T07:00:00Z")]))
    handoff.compact_when_idle(stop_event(session))
    [pointer] = [line for line in plan.read_text().splitlines() if line.startswith(handoff.POINTER_PREFIX)]
    assert "now `cccccccc`" in pointer
    assert ["doc", "supersede", "b" * 40, "--by", "c" * 40] in ccn_calls(docs)
    assert plan.read_text().startswith("# brook\n\n")


def test_a_failed_supersede_keeps_the_handoff_due(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due", prior=["a" * 40]).save(
        bash(session)
    )
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z"), doc("b" * 40, "2026-09-30T05:44:08Z")]))
    ccn = Path(os.environ["PATH"].split(":")[0]) / "ccn"
    ccn.write_text(ccn.read_text() + 'sys.exit(1 if args[:2] == ["doc", "supersede"] else 0)\n')

    assert handoff.compact_when_idle(stop_event(session)) is None

    assert state(session).phase == "due"
    assert plan.read_text() == "# brook\n"


def test_without_cc_notes_the_record_is_a_sibling_folder(home: Path, plan: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))

    handoff.nudge_at_threshold(bash(session))

    saved = state(session)
    assert saved.store == "folder"
    folder = plan.with_name("brook-progress")
    assert f"to a new file `{folder}/" in pending(session)[0]
    folder.mkdir()
    (folder / "2026-09-30T0544Z.md").write_text("# state\n")

    handoff.compact_when_idle(stop_event(session))

    assert state(session).phase == "compacting"
    assert plan.read_text().splitlines()[-1] == (
        f"{handoff.POINTER_PREFIX} the latest execution state is the newest file in `{folder}/`, now "
        "`2026-09-30T0544Z.md`; only this line's name changes."
    )


def test_fable_without_suffix_uses_the_configured_600k_window(home: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, model="claude-fable-5-1").save(bash(session))

    handoff.nudge_at_threshold(bash(session, transcript_path=str(FIXTURES / "usage-262k-fable-5-1.jsonl")))

    assert (state(session).phase, pending(session)) == ("idle", [])


def test_legacy_model_fires_at_the_200k_window(home: Path, docs: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True).save(bash(session))

    handoff.nudge_at_threshold(bash(session, transcript_path=str(FIXTURES / "usage-170k-sonnet-4-6.jsonl")))

    saved = state(session)
    assert (saved.phase, saved.plan_path) == ("due", str(home / ".claude/plans/long-running-01234567.md"))
    assert saved.slug == "long-running-01234567"
    [nudge] = pending(session)
    assert nudge.startswith("Context is at 170,000 of the 167,000-token auto-compaction threshold (102%). ")


def test_plan_write_only_tracks_the_path(home: Path, plan: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, phase="due").save(bash(session))

    handoff.track_plan(tool_event(session, "Write", {"file_path": str(plan), "content": "# brook"}))

    assert plan.read_text() == "# brook\n"
    assert (state(session).phase, state(session).plan_path) == ("due", str(plan))


def test_stop_after_the_record_spawns_one_compact_job_and_retries_after_30_minutes(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1_790_000_000.0]
    monkeypatch.setattr(handoff, "time", type("Clock", (), {"time": staticmethod(lambda: clock[0])}))
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term-7")
    monkeypatch.setenv("ORCA_USER_DATA_PATH", "/orca/data")
    spawned = []
    monkeypatch.setattr(handoff.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)))
    session = home / "session"
    handoff.CompactionState(active=True, plan_path="/p/brook.md", slug="brook", phase="written").save(bash(session))

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
        "/compact Long-running compaction handoff. `/p/brook.md` and its progress record are the authoritative "
        "restart state: read the plan, then the progress doc: `ccn doc list --label progress:brook`, then "
        "`ccn doc show <id>`. Keep only in-flight details from the last turn that they lack.",
        str(FIXTURES / "usage-460k.jsonl"),
    ]
    assert kw["env"]["ORCA_USER_DATA_PATH"] == "/orca/data"
    assert kw["start_new_session"]
    assert kw["stdin"] is kw["stdout"] is kw["stderr"] is subprocess.DEVNULL
    assert (state(session).phase, state(session).compacting_since) == ("compacting", clock[0])


def test_stop_without_orca_tells_the_owner_once(home: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path="/p/brook.md", slug="brook", phase="written").save(bash(session))

    first = handoff.compact_when_idle(stop_event(session))

    assert (first.action, first.system_message.startswith("Long-running progress for `/p/brook.md` is recorded")) == (
        "allow",
        True,
    )
    assert handoff.compact_when_idle(stop_event(session)) is None


@pytest.mark.parametrize("phase", ["due", "written", "compacting", "idle"])
def test_compaction_resets_the_handoff(tmp_path: Path, phase: str) -> None:
    evt = SessionStartEvent(
        _raw={"session_id": SESSION, "source": "compact", "cwd": str(FIXTURES / "project-600k")}, ctx=build_context(session_dir=tmp_path / "session")
    )
    handoff.CompactionState(
        active=True, plan_path="/p/brook.md", phase=phase, compacting_since=1.0 if phase == "compacting" else None
    ).save(evt)

    handoff.reground_after_compact(evt)

    saved = handoff.CompactionState.load(evt)
    assert (saved.phase, saved.compacting_since) == ("idle", None)


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
