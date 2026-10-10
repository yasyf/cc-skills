from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import compact_job
import pytest
import standing
from cc_transcript import Session
from captain_hook.app import _state
from captain_hook.events import PostToolUseEvent, PreCompactEvent, SessionStartEvent, StopEvent, UserPromptSubmitEvent
from captain_hook.testing.helpers import build_context, matches_conditions

from hooks import nudges
from hooks import compaction_handoff as handoff

FIXTURES = Path(handoff.__file__).parent / "tests" / "fixtures"
SESSION = "0123456789abcdef"


def context(session_dir: Path, raw: dict):
    return build_context(transcript=Session.from_path(Path(raw["transcript_path"])), session_dir=session_dir)


def stop_event(session_dir: Path, **raw) -> StopEvent:
    payload = {
        "session_id": SESSION,
        "transcript_path": str(FIXTURES / "usage-460k.jsonl"),
        "cwd": str(FIXTURES / "project-600k"),
    } | raw
    return StopEvent(_raw=payload, ctx=context(session_dir, payload))


def tool_event(session_dir: Path, tool_name: str, tool_input: dict, **raw) -> PostToolUseEvent:
    payload = {
        "session_id": SESSION,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "transcript_path": str(FIXTURES / "usage-460k.jsonl"),
        "cwd": str(FIXTURES / "project-600k"),
    } | raw
    return PostToolUseEvent(_raw=payload, ctx=context(session_dir, payload))


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
from datetime import datetime, timezone
state = os.environ["FAKE_CCN"]
args = sys.argv[3:]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
GENERATED = "Generated from sources by the long-running compaction hook at 2026-09-30T00:00:00Z.\\n\\n"
def history(doc):
    path = os.path.join(state, doc + ".history.json")
    return json.load(open(path)) if os.path.exists(path) else []
def record(doc, entry):
    json.dump([entry] + history(doc), open(os.path.join(state, doc + ".history.json"), "w"))
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
listed = os.path.join(state, "docs.json")
if args[:2] == ["doc", "list"]:
    labels = [args[i + 1] for i, arg in enumerate(args) if arg == "--label"]
    print(json.dumps([d for d in json.load(open(listed)) if not d.get("superseded_by") and all(label in d["tags"] for label in labels)]))
if args[:2] == ["doc", "add"]:
    docs = json.load(open(listed))
    label = args[args.index("--label") + 1]
    added = "def"[sum(d["title"].endswith("(generated)") for d in docs)] * 40
    body = sys.stdin.read()
    open(os.path.join(state, added + ".md"), "w").write(body)
    record(added, {"kind": "create", "time": now, "changes": [{"field": "body", "to": body}]})
    json.dump(docs + [{"id": added, "title": args[2], "tags": [label], "updated_at": now}], open(listed, "w"))
    print(json.dumps({"id": added}))
if args[:2] == ["doc", "edit"]:
    path = os.path.join(state, args[2] + ".md")
    before, body = open(path).read() if os.path.exists(path) else "", sys.stdin.read()
    open(path, "w").write(body)
    record(args[2], {"kind": "edit", "time": now, "changes": [{"field": "body", "from": before, "to": body}]})
    title = {"title": args[args.index("--title") + 1]} if "--title" in args else {}
    json.dump([d | title | {"updated_at": now} if d["id"] == args[2] else d for d in json.load(open(listed))], open(listed, "w"))
if args[:2] in (["doc", "history"], ["answer", "history"]):
    entries = history(args[2])
    if not entries or entries[-1]["kind"] != "create":
        entries.append({"kind": "create", "time": "2026-09-01T00:00:00Z", "changes": []})
    print(json.dumps(entries))
if args[:2] == ["doc", "supersede"] and os.environ.get("FAKE_CCN_SUPERSEDE") != "fail":
    docs = json.load(open(listed))
    for d in docs:
        if d["id"] == args[2]:
            d["superseded_by"] = args[4]
        if d["id"] == args[4]:
            d["supersedes"] = d.get("supersedes", []) + [args[2]]
    json.dump(docs, open(listed, "w"))
if args[:2] == ["doc", "show"]:
    entry = next((d for d in json.load(open(listed)) if d["id"] == args[2]), {})
    path = os.path.join(state, args[2] + ".md")
    body = open(path).read() if os.path.exists(path) else GENERATED + "## Standing owner rules\\n- none\\n"
    fields = {key: entry.get(key, []) for key in ("tags", "supersedes")} | {"title": entry.get("title", "")}
    print(json.dumps({"id": args[2], "body": body, "created_at": entry.get("updated_at", ""), "updated_at": entry.get("updated_at", "")} | fields))
