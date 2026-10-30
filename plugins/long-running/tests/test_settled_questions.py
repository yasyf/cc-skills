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
RELEASE_ANSWER = {"id": "4ffc9a5" + "0" * 33, "title": RELEASE, "body": "Continuously as it merges", "tags": ["scope:durable"], "created_at": "2026-10-20T17:00:00Z"}
UPLOAD_PICK = {
    "id": "7599417" + "0" * 33,
    "title": "Journal-rewrite picks / 2. SandSQL starts one litestream process per chat being written, up to 16 on a Mac. Keep that, or consolidate to one per...",
    "body": (
        "Card: Which of our journal-rewrite picks stand?\n"
        "Board question: 2. SandSQL starts one litestream process per chat being written, up to 16 on a Mac. Keep that, or consolidate to one per Mac?\n"
        "Pick: option a, \"Keep it as SandSQL's own\".\n"
    ),
    "tags": ["from:owner"],
    "created_at": "2026-10-22T20:22:19Z",
    "superseded_by": "0f31b56",
}
PER_CHAT_PICK = UPLOAD_PICK | {
    "id": "0f31b56" + "0" * 33,
    "body": "Board question: Does SandSQL run one litestream process per chat being written on a Mac, or one per Mac?\nPick: one per chat.\n",
    "created_at": "2026-10-23T02:05:00Z",
}
LIGHTER_JOURNAL = UPLOAD_PICK | {
    "id": "0f31b56" + "1" * 33,
    "title": "In general: how much lighter should a chat's journal on a Mac get?",
    "body": "Owner picked:\nOption b: Lighter writer, one litestream upload process per chat being written\nPro: Each chat's upload fails on its own\n",
    "created_at": "2026-10-22T22:40:00Z",
}
SOMEONE_ELSES = UPLOAD_PICK | {"id": "1234567" + "0" * 33, "body": UPLOAD_PICK["body"].replace("Keep it as SandSQL's own", "One per Mac"), "tags": ["scope:ephemeral"]}
UPLOAD_CARD = {"title": "Does SandSQL run one litestream process per Mac, or one per chat being written?", "options": ["One per Mac", "One per chat"]}
PLAN = "# brook\n\n## Decisions (owner, binding)\n" + "".join(
    f"- Decision {n}: the release pipeline keeps flow {n} with the same hard-fought UX and owner rulings.\n" for n in range(40)
) + "\n## Context\nprose\n"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "answers.json").write_text(json.dumps([RELEASE_ANSWER]))
    (bin_dir / "ccn").write_text(f"#!/bin/sh\ncat {tmp_path / 'answers.json'}\n")
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


def card(home: Path, body: str = "") -> PreToolUseEvent:
    return event(home, settled.SLACK_CARD, {"card": UPLOAD_CARD | ({"body": body} if body else {})})


def test_a_card_on_something_the_owner_picked_before_warns_with_every_pick_he_made_on_it(home: Path) -> None:
    """Root #211144: the Oct 29 card on one upload process per Mac or per chat never showed his Oct 22 picks, one of which pointed the other way."""
    untagged = {key: value for key, value in SOMEONE_ELSES.items() if key != "tags"}
    (home / "answers.json").write_text(json.dumps([UPLOAD_PICK, PER_CHAT_PICK, SOMEONE_ELSES, untagged]))

    warned = settled.check_settled_before_asking(card(home))

    assert warned.message.startswith(settled.PICKS_MESSAGE)
    assert "You picked \"Keep it as SandSQL's own\" on Oct 22, asked \"SandSQL starts one litestream process per chat being written" in warned.message
    assert 'You picked "one per chat" on Oct 22, asked "Does SandSQL run one litestream process per chat' in warned.message
    assert '"One per Mac" on' not in warned.message


def test_a_pick_whose_question_was_general_matches_on_what_he_picked(home: Path) -> None:
    (home / "answers.json").write_text(json.dumps([LIGHTER_JOURNAL]))

    warned = settled.check_settled_before_asking(card(home))

    assert 'You picked "Lighter writer, one litestream upload process per chat being written" on Oct 22' in warned.message


def test_a_card_that_shows_each_earlier_pick_passes(home: Path) -> None:
    (home / "answers.json").write_text(json.dumps([UPLOAD_PICK, PER_CHAT_PICK]))

    assert settled.check_settled_before_asking(card(home, "You picked \"Keep it as SandSQL's own\" on Oct 22, and \"one per chat\" on Oct 22.")) is None


def test_a_card_missing_one_earlier_pick_names_only_that_one(home: Path) -> None:
    (home / "answers.json").write_text(json.dumps([UPLOAD_PICK, PER_CHAT_PICK]))

    warned = settled.check_settled_before_asking(card(home, 'You picked "one per chat" on Oct 22.'))

    assert "Keep it as SandSQL's own" in warned.message and 'You picked "one per chat"' not in warned.message
