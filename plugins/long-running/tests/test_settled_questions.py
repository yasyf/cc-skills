from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from captain_hook.events import PreToolUseEvent
from captain_hook.testing.helpers import build_context

from hooks import settled_questions as settled
from hooks.compaction_handoff import CompactionState

SESSION = "0123456789abcdef"
RELEASE = "When does a merged change get released — on the owner's word per release, or continuously as it merges?"
UNSETTLED = (
    "No GitHub App we hold can read CI check contexts, so PR-status reads can't leave your 5k/h bucket until an "
    "app gets read-only Checks + Commit statuses. Which way?"
)
PLAN = "# brook\n\n## Decisions (owner, binding)\n" + "".join(
    f"- Decision {n}: the release pipeline keeps flow {n} with the same hard-fought UX and owner rulings.\n" for n in range(40)
) + "\n## Context\nprose\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ccn").write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"print(json.dumps([{{'id': '4ffc9a5' + '0' * 33, 'title': {RELEASE!r}}}]))\n"
    )
    (bin_dir / "ccn").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    plan = tmp_path / ".claude" / "plans" / "brook.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(PLAN)
    return tmp_path


def event(home: Path, tool_name: str, tool_input: dict) -> PreToolUseEvent:
    transcript = home / ".claude" / "projects" / "-repo" / f"{SESSION}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("")
    payload = {"session_id": SESSION, "tool_name": tool_name, "tool_input": tool_input, "transcript_path": str(transcript), "cwd": str(home)}
    evt = PreToolUseEvent(_raw=payload, ctx=build_context(transcript_path=transcript, session_dir=home / "session"))
    CompactionState(active=True, plan_path=str(home / ".claude/plans/brook.md")).save(evt)
    return evt


def ask(home: Path, *questions: str) -> PreToolUseEvent:
    return event(home, "AskUserQuestion", {"questions": [{"question": question} for question in questions]})


def test_an_unsettled_question_passes_silently(home: Path) -> None:
    assert settled.check_settled_before_asking(ask(home, UNSETTLED)) is None


def test_a_re_asked_durable_answer_warns_with_its_id_and_no_plan_dump(home: Path) -> None:
    warned = settled.check_settled_before_asking(ask(home, UNSETTLED, RELEASE))

    assert warned.message.startswith(settled.GATE_MESSAGE)
    assert f"{RELEASE}\n  answer 4ffc9a5: {RELEASE}" in warned.message
    assert UNSETTLED not in warned.message and "Decision 1:" not in warned.message
    assert settled.check_settled_before_asking(ask(home, RELEASE)) is None
    assert settled.check_settled_before_asking(ask(home, UNSETTLED)) is None


def test_a_board_question_settled_by_a_feedback_memory_warns(home: Path) -> None:
    memory = home / ".claude" / "projects" / "-repo" / "memory"
    memory.mkdir(parents=True)
    (memory / "owner-clicks.md").write_text(
        "---\nname: owner-clicks\ndescription: the owner never owes a review or approval click on a program PR\n"
        "metadata:\n  type: feedback\n---\n\nbody\n"
    )
    board = home / "owner-board.json"
    board.write_text(json.dumps({"blocks": [{"title": "Do you owe a review or approval click on the program PR?", "body": "prose"}]}))

    warned = settled.check_settled_before_asking(event(home, "Bash", {"command": f"cc-present start --session s --doc {board}"}))

    assert warned.message.startswith(settled.GATE_MESSAGE)
    assert "the owner never owes a review or approval click on a program PR" in warned.message


def test_a_confirm_block_restating_a_settled_answer_is_not_a_re_ask(home: Path) -> None:
    confirm = {"id": "confirms:release", "type": "card", "title": RELEASE, "children": [{"id": "confirms:release-ok", "type": "approval", "prompt": RELEASE}]}
    board = home / "confirm-board.json"
    board.write_text(json.dumps({"blocks": [confirm]}))

    assert settled.check_settled_before_asking(event(home, "Bash", {"command": f"cc-present push {board}"})) is None
    board.write_text(json.dumps({"blocks": [confirm | {"id": "release"}]}))
    assert settled.check_settled_before_asking(event(home, "Bash", {"command": f"cc-present push {board}"})).message.startswith(settled.GATE_MESSAGE)
