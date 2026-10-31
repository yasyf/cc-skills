"""board_text_gate.py: one refusal names every problem in order, a failed read lets the board through with one blocker, and the recorder starts with each board."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from captain_hook.events import PostToolUseEvent, PreToolUseEvent
from captain_hook.testing.helpers import StubbedContext, build_context, stubbed_commands

from hooks import board_text_gate as gate
from hooks import compaction_handoff
from hooks.compaction_handoff import CompactionState
from hooks.tests.board_fixtures import GLOSSARY, ROWS, SWEEPS_LOG, board, held_copy_hit, judged, sha12, sweeps

SESSION = "0123456789abcdef"
RULINGS = gate.RULINGS


class Root:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.session_dir = home / "state"
        seed = PostToolUseEvent(_raw={"session_id": SESSION, "tool_name": "Bash"}, ctx=build_context(session_dir=self.session_dir))
        CompactionState(active=True, slug="brook").save(seed)

    def event(self, cls, command: str):
        raw = {"session_id": SESSION, "cwd": str(self.home), "tool_name": "Bash", "tool_input": {"command": command}}
        return cls(_raw=raw, ctx=StubbedContext.wrapping(build_context(session_dir=self.session_dir)))

    def reads(self, *, sweep: str = "", reviewed: str = "", rows: str = ROWS, phrase: str = "") -> dict[str, str]:
        return {
            **judged(phrase),
            f"{RULINGS} rows": rows,
            f"{RULINGS} sweep": sweep,
            f"{RULINGS} glossary": GLOSSARY,
            "cci grep": reviewed,
            f"ccn -R {self.home} log list": SWEEPS_LOG,
            f"ccn -R {self.home} log show": sweeps(5),
            f"ccn -R {self.home} answer list": "[]",
            "cc-present outcomes": json.dumps({"revising": {"blockIds": []}}),
        }


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Root:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    entry = {"drive": "brook", "sessions": [SESSION], "program": "brook", "state_dir": str(tmp_path / "scratch"), "cci_drive": "brook-bus"}
    (drives / "brook.json").write_text(json.dumps(entry))
    return Root(tmp_path)


@pytest.fixture
def posts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "cci-posts.jsonl"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cci").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\necho '#1'\n")
    (bin_dir / "cci").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


def test_one_refusal_names_every_problem_in_stage_order(root: Root) -> None:
    name = "r3-confirm-loops-v1.json"
    with stubbed_commands(root.reads(sweep=held_copy_hit(name), phrase="mount lease")):
        result = gate.board_text_gate(root.event(PreToolUseEvent, f"cc-present update-block {board(name)}"))

    message = result.message
    assert message.startswith(gate.REFUSAL)
    stages = ['names "held copy"', 'plain words, "mount lease"', "text grade: no fidelity desk review"]
    assert [message.index(stage) for stage in stages] == sorted(message.index(stage) for stage in stages)


def test_a_reply_the_judge_reads_as_drive_words_is_refused_with_its_sentence(root: Root) -> None:
    body = "The slice for this lane is done."
    with stubbed_commands(root.reads(phrase="slice")):
        result = gate.board_text_gate(root.event(PreToolUseEvent, f"cc-present reply --block share-open --body {shlex.quote(body)}"))

    assert 'plain words, "slice": Say it in plain words.' in result.message


def test_a_judge_that_fails_adds_nothing(root: Root) -> None:
    name = "clean-card.json"
    reviewed = json.dumps({"seq": 1, "lane": "fidelity-desk", "kind": "review", "text": f"fidelity: ok text {sha12(name)}"}) + "\n"
    with stubbed_commands(root.reads(reviewed=reviewed) | {"claude -p": (1, "claude: overloaded")}):
        assert gate.board_text_gate(root.event(PreToolUseEvent, f"cc-present update-block {board(name)}")) is None


@pytest.mark.parametrize(
    "body_flag",
    [
        "--body \"$(cat <<'EOF'\n{body}\nEOF\n)\"",
        "--body \"$(cat <<-'END-REPLY'\n\t{body}\n\tEND-REPLY\n)\"",
        "--body \"$(cat 'reply draft.md')\"",
        "--body=\"$(cat 'reply draft.md')\"",
        "--body \"$(< 'reply draft.md')\"",
    ],
)
def test_a_reply_whose_body_is_substituted_is_graded_on_that_body(root: Root, body_flag: str) -> None:
    body = "The slice for this lane is done."
    (root.home / "reply draft.md").write_text(f"{body}\n")
    with stubbed_commands(root.reads()):
        result = gate.board_text_gate(root.event(PreToolUseEvent, f"cc-present reply --block share-open {body_flag.format(body=body)}"))

    assert f"no fidelity desk review names the reply on share-open ({gate.hashlib.sha256(body.encode()).hexdigest()[:12]})" in result.message


def test_two_replies_on_one_block_are_each_graded_on_their_own_body(root: Root) -> None:
    command = "cc-present reply --block share-open --body \"$(cat <<'EOF'\nShared.\nEOF\n)\" && cc-present reply --block share-open --body \"$(cat <<'EOF'\nThe slice is done.\nEOF\n)\""
    with stubbed_commands(root.reads()):
        result = gate.board_text_gate(root.event(PreToolUseEvent, command))

    assert all(gate.hashlib.sha256(text.encode()).hexdigest()[:12] in result.message for text in ("Shared.", "The slice is done."))


def test_a_reply_with_no_body_is_not_graded(root: Root) -> None:
    with stubbed_commands(root.reads()):
        assert gate.board_text_gate(root.event(PreToolUseEvent, "cc-present reply --help")) is None


def test_a_failed_register_read_passes_the_board_and_posts_one_blocker(root: Root, posts: Path) -> None:
    with stubbed_commands(root.reads(rows="no register for brook")):
        assert gate.board_text_gate(root.event(PreToolUseEvent, f"cc-present update-block {board('clean-card.json')}")) is None

    [post] = posts.read_text().splitlines()
    assert post.startswith("post --drive brook-bus --lane root --kind blocker --to root --text board_text_gate: rulings.py rows: ")
    assert " --id board_text_gate:" in post


def test_a_drive_with_no_landing_runner_skips_the_text_grade(root: Root, posts: Path) -> None:
    rows = json.dumps({**json.loads(ROWS), "state_dir": str(root.home / "no-runner")})
    with stubbed_commands(root.reads(rows=rows)):
        assert gate.board_text_gate(root.event(PreToolUseEvent, f"cc-present update-block {board('clean-card.json')}")) is None

    assert not posts.exists()


def test_the_recorder_starts_with_each_board_and_once_per_pushed_session(root: Root, monkeypatch: pytest.MonkeyPatch) -> None:
    spawned = []
    monkeypatch.setattr(gate.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw["start_new_session"])))

    for command in (
        "cc-present start --session b1 && cc-present push doc.json --session b1",
        "cc-present push doc.json --session b1",
        "cc-present push doc.json --session b2 --dry-run",
        "cc-present update-block card.json --session b3",
        "cc-present push doc.json",
        "cc-present start --session b1 --new",
    ):
        gate.start_board_watch(root.event(PostToolUseEvent, command))

    start = [sys.executable, str(compaction_handoff.script("board_watch.py")), "start", "--session"]
    assert spawned == [([*start, "b1", "--drive", "brook"], True), ([*start, SESSION, "--drive", "brook"], True), ([*start, "b1", "--drive", "brook"], True)]
    assert compaction_handoff.script("board_watch.py").is_file()


def test_a_worker_recorder_names_its_drive(root: Root, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spawned = []
    monkeypatch.setattr(gate.subprocess, "Popen", lambda argv, **kw: spawned.append(argv))
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_DRIVE", "900424b6")
    worker = Root(tmp_path / "worker")
    seed = PostToolUseEvent(_raw={"session_id": SESSION, "tool_name": "Bash"}, ctx=build_context(session_dir=worker.session_dir))
    CompactionState(active=False).save(seed)

    gate.start_board_watch(worker.event(PostToolUseEvent, "cc-present push doc.json --session b1"))

    assert spawned == [[sys.executable, str(compaction_handoff.script("board_watch.py")), "start", "--session", "b1", "--drive", "900424b6"]]


def test_the_text_grade_reads_only_the_reviews_naming_the_board_in_one_call(root: Root) -> None:
    """Root #234837 and #235862: each board push paged every fidelity review since the drive began, one cci tail per page, and held the cc-inbox daemon near a full core. The gate now asks cci once for the reviews that name this board's hash."""
    name = "clean-card.json"
    reviewed = json.dumps({"seq": 1, "lane": "fidelity-desk", "kind": "review", "text": f"fidelity: ok text {sha12(name)}"}) + "\n"
    calls: list[list[str]] = []
    with stubbed_commands(root.reads(reviewed=reviewed)):
        stubbed = subprocess.run

        def recording(args, *rest, **kwargs):
            if list(args)[:1] == ["cci"]:
                calls.append(list(args))
            return stubbed(args, *rest, **kwargs)

        with mock.patch.object(subprocess, "run", recording):
            result = gate.board_text_gate(root.event(PreToolUseEvent, f"cc-present update-block {board(name)}"))

    assert result is None
    assert [call[:3] for call in calls] == [["cci", "grep", f"fidelity: (ok|missing) text {sha12(name)}"]]