if args[:2] == ["answer", "list"]:
    answers = json.load(open(os.path.join(state, "answers.json")))
    print(json.dumps([a for a in answers if all(l in a["tags"] for l in args[3:-3:2])]))
"""


FAKE_CCI = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
since = int(args[args.index("--since") + 1])
path = os.path.join(os.environ["FAKE_CCN"], "cci.jsonl")
for line in open(path) if os.path.exists(path) else []:
    if json.loads(line)["seq"] > since:
        print(line, end="")
"""


def standing_rule(docs: Path, kind: str, text: str, **fields) -> None:
    path = docs / "cci.jsonl"
    seq = len(path.read_text().splitlines()) + 1 if path.exists() else 1
    with path.open("a") as out:
        out.write(json.dumps({"seq": seq, "kind": kind, "text": text, "topic": "standing", "refs": {}, **fields}) + "\n")


@pytest.fixture
def docs(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = home / "ccn"
    bin_dir = home / "bin"
    state_dir.mkdir()
    bin_dir.mkdir()
    (bin_dir / "ccn").write_text(FAKE_CCN)
    (bin_dir / "ccn").chmod(0o755)
    (bin_dir / "cci").write_text(FAKE_CCI)
    (bin_dir / "cci").chmod(0o755)
    (state_dir / "docs.json").write_text("[]")
    (state_dir / "answers.json").write_text("[]")
    monkeypatch.setenv("FAKE_CCN", str(state_dir))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return state_dir


def ccn_calls(docs: Path) -> list[list[str]]:
    path = docs / "calls"
    return [json.loads(line)[2:] for line in path.read_text().splitlines()] if path.exists() else []


def doc(doc_id: str, updated: str) -> dict:
    return {"id": doc_id, "title": f"brook: progress {updated}", "tags": ["progress:brook"], "updated_at": updated}


def active(docs: Path) -> list[str]:
    return [entry["id"] for entry in json.loads((docs / "docs.json").read_text()) if "progress:brook" in entry["tags"] and not entry.get("superseded_by")]


REGISTER_ID = "9" * 40


def add_register(docs: Path, body: str) -> None:
    entry = {"id": REGISTER_ID, "title": "brook register", "tags": ["standing-rules:brook"], "updated_at": "2026-10-04T00:00:00Z"}
    (docs / "docs.json").write_text(json.dumps(json.loads((docs / "docs.json").read_text()) + [entry]))
    (docs / (REGISTER_ID + ".md")).write_text(body)


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
    assert nudge.startswith('Context is near the auto-compaction threshold. Write the drive\'s execution state with `ccn doc add "<drive>: progress" ')
    assert "--label progress:brook" in nudge
    assert len(nudge) <= 300
    assert nudges.deliver_nudge(bash(session)).message == nudge
    assert pending(session) == []


def test_the_slug_comes_from_the_plans_progress_line(home: Path, plan: Path, docs: Path) -> None:
    plan.write_text(f"# brook\n\n{handoff.POINTER_PREFIX} the active doc labelled `progress:release-v3`, now `d473abdd`.\n")
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))

    handoff.nudge_at_threshold(bash(session))

    assert state(session).slug == "release-v3"
    assert "--label progress:release-v3" in pending(session)[0]


