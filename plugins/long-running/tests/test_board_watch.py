"""board_watch.py over a recorded cc-present stream: every owner interaction becomes a capture, and each event line is kept verbatim."""

from __future__ import annotations

import fcntl
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import board_watch
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "board_watch"
SESSION = "board-1"
ARRIVED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
CONFIRMED = "download the update in the background, show the banner, and only restart when i click it"


class RecordedShell(board_watch.Shell):
    def __init__(self) -> None:
        self.reads: list[str] = []

    def watch(self, session: str):
        assert session == SESSION
        yield from (FIXTURES / "stream.jsonl").read_text().splitlines(keepends=True)

    def block(self, session: str, block: str) -> dict | None:
        self.reads.append(block)
        return board_watch.find(json.loads((FIXTURES / "outcomes.json").read_text())["doc"], block)

    def now(self) -> datetime:
        return ARRIVED


def run(state: Path, shell: board_watch.Shell | None = None) -> list[dict]:
    assert board_watch.main(["start", "--session", SESSION, "--program", "brook", "--state-dir", str(state)], shell or RecordedShell()) == 0
    return [json.loads(line) for line in (state / "owner-prompts.jsonl").read_text().splitlines()]


def test_every_owner_interaction_is_captured_in_order(tmp_path: Path) -> None:
    captured = run(tmp_path)

    assert [(capture["block"], capture["text"]) for capture in captured] == [
        ("share-open-pick", "only for the team, and only read-only"),
        ("share-open-pick", "Yes, read-only"),
        ("board-notes", "also check the share menu"),
        ("share-open-pick", "say who can open it"),
        ("update-confirm-ok", CONFIRMED),
        ("banner-ok", "rejected"),
        ("banner-ok", "keep the old banner text"),
    ]
    assert {capture["source"] for capture in captured} == {"board"}
    assert {capture["at"] for capture in captured} == {"2026-01-02T03:04:05Z"}


def test_a_write_in_then_a_pick_on_one_block_yields_two_captures(tmp_path: Path) -> None:
    picks = [capture for capture in run(tmp_path) if capture["block"] == "share-open-pick"][:2]

    assert [capture["text"] for capture in picks] == ["only for the team, and only read-only", "Yes, read-only"]


def test_the_digest_joins_session_text_and_block_with_nul(tmp_path: Path) -> None:
    approval = next(capture for capture in run(tmp_path) if capture["block"] == "update-confirm-ok")

    assert approval["digest"] == hashlib.sha256(f"{SESSION}\0{CONFIRMED}\0update-confirm-ok".encode()).hexdigest()[:16]
    assert approval["session"] == SESSION


def test_each_event_line_is_kept_verbatim(tmp_path: Path) -> None:
    run(tmp_path)

    assert (tmp_path / "boards" / f"{SESSION}-events.jsonl").read_text() == (FIXTURES / "stream.jsonl").read_text()


def test_a_repeated_pick_or_a_rerun_writes_no_duplicate_capture(tmp_path: Path) -> None:
    first = run(tmp_path)

    assert len({capture["digest"] for capture in first}) == len(first)
    assert run(tmp_path) == first


def test_only_picks_and_approvals_read_the_document(tmp_path: Path) -> None:
    shell = RecordedShell()
    run(tmp_path, shell)

    assert shell.reads == ["share-open-pick", "update-confirm-ok", "share-open-pick"]


def test_a_second_watch_of_the_same_board_exits_at_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "boards").mkdir()
    with (tmp_path / "boards" / f"{SESSION}.lock").open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert board_watch.main(["start", "--session", SESSION, "--program", "brook", "--state-dir", str(tmp_path)], RecordedShell()) == 0

    assert "another watch holds" in capsys.readouterr().out
    assert not (tmp_path / "owner-prompts.jsonl").exists()


def test_the_state_dir_resolves_as_rulings_resolves_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    run_state = tmp_path / ".claude" / "scratch" / "brook"

    assert board_watch.main(["start", "--session", SESSION, "--program", "brook"], RecordedShell()) == 0
    assert (run_state / "owner-prompts.jsonl").is_file()


class MissingBlockShell(RecordedShell):
    def watch(self, session: str):
        yield '{"blockId":"gone","optionIds":["yes"],"schemaVersion":1,"type":"choice.selected"}\n'
        yield '{"blockId":"gone","note":"ship it","schemaVersion":1,"type":"decision.created","verdict":"approved"}\n'
        yield '{"blockId":"banner-ok","note":"rejected","schemaVersion":1,"type":"decision.created","verdict":"rejected"}\n'


def test_a_block_missing_from_the_document_gets_no_stand_in_capture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    captured = run(tmp_path, MissingBlockShell())

    assert [(capture["block"], capture["text"]) for capture in captured] == [("gone", "ship it"), ("banner-ok", "rejected")]
    assert "gone is not in the board's document" in capsys.readouterr().out


class TriageShell(RecordedShell):
    def watch(self, session: str):
        yield '{"blockId":"merge-triage","schemaVersion":1,"type":"triage.decided","verdicts":{"merge-row-101":{"verdict":"approved"}}}\n'
        yield '{"blockId":"merge-triage","schemaVersion":1,"type":"triage.decided","verdicts":{"merge-row-101":{"verdict":"approved","note":"group these three"},"merge-row-102":{"verdict":"rejected","note":"group these three"}}}\n'


def test_each_note_on_a_triage_row_is_captured_with_its_row_and_verdict(tmp_path: Path) -> None:
    captured = run(tmp_path, TriageShell())

    assert [(capture["block"], capture["row"], capture["verdict"], capture["text"], capture["at"]) for capture in captured] == [
        ("merge-triage", "merge-row-101", "approved", "group these three", "2026-01-02T03:04:05Z"),
        ("merge-triage", "merge-row-102", "rejected", "group these three", "2026-01-02T03:04:05Z"),
    ]
    assert captured[0]["digest"] == hashlib.sha256(f"{SESSION}\0group these three\0merge-row-101".encode()).hexdigest()[:16]


def test_a_credential_in_a_board_note_is_redacted_in_its_capture() -> None:
    event = {"type": "triage.decided", "blockId": "notes", "verdicts": {"r1": {"verdict": "ok", "note": "token api-0f3c2b1a-9d8e-4f7a-b6c5-1a2b3c4d5e6f works"}}}

    [capture] = board_watch.captures(event, SESSION, RecordedShell(), ARRIVED)

    assert capture["text"] == "token [credential redacted] works"
