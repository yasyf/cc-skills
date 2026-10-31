from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, SessionStartEvent, SubagentStartEvent
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
if args[:2] == ["doc", "list"] and "--full" in args:
    print(json.dumps([{{"id": "{REGISTER_ID}", "updated_at": "2026-10-05T00:00:00Z", "body": {REGISTER_BODY!r}}}]))
"""
@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(injection.DRIVE_ENV, raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
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


def test_every_subagent_helpers_included_receives_the_register_once_at_start(tools: Path) -> None:
    session = tools / "session"
    drives = tools / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "b7.json").write_text(json.dumps({"drive": "b7", "sessions": [SESSION], "program": "brook", "state_dir": str(tools / ".claude" / "scratch" / "brook")}))
    handoff.CompactionState(active=True, slug="users-plan-stem-of-brook").save(PostToolUseEvent(_raw=raw(tool_name="Bash", tool_input={}), ctx=ctx(session)))

    for agent_type in (
        "long-running:lane",
        "long-running:lane-ship",
        "general-purpose",
        "Explore",
        "Plan",
        "claude-code-guide",
        "cc-context:enumerator",
        "codex:codex-wrapper",
    ):
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
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "program": "brook", "state_dir": str(tools / ".claude" / "scratch" / "brook")}))
    monkeypatch.setenv(injection.DRIVE_ENV, "d1")

    started = injection.brief_drive_worker(SessionStartEvent(_raw=raw(source="startup"), ctx=ctx(tools / "worker")))

    assert fenced(started.message) == REGISTER_BODY


def activated(session: Path) -> Path:
    drives = session.parent / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "b7.json").write_text(json.dumps({"drive": "b7", "sessions": [SESSION], "program": "brook", "state_dir": str(session.parent / ".claude" / "scratch" / "brook")}))
    handoff.CompactionState(active=True, slug="users-plan-stem-of-brook").save(PostToolUseEvent(_raw=raw(tool_name="Bash", tool_input={}), ctx=ctx(session)))
    return session


def spawned(session: Path, tool_name: str, tool_input: dict, **fields: str) -> PreToolUseEvent:
    return PreToolUseEvent(_raw=raw(tool_name=tool_name, tool_input=tool_input, **fields), ctx=ctx(session))


def slicing(calls: list[tuple[str, ...]]):
    def refresh(cwd: str, *args: str, **_: object) -> str:
        calls.append(args)
        brief = Path(args[2])
        brief.write_text(brief.read_text() + "> words\nslice-hash: 0123456789ab\n")
        return f"refreshed {brief} slice-hash none -> 0123456789ab\n"

    return refresh


def test_an_incident_spawn_passes_with_its_slice_and_captures_written_into_the_prompt(tools: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(injection, "rulings", slicing(calls))
    tool_input = {"name": "incident-api-1n80-fix", "prompt": "Fix the api outage."}

    result = injection.slice_on_spawn(spawned(activated(tools / "session"), "Agent", tool_input))

    assert result.updated_input == tool_input | {
        "prompt": "Fix the api outage.\n\nRegister slice: lane=incident-api-1n80-fix step=incident\n> words\nslice-hash: 0123456789ab\n"
    }
    assert [(args[:2], args[3:]) for args in calls] == [(("slice", "--refresh"), ("--drive", "b7"))]


def test_a_sub_lane_a_lane_spawns_is_warned_without_its_slice_and_refreshed_with_one(tools: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(injection, "rulings", lambda cwd, *args, **_: calls.append(args) or "fresh /tmp/brief.md slice-hash 0123456789ab\n")
    monkeypatch.setattr(injection, "judged", lambda *_: None)
    session = activated(tools / "session")
    sliced = "Lane lane-a-sub.\nScale: no N\nRegister slice: lane=lane-a-sub step=1.9\nslice-hash: 0123456789ab\n"

    warned = injection.slice_on_spawn(spawned(session, "Agent", {"name": "lane-a-sub", "prompt": "Lane lane-a-sub: build the tests."}, agent_id="a1b2c3"))
    passed = injection.slice_on_spawn(spawned(session, "Agent", {"name": "lane-a-sub", "prompt": sliced}, agent_id="a1b2c3"))

    assert warned.action.value == "warn" and "has no `Register slice: lane=<name> step=<n>` line" in warned.message
    assert passed is None
    assert [(args[:2], args[3:]) for args in calls] == [(("slice", "--refresh"), ("--drive", "b7"))]


def test_an_incident_launch_by_hand_gets_its_slice_written_into_the_brief(tools: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(injection, "rulings", slicing([]))
    brief = tools / "fix-brief.md"
    brief.write_text("Fix the api outage.\n")

    result = injection.slice_on_spawn(spawned(activated(tools / "session"), "Bash", {"command": f"orca-launch.sh incident-api-1n80-fix incident xhigh {brief}"}))

    assert result is None
    assert brief.read_text() == "Fix the api outage.\n\nRegister slice: lane=incident-api-1n80-fix step=incident\n> words\nslice-hash: 0123456789ab\n"


def test_a_slice_that_does_not_read_passes_the_spawn_and_posts_one_blocker(tools: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(cwd: str, *args: str, **_: object) -> str:
        raise subprocess.CalledProcessError(1, ["rulings.py", *args], stderr="no register for brook\n")

    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(injection, "rulings", broken)
    monkeypatch.setattr(injection, "post_blocker", lambda evt, text, **flags: posted.append((text, flags)))
    monkeypatch.setattr(injection, "judged", lambda *_: None)
    prompt = "Lane lane-a.\nScale: no N\nRegister slice: lane=lane-a step=1.9\nslice-hash: 0123456789ab\n"

    assert injection.slice_on_spawn(spawned(activated(tools / "session"), "Agent", {"name": "lane-a", "prompt": prompt})) is None
    assert posted == [("slice_on_spawn: the register slice for lane-a did not read: no register for brook", {"key": "slice-on-spawn:lane-a"})]


def test_a_brief_that_does_not_read_passes_the_spawn_and_posts_one_blocker(tools: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(injection, "post_blocker", lambda evt, text, **flags: posted.append((text, flags)))
    brief = tools / "lane-a.md"
    brief.write_bytes(b"Lane lane-a.\n\xff\xfe\n")

    assert injection.slice_on_spawn(spawned(activated(tools / "session"), "Agent", {"name": "lane-a", "prompt": f"Your brief is {brief}; read it."})) is None
    assert len(posted) == 1
    assert posted[0][0].startswith("slice_on_spawn: the spawn's brief did not read: ")
    assert posted[0][1] == {"key": "slice-on-spawn:brief"}


def test_a_brief_written_before_the_scale_rule_spawns_quietly_and_a_new_one_is_warned_naming_the_line(tools: Path) -> None:
    session = activated(tools / "session")
    old, new = tools / "old.md", tools / "new.md"
    for brief in (old, new):
        brief.write_text("Lane lane-a.\nRegister slice: lane=lane-a step=1.9\nslice-hash: 0123456789ab\n")
    rule = injection.lane_brief.SCALE_RULE.timestamp()
    os.utime(old, (rule - 60, rule - 60))

    passed = injection.warn_unscaled_spawn(spawned(session, "Agent", {"prompt": f"Your brief is {old}; read it."}))
    warned = injection.warn_unscaled_spawn(spawned(session, "Agent", {"prompt": f"Your brief is {new}; read it."}))

    assert passed is None
    assert warned.action.value == "warn"
    assert warned.message == (
        f"Warning, nothing was blocked: brief {new} has no `Scale:` line; add one line to it: `Scale: no N`, or the N its paths run at, "
        "such as `Scale: chats on one Mac, up to 1,000`"
    )


def test_an_unnamed_successor_spawned_on_its_predecessors_brief_owes_no_scale_line(tools: Path) -> None:
    brief = tools / "lane-a.md"
    brief.write_text("Lane lane-a.\nRegister slice: lane=lane-a step=1.9\nslice-hash: 0123456789ab\n")

    prompt = f"Replaces: lane-a\nYou are lane-a-2 and take over lane-a's work. Your brief is {brief}."

    assert injection.warn_unscaled_spawn(spawned(activated(tools / "session"), "Agent", {"prompt": prompt})) is None


def test_a_drive_lane_spawn_posts_one_spawned_record_naming_the_lane_its_slice_names_and_its_brief(tools: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = activated(tools / "session")
    brief = tools / "lane-a.md"
    brief.write_text("Lane lane-a.\nScale: no N\nRegister slice: lane=lane-a step=1.9\nslice-hash: 0123456789ab\n")
    calls: list[list[str]] = []
    monkeypatch.setattr(injection.subprocess, "run", lambda argv, **_: calls.append(argv))

    injection.record_spawn(spawned(session, "Agent", {"prompt": f"Your brief is {brief}; read it.", "subagent_type": "general-purpose"}))
    injection.record_spawn(spawned(session, "Agent", {"prompt": "Lane lane-b: build the relay.", "subagent_type": "general-purpose"}))

    assert [argv[2:] for argv in calls] == [["spawned", "--lane", "lane-a", "--brief", str(brief), "--by", "root", "--session", SESSION]]


def subagent(session: Path, agent_id: str, prompt: str, **meta: str) -> Path:
    main = session.parent / "main.jsonl"
    main.write_text("")
    agents = session.parent / "main" / "subagents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"agent-{agent_id}.meta.json").write_text(json.dumps({"agentType": "general-purpose"} | meta))
    line = {"type": "user", "isSidechain": True, "parentUuid": None, "uuid": f"u-{agent_id}", "timestamp": "2026-10-30T22:00:00.000Z", "sessionId": SESSION, "message": {"role": "user", "content": prompt}}
    (agents / f"agent-{agent_id}.jsonl").write_text(json.dumps(line) + "\n")
    return main


def bash(session: Path, main: Path, agent_id: str, command: str, event=PreToolUseEvent):
    fields = raw(tool_name="Bash", tool_input={"command": command}, agent_id=agent_id, transcript_path=str(main))
    hook = injection.register_lane_at_first_command if event is PreToolUseEvent else injection.settle_lane_registration
    return hook(event(_raw=fields, ctx=ctx(session)))


def test_an_unnamed_lane_registers_as_its_brief_lane_once_and_a_worker_on_its_parents_brief_never_does(tools: Path) -> None:
    session = activated(tools / "session")
    brief = tools / "lane-a.md"
    brief.write_text("Lane lane-a.\nScale: no N\nRegister slice: lane=lane-a step=1.9\n")
    main = subagent(session, "a1", f"You are lane lane-a. Your brief is {brief}.")
    subagent(session, "aparent", "Lead the rebuild.", name="lane-a")
    subagent(session, "a2", f"Read {brief} and list its files.", parentAgentId="aparent")

    first = bash(session, main, "a1", "cd /repo && cci me")
    assert first.action == "rewrite"
    assert first.updated_input["command"] == "cci post --lane lane-a --kind state --stamp 'lane-a: registered at its first command' >/dev/null\ncd /repo && cci me"
    bash(session, main, "a1", first.updated_input["command"], PostToolUseEvent)
    assert bash(session, main, "a1", "git status") is None
    assert bash(session, main, "a2", "ls") is None


def test_a_registration_another_hooks_rewrite_dropped_is_retried_at_the_next_command(tools: Path) -> None:
    session = activated(tools / "session")
    main = subagent(session, "a1", "Lane lane-b.\nScale: no N\nRegister slice: lane=lane-b step=1.9\n")

    assert bash(session, main, "a1", "git status").action == "rewrite"
    bash(session, main, "a1", "git status", PostToolUseEvent)

    assert bash(session, main, "a1", "git log").updated_input["command"].startswith("cci post --lane lane-b ")