def test_the_roots_new_doc_gains_the_generated_sections_in_place_and_supersedes_every_other(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z")]))
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))
    handoff.nudge_at_threshold(bash(session))
    assert state(session).prior == ["a" * 40]

    assert handoff.compact_when_idle(stop_event(session)) is None
    assert state(session).phase == "due"
    assert plan.read_text() == "# brook\n"

    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z"), doc("b" * 40, "2026-09-30T05:44:08Z")]))
    (docs / ("b" * 40 + ".md")).write_text("## Root's next actions\n1. land l11\n")
    result = handoff.compact_when_idle(stop_event(session, background_tasks=[{"id": "t1", "type": "teammate", "status": "running", "description": "orca-desk-6"}]))

    assert result.system_message.startswith("The handoff is recorded")
    assert ["doc", "supersede", "a" * 40, "--by", "b" * 40] in ccn_calls(docs)
    assert not any(call[:2] == ["doc", "add"] and "standing-rules:brook" not in call for call in ccn_calls(docs))
    assert active(docs) == ["b" * 40]
    augmented = (docs / ("b" * 40 + ".md")).read_text()
    assert augmented.endswith("_From doc bbbbbbb._\n\n## Root's next actions\n1. land l11\n")
    assert "- teammate: orca-desk-6" in augmented
    assert "read the progress doc `ccn doc show bbbbbbb`, then " in state(session).digest
    assert (state(session).active_doc, state(session).read_first, state(session).narrative) == ("b" * 40, [], "bbbbbbb")
    lines = plan.read_text().splitlines()
    assert lines[:2] == ["# brook", ""]
    assert lines[2].startswith(handoff.POINTER_PREFIX) and "now `bbbbbbbb`" in lines[2]

    with handoff.CompactionState.mutate(bash(session)) as saved:
        saved.phase, saved.prior = "due", ["b" * 40]
    (docs / "docs.json").write_text(json.dumps([doc("b" * 40, "2026-09-30T06:00:00Z"), doc("c" * 40, "2026-09-30T07:00:00Z")]))
    (docs / ("c" * 40 + ".md")).write_text("## Root's next actions\n1. land l12\n")
    handoff.compact_when_idle(stop_event(session))
    [pointer] = [line for line in plan.read_text().splitlines() if line.startswith(handoff.POINTER_PREFIX)]
    assert "now `cccccccc`" in pointer
    assert ["doc", "supersede", "b" * 40, "--by", "c" * 40] in ccn_calls(docs)
    assert not any(call[:2] == ["doc", "add"] and "standing-rules:brook" not in call for call in ccn_calls(docs))
    assert (docs / ("c" * 40 + ".md")).read_text().endswith("_From doc ccccccc._\n\n## Root's next actions\n1. land l12\n")
    assert plan.read_text().startswith("# brook\n\n")


def test_a_generated_doc_is_never_taken_for_the_roots_narrative(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    (docs / "docs.json").write_text(json.dumps([doc("d" * 40, "2026-09-30T06:00:00Z") | {"title": "brook: progress (generated)"}]))
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due").save(bash(session))

    assert handoff.compact_when_idle(stop_event(session)) is None

    assert state(session).phase == "due"
    assert not any(call[:2] == ["doc", "add"] and "standing-rules:brook" not in call for call in ccn_calls(docs))


def test_a_failed_supersede_keeps_the_handoff_due(home: Path, plan: Path, docs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due", prior=["a" * 40]).save(
        bash(session)
    )
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z"), doc("b" * 40, "2026-09-30T05:44:08Z")]))
    ccn = Path(os.environ["PATH"].split(":")[0]) / "ccn"
    ccn.write_text(ccn.read_text() + 'sys.exit(1 if args[:2] == ["doc", "supersede"] else 0)\n')
    monkeypatch.setenv("FAKE_CCN_SUPERSEDE", "fail")

    assert handoff.compact_when_idle(stop_event(session)) is None

    assert state(session).phase == "due"
    assert state(session).failure
    assert plan.read_text() == "# brook\n"


def test_without_cc_notes_the_record_is_a_sibling_folder(home: Path, plan: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan)).save(bash(session))

    handoff.nudge_at_threshold(bash(session))

    saved = state(session)
    assert saved.store == "folder"
    folder = plan.with_name("brook-progress")
    assert f"to a new file in `{folder}/" in pending(session)[0]
    folder.mkdir()
    (folder / "2026-09-30T0544Z.md").write_text("# state\n")

    handoff.compact_when_idle(stop_event(session))

    assert state(session).phase == "compacting"
    [generated] = folder.glob("*-generated.md")
    assert generated.read_text().endswith("_From file 2026-09-30T0544Z.md._\n\n# state\n")
    assert plan.read_text().splitlines()[-1] == (
        f"{handoff.POINTER_PREFIX} the latest execution state is the newest file in `{folder}/`, now "
        f"`{generated.name}`; only this line's name changes."
    )
    assert handoff.newest_record(state(session), str(home))[0] == f"`{generated}`"


def test_an_inactive_drive_is_not_nudged_whatever_the_transcript_holds(home: Path, docs: Path) -> None:
    session = home / "session"

    handoff.nudge_at_threshold(bash(session))

    assert (state(session).active, pending(session)) == (False, [])


def test_nudge_fires_from_a_tail_window_of_an_oversized_transcript(home: Path, docs: Path) -> None:
    session = home / "session"
    source = FIXTURES / "usage-460k.jsonl"
    padding = (json.dumps({"type": "queue-operation", "operation": "enqueue", "padding": "x" * 4096}) + "\n").encode()
    big = home / "big.jsonl"
    big.write_bytes(padding * 4500 + source.read_bytes())
    assert big.stat().st_size > 16 * 1024 * 1024
    handoff.CompactionState(active=True).save(bash(session))

    entry = next(h for h in _state.hooks if h.handler is handoff.nudge_at_threshold)
    assert entry.spec.transcript_events == handoff.TURN_WINDOW
    tail = Session.from_path(source)
    evt = PostToolUseEvent(
        _raw={"session_id": SESSION, "tool_name": "Bash", "tool_input": {"command": "ls"}, "transcript_path": str(big),
              "cwd": str(FIXTURES / "project-600k")},
        ctx=build_context(transcript=tail, session_dir=session),
    )
    handoff.nudge_at_threshold(evt)

    [nudge] = pending(session)
    assert nudge.startswith("Context is near the auto-compaction threshold. ")


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
    assert nudge.startswith("Context is near the auto-compaction threshold. ")


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
        str(handoff.script("compact_job.py")),
        "term-7",
        "/compact Resume the drive from `/p/brook.md` and its progress record: read the plan, "
        "then the progress doc: `ccn doc list --label progress:brook`, then "
        "`ccn doc show <id>`. Keep only in-flight details they lack.",
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

    assert (first.action, first.system_message.startswith("The handoff is recorded")) == (
        "allow",
        True,
    )
    assert handoff.compact_when_idle(stop_event(session)) is None


@pytest.mark.parametrize("phase", ["due", "written", "compacting", "idle"])
def test_compaction_resets_the_handoff(tmp_path: Path, phase: str) -> None:
    raw = {
        "session_id": SESSION,
        "source": "compact",
        "cwd": str(FIXTURES / "project-600k"),
        "transcript_path": str(FIXTURES / "usage-460k.jsonl"),
    }
    evt = SessionStartEvent(_raw=raw, ctx=context(tmp_path / "session", raw))
    handoff.CompactionState(
        active=True, plan_path="/p/brook.md", phase=phase, compacting_since=1.0 if phase == "compacting" else None
    ).save(evt)

    handoff.reground(evt)

    saved = handoff.CompactionState.load(evt)
    assert (saved.phase, saved.compacting_since) == ("idle", None)


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


def test_a_narrative_with_an_uncited_owner_gate_blocks_the_stop_until_fixed(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    rule = {"id": "4ffc9a5" + "0" * 33, "title": "When does a merged change get released?", "tags": ["scope:durable", "brook"]}
    (docs / "answers.json").write_text(json.dumps([rule]))
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z"), doc("b" * 40, "2026-09-30T05:44:08Z")]))
    (docs / ("b" * 40 + ".md")).write_text("## Owner asks\nSoFi release on the owner's word\n")
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due", prior=["a" * 40]).save(
        bash(session)
    )

    blocked = handoff.compact_when_idle(stop_event(session))

    assert blocked.action.name == "block"
    assert "fails the standing-rules lint" in blocked.message and "`ccn doc edit bbbbbbbb --body -`" in blocked.message
    assert "owner-gate line cites no live answer id: SoFi release on the owner's word" in blocked.message
    assert "missing durable rule" not in blocked.message
    assert state(session).phase == "due"
    assert not any(call[:2] in (["doc", "supersede"], ["doc", "add"]) for call in ccn_calls(docs))

    (docs / ("b" * 40 + ".md")).write_text("## Owner asks\nSoFi released as it merges (4ffc9a5), never on the owner's word\n")
    assert handoff.compact_when_idle(stop_event(session)).system_message.startswith("The handoff is recorded")
    assert ["doc", "supersede", "a" * 40, "--by", "b" * 40] in ccn_calls(docs)
    assert "## Standing owner rules\n\n- no `standing-rules` register doc\n" in (docs / ("b" * 40 + ".md")).read_text()


def test_an_uncited_standing_rule_blocks_the_stop_at_its_record_not_the_doc(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    rule = {"id": "4ffc9a5" + "0" * 33, "title": "When does a merged change get released?", "tags": ["scope:durable", "brook"]}
    (docs / "answers.json").write_text(json.dumps([rule]))
    (docs / "docs.json").write_text(json.dumps([doc("b" * 40, "2026-09-30T05:44:08Z")]))
    (docs / ("b" * 40 + ".md")).write_text("## Root's next actions\n1. watch SoFi\n")
    standing_rule(docs, "go", "release on the owner's word")
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due").save(bash(session))

    blocked = handoff.compact_when_idle(stop_event(session))

    assert blocked.action.name == "block"
    assert "\ncci #1: standing rule #1 requires owner approval but cites no live answer id: " in blocked.message
    assert "ccn doc edit" not in blocked.message

    standing_rule(docs, "correction", "every merged PR is released as it merges (answer 4ffc9a5)", re=1)
    assert handoff.compact_when_idle(stop_event(session)).system_message.startswith("The handoff is recorded")


def precompact(session: Path, **raw) -> PreCompactEvent:
    payload = {"session_id": SESSION, "transcript_path": str(FIXTURES / "usage-460k.jsonl"), "cwd": str(FIXTURES / "project-600k")}
    payload |= raw
    return PreCompactEvent(_raw=payload, ctx=context(session, payload))


def test_every_compaction_generates_the_handoff_and_restores_its_digest(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    inbox = home / ".claude" / "scratch" / "brook" / "inbox"
    inbox.mkdir(parents=True)
    standing_rule(docs, "go", "release every landing as it merges")
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z")]))
    (docs / ("a" * 40 + ".md")).write_text("## Root's next actions\n1. watch SoFi\n")
    add_register(docs, "# brook register\n\n1. Pulumi state is the only truth.\n")
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook").save(bash(session))

    handoff.compaction_instructions(precompact(session))

    generated = (docs / ("a" * 40 + ".md")).read_text()
    assert "- #1 [cci #1]" in generated
    assert generated.endswith("_From doc aaaaaaa._\n\n## Root's next actions\n1. watch SoFi\n")
    assert not any(call[:2] in (["doc", "add"], ["doc", "supersede"]) and "standing-rules:brook" not in call for call in ccn_calls(docs))

    restored = handoff.reground(session_start(session, "compact")).message

    assert restored.startswith("Compacted long-running drive `brook`. Before acting, read the standing rules register `ccn doc show 9999999`")
    assert "Then read the progress doc `ccn doc show aaaaaaa`" in restored
    assert "\nRegister: 1 owner-approved rules, 1 live standing rules.\n" in restored
    assert len(restored.encode()) <= 2000
    assert state(session).digest is None


def test_the_register_arrives_once_whole_after_a_compaction(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    body = "# brook register\n\n" + "".join(f"{n}. Pulumi state is the only truth, rule {n}; ünïcode reason {'x' * 150}\n" for n in range(1, 31))
    add_register(docs, body)
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook").save(bash(session))

    handoff.compaction_instructions(precompact(session))
    handoff.reground(session_start(session, "compact"))
    delivered = handoff.deliver_register(bash(session))

    header, _, rest = delivered.message.partition(f"\n{handoff.REGISTER_FENCE}\n")
    assert header == "Standing rules register `9999999`, verbatim; it binds this session and every lane brief, and outranks any summary."
    assert rest == f"{body.rstrip()}\n{handoff.REGISTER_FENCE}"
    assert len(delivered.message) < 10_000
    assert handoff.deliver_register(bash(session)) is None
    assert handoff.deliver_register(bash(session, agent_id="a1")) is None
    assert standing.pointer({"id": REGISTER_ID, "body": body}) in (docs / ("d" * 40 + ".md")).read_text()
    assert not any(call[:2] in (["doc", "edit"], ["doc", "add"], ["doc", "supersede"]) and REGISTER_ID in call for call in ccn_calls(docs))


def session_start(session: Path, source: str) -> SessionStartEvent:
    raw = {
        "session_id": SESSION,
        "source": source,
        "cwd": str(FIXTURES / "project-600k"),
        "transcript_path": str(FIXTURES / "usage-460k.jsonl"),
    }
    return SessionStartEvent(_raw=raw, ctx=context(session, raw))


def test_a_resumed_drive_restores_its_newest_progress_doc_within_budget(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z"), doc("b" * 40, "2026-09-30T05:00:00Z")]))
    (docs / ("a" * 40 + ".md")).write_text("## Root's next actions\n1. stale\n")
    (docs / ("b" * 40 + ".md")).write_text("## Root's next actions\n1. watch SoFi\n" + "- lane row\n" * 400)
    add_register(docs, "# brook register\n\n1. Pulumi state is the only truth.\n")
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook").save(bash(session))

    restored = handoff.reground(session_start(session, "resume")).message
    assert handoff.deliver_register(bash(session)) is None

    assert restored.startswith("Resumed long-running drive `brook`. Before acting, read the progress record `ccn doc show bbbbbbb`")
    assert "\n## Root's next actions\n1. watch SoFi\n- lane row\n" in restored
    assert "stale" not in restored
    assert len(restored.encode()) <= handoff.RESTORE_BUDGET


def test_a_resumed_drive_without_a_progress_doc_points_at_the_label(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook").save(bash(session))

    restored = handoff.reground(session_start(session, "resume")).message

    assert restored == (
        f"Read `{plan}` before anything else, then the progress doc: `ccn doc list --label progress:brook`, "
        "then `ccn doc show <id>`; they supersede the conversation so far."
    )


def test_a_hand_written_doc_gains_the_generated_sections_and_the_summary_names_it(
    home: Path, plan: Path, docs: Path
) -> None:
    session = home / "session"
    old_generated = doc("4" * 40, "2026-10-02T22:12:33Z") | {"title": "brook: progress 2026-10-02T2212Z (generated)"}
    record = doc("6" * 40, "2026-10-03T00:42:16Z")
    (docs / "docs.json").write_text(json.dumps([old_generated, record]))
    (docs / ("6" * 40 + ".md")).write_text("## Root's next actions\n1. 12-item plan\n")
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook").save(bash(session))

    instructions = handoff.compaction_instructions(precompact(session)).message

    assert ["doc", "supersede", "4" * 40, "--by", "6" * 40] in ccn_calls(docs)
    assert not any(call[:2] == ["doc", "add"] and "standing-rules:brook" not in call for call in ccn_calls(docs))
    assert active(docs) == ["6" * 40]
    augmented = (docs / ("6" * 40 + ".md")).read_text()
    assert augmented.count("## Standing owner rules") == 1
    assert augmented.endswith("_From doc 6666666._\n\n## Root's next actions\n1. 12-item plan\n")
    assert instructions == (
        f"Resume from `{plan}`, then `ccn doc show 66666666`; keep only in-flight details they lack. "
        "Quote: active progress doc: 66666666; the id in this summary wins over any id captured earlier in the conversation."
    )
    [pointer] = [line for line in plan.read_text().splitlines() if line.startswith(handoff.POINTER_PREFIX)]
    assert "now `66666666`" in pointer and "It is the only active one" in pointer

    handoff.reground(session_start(session, "compact"))
    with handoff.CompactionState.mutate(bash(session)) as saved:
        saved.generated_at = None
    handoff.compaction_instructions(precompact(session))

    assert not any(call[:2] == ["doc", "add"] and "standing-rules:brook" not in call for call in ccn_calls(docs))
    assert [call[:3] for call in ccn_calls(docs) if call[:2] == ["doc", "edit"]] == [["doc", "edit", "6" * 40]] * 2
    assert state(session).active_doc == "6" * 40
    augmented = (docs / ("6" * 40 + ".md")).read_text()
    assert augmented.count("## Standing owner rules") == 1 and augmented.count("_From ") == 1
    assert augmented.endswith("_From doc 6666666, carried forward._\n\n## Root's next actions\n1. 12-item plan\n")


def test_a_second_active_doc_after_generation_blocks_the_stop_and_names_the_failure_at_compaction(
    home: Path, plan: Path, docs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = home / "session"
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z"), doc("b" * 40, "2026-09-30T05:44:08Z")]))
    (docs / ("b" * 40 + ".md")).write_text("## Root's next actions\n1. land l11\n")
    monkeypatch.setenv("FAKE_CCN_SUPERSEDE", "fail")
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due", prior=["a" * 40]).save(bash(session))

    blocked = handoff.compact_when_idle(stop_event(session))

    assert blocked.action.name == "block"
    assert blocked.message.startswith(
        "The drive's handoff left more than one active progress doc. Fix it, then stop again:\n"
        "2 active progress:brook docs after generation, expected only bbbbbbb: aaaaaaa, bbbbbbb; "
    )
    assert state(session).phase == "due"

    instructions = handoff.compaction_instructions(precompact(session)).message

    assert instructions.startswith("The generated handoff failed: 2 active progress:brook docs after generation, expected only bbbbbbb: ")


def test_a_stop_generated_handoff_is_not_regenerated_at_compaction(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", generated_at=handoff.time.time(), digest="kept").save(bash(session))

    handoff.compaction_instructions(precompact(session))

    assert not any(call[:2] == ["doc", "add"] and "standing-rules:brook" not in call for call in ccn_calls(docs))
    assert state(session).digest == "kept"


def test_a_doc_written_after_the_stop_adoption_is_the_one_compaction_names(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-10-07T16:03:15Z"), doc("b" * 40, "2026-10-07T16:06:13Z")]))
    (docs / ("b" * 40 + ".md")).write_text("## Root's next actions\n1. dump 30\n")
    handoff.CompactionState(
        active=True,
        plan_path=str(plan),
        slug="brook",
        phase="compacting",
        active_doc="a" * 40,
        generated_at=handoff.time.time() - 180,
    ).save(bash(session))

    instructions = handoff.compaction_instructions(precompact(session)).message

    assert instructions.startswith(f"Resume from `{plan}`, then `ccn doc show bbbbbbbb`; ")
    assert ["doc", "supersede", "a" * 40, "--by", "b" * 40] in ccn_calls(docs)
    assert active(docs) == ["b" * 40]
    [pointer] = [line for line in plan.read_text().splitlines() if line.startswith(handoff.POINTER_PREFIX)]
    assert "now `bbbbbbbb`" in pointer
    assert handoff.reground(session_start(session, "resume")).message.startswith(
        "Resumed long-running drive `brook`. Before acting, read the progress record `ccn doc show bbbbbbb`"
    )


def edit_in_place(docs: Path, doc_id: str, dump: str) -> None:
    head, carried = (docs / f"{doc_id}.md").read_text().split("## Root narrative\n\n")
    edited = f"{head}## Root narrative\n\n{dump}\n{carried.split('\n\n', 1)[1]}"
    subprocess.run(["ccn", "-R", str(FIXTURES / "project-600k"), "doc", "edit", doc_id, "--body", "-"], input=edited, text=True, check=True)


DUMP_1510 = "Pre-compact handoff written by main at 3:10 PM. Read all of it.\n\n### A. Current state\nReleases are paused.\n"


@pytest.mark.parametrize("stop", [True, False])
def test_a_narrative_edited_into_the_generated_doc_is_the_written_narrative(home: Path, plan: Path, docs: Path, stop: bool) -> None:
    session = home / "session"
    (docs / "docs.json").write_text(json.dumps([doc("a" * 40, "2026-09-30T04:00:00Z")]))
    (docs / ("a" * 40 + ".md")).write_text("## 10:00 PM dump 1\n\n### Program state\nCensus 200/293.\n")
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook").save(bash(session))
    handoff.compaction_instructions(precompact(session))
    handoff.reground(session_start(session, "compact"))
    with handoff.CompactionState.mutate(bash(session)) as saved:
        saved.phase, saved.generated_at = "due", handoff.time.time() - 60
    edit_in_place(docs, "a" * 40, DUMP_1510)

    if stop:
        assert handoff.compact_when_idle(stop_event(session)).system_message.startswith("The handoff is recorded")
    handoff.compaction_instructions(precompact(session))
    restored = handoff.reground(session_start(session, "compact")).message

    generated = (docs / ("a" * 40 + ".md")).read_text()
    assert generated.split("## Root narrative\n")[1] == (
        f"\n_From doc aaaaaaa._\n\n{DUMP_1510}\n## 10:00 PM dump 1\n\n### Program state\nCensus 200/293.\n"
    )
    assert not any(call[:2] == ["doc", "add"] for call in ccn_calls(docs))
    assert sum(call[:3] == ["doc", "edit", "a" * 40] for call in ccn_calls(docs)) == 3
    assert "not written" not in restored


def test_without_a_narrative_the_restore_asks_for_one(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due").save(bash(session))

    handoff.compaction_instructions(precompact(session))

    assert handoff.reground(session_start(session, "compact")).message.endswith(
        "Your narrative was not written before compaction; write a progress record when convenient."
    )


def test_a_subagent_compaction_generates_nothing(tmp_path: Path) -> None:
    entry = next(h for h in _state.hooks if h.handler is handoff.compaction_instructions)
    assert not matches_conditions(entry.spec, precompact(tmp_path, agent_id="a1b2c3"))


def test_an_installed_hook_runs_the_newest_live_release_of_its_scripts(tmp_path: Path) -> None:
    cache = tmp_path / "long-running"
    for version in ("0.7.9", "0.7.46", "0.7.48", "0.7.50"):
        (cache / version / handoff.SCRIPTS).mkdir(parents=True)
    (cache / "0.7.50" / handoff.ORPHANED).touch()
    (cache / "0.7.47").mkdir()

    assert handoff.newest(cache / "0.7.46") == cache / "0.7.48"
    assert handoff.newest(tmp_path / "long-running-worktree") == tmp_path / "long-running-worktree"
    assert handoff.script("handoff.py") == Path(handoff.__file__).parents[2] / handoff.SCRIPTS / "handoff.py"


FULL_HANDOFF = (
    "# Drive brook: pre-compact handoff, 2026-10-10 ~22:10Z\n\n"
    "This supersedes progress doc aaaaaaa.\n\n"
    "## 1. Owner asks 83 through 103\n\n" + "".join(f"- ask/0000{n}: in flight on lane-{n}\n" for n in range(83, 104)) + "\n"
    "## 2. Live lanes\n\n- oncall-ic-dual-3 (124)\n"
)
ADDENDUM = (
    "# Addendum to progress doc bbbbbbb (everything after 22:10Z)\n\n"
    "## Owner rulings since bbbbbbb\n\n- **824f65f**: the version-qualified PC replace is routine.\n\n"
    "## Results since bbbbbbb\n\n- **Retro DONE**: ccn doc **8bcade3** supersedes 21961bb.\n\n"
    "## Next actions for the root\n\n1. Keep the freeze until the owner lifts it.\n"
)


def test_a_stop_handoff_whose_state_was_lost_still_points_the_compaction_at_the_roots_records(home: Path, plan: Path, docs: Path) -> None:
    session = home / "session"
    entries = [
        doc("a" * 40, "2026-10-10T21:13:13Z") | {"title": "brook: progress 2026-10-10T2113Z (generated)", "superseded_by": "b" * 40},
        doc("b" * 40, "2026-10-10T22:10:38Z") | {"title": "brook: progress 2026-10-10T2210Z (pre-compact handoff, full)", "supersedes": ["a" * 40]},
        doc("c" * 40, "2026-10-10T22:15:46Z") | {"title": "brook: progress 2026-10-10T2225Z addendum (read with bbbbbbb)"},
    ]
    (docs / "docs.json").write_text(json.dumps(entries))
    (docs / ("b" * 40 + ".md")).write_text(FULL_HANDOFF)
    (docs / ("c" * 40 + ".md")).write_text(ADDENDUM)
    due = handoff.CompactionState(active=True, plan_path=str(plan), slug="brook", phase="due", prior=["a" * 40])
    due.save(bash(session))

    assert handoff.compact_when_idle(stop_event(session)).system_message.startswith("The handoff is recorded")
    due.save(bash(session))
    instructions = handoff.compaction_instructions(precompact(session)).message
    restored = handoff.reground(session_start(session, "compact")).message

    assert active(docs) == ["c" * 40]
    assert not any(call[:2] == ["doc", "add"] for call in ccn_calls(docs))
    assert (docs / ("b" * 40 + ".md")).read_text() == FULL_HANDOFF
    record = (docs / ("c" * 40 + ".md")).read_text()
    assert record.split("\n## Root narrative\n")[1] == f"\n_From doc ccccccc, carried forward._\n\n{ADDENDUM.strip()}\n"
    assert "\n- Hand-written handoff, read it in full before `## Root narrative`: `ccn doc show bbbbbbb`: " in record
    assert instructions.startswith(f"Resume from `{plan}`, then `ccn doc show bbbbbbb`, then `ccn doc show cccccccc`; ")
    assert "Quote: hand-written handoff bbbbbbb, then active progress doc: cccccccc; " in instructions
    assert "read the hand-written handoff `ccn doc show bbbbbbb`, then the progress doc `ccn doc show ccccccc`, then " in restored
    assert "not written" not in restored
    [pointer] = [line for line in plan.read_text().splitlines() if line.startswith(handoff.POINTER_PREFIX)]
    assert "now `cccccccc`" in pointer
