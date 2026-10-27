"""board_watch.py over a recorded cc-present stream: every owner interaction becomes a capture and one bus record, and each event line is kept verbatim."""

from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import board_watch
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "board_watch"
SESSION = "board-1"
ARRIVED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
CONFIRMED = "download the update in the background, show the banner, and only restart when i click it"


class RecordedShell(board_watch.Shell):
    def __init__(self, bus: list[dict] | None = None, doc: dict | None = None, refused: bool = False) -> None:
        self.bus = bus or []
        self.doc = doc or json.loads((FIXTURES / "outcomes.json").read_text())["doc"]
        self.refused = refused
        self.posts: list[list[str]] = []

    def watch(self, session: str):
        assert session == SESSION
        yield from (FIXTURES / "stream.jsonl").read_text().splitlines(keepends=True)

    def document(self, session: str, block: str) -> dict | None:
        return self.doc

    def cci(self, argv: list[str]) -> subprocess.CompletedProcess:
        if argv[0] == "grep":
            return subprocess.CompletedProcess(argv, 0, "".join(json.dumps(record) + "\n" for record in self.bus), "")
        self.posts.append(argv)
        return subprocess.CompletedProcess(argv, 1 if self.refused else 0, "", "refused" if self.refused else "")

    def now(self) -> datetime:
        return ARRIVED

    def posted(self, name: str) -> list[str]:
        return [post[post.index(name) + 1] for post in self.posts if name in post]


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

    [capture] = board_watch.captures(event, SESSION, None, ARRIVED)

    assert capture["text"] == "token [credential redacted] works"


SHARE = "Owner on board board-1, Should a shared note open in the browser? (share-open-pick)"


def test_each_owner_interaction_reaches_the_bus_once_with_the_card_and_his_words(tmp_path: Path) -> None:
    shell = RecordedShell()
    run(tmp_path, shell)

    assert shell.posted("--text") == [
        f"{SHARE}: wrote \u201conly for the team, and only read-only\u201d, at {{at}}",
        f"{SHARE}: picked \u201cYes, read-only\u201d, at {{at}}",
        "Owner on board board-1, board-notes: wrote \u201calso check the share menu\u201d, at {at}",
        f"{SHARE}: noted \u201csay who can open it\u201d, at {{at}}",
        f"Owner on board board-1, Confirm your words as written? (update-confirm-ok): approved \u201c{CONFIRMED}\u201d, at {{at}}",
        "Owner on board board-1, banner-ok: rejected noted \u201ckeep the old banner text\u201d, at {at}",
        f"{SHARE}: picked \u201cYes, read-only\u201d, at {{at}}",
    ]
    assert {tuple(shell.posted(flag)) for flag in ("--kind", "--to", "--lane", "--drive")} == {("owner",) * 7, ("all",) * 7, ("board-watch",) * 7, ("brook",) * 7}


def test_a_restart_keys_events_after_the_lines_already_recorded(tmp_path: Path) -> None:
    first, second = RecordedShell(), RecordedShell()
    run(tmp_path, first)
    run(tmp_path, second)

    keys = first.posted("--id") + second.posted("--id")
    assert [key.split(":")[2] for key in first.posted("--id")] == ["2", "3", "4", "5", "6", "7", "8"]
    assert len(set(keys)) == len(keys) == 14


class MergeShell(RecordedShell):
    def watch(self, session: str):
        yield '{"blockId":"merge-triage","schemaVersion":1,"type":"triage.decided","verdicts":{"merge-row-101":{"verdict":"approved"}}}\n'
        yield '{"blockId":"merge-prs","schemaVersion":1,"type":"triage.decided","verdicts":{"202":{"verdict":"rejected","head":"413b38e","note":"split it"}}}\n'


MERGE_DOC = {"blocks": [{"id": "card-merge", "type": "card", "title": "Merge these?", "children": [{"id": "merge-triage", "type": "triage", "items": [{"id": "merge-row-101", "hint": "commit abc1234, built on dev"}]}]}]}


def ask(seq: int, text: str, kind: str = "ask", status: str = "open", drive: str = "brook") -> dict:
    return {"seq": seq, "kind": kind, "status": status, "drive": drive, "text": text}


def test_a_merge_tap_names_the_head_and_resolves_only_open_asks_on_that_row(tmp_path: Path) -> None:
    bus = [
        ask(11, "waiting on merge-row-101"),
        ask(12, "hold merge:202:413b38e until he taps", kind="hold"),
        ask(13, "merge-row-101 again", status="closed"),
        ask(14, "merge-row-1010 waits", kind="blocker"),
        ask(15, "merge-row-101 elsewhere", drive="other"),
        ask(16, "#202 is red"),
    ]
    shell = MergeShell(bus, MERGE_DOC)
    run(tmp_path, shell)

    assert shell.posted("--text") == [
        "Owner on board board-1, Merge these? (merge-triage): merge-row-101 approved at abc1234, at {at}",
        "Owner on board board-1, merge-prs: 202 rejected at 413b38e noted \u201csplit it\u201d, at {at}",
    ]
    assert shell.posted("--resolves") == ["11", "12"]
    assert [[arg for arg in post if arg.startswith("--pr=")] for post in shell.posts] == [["--pr=101"], ["--pr=202"]]


def test_a_tap_resolves_open_asks_naming_its_block_or_card_but_not_a_longer_id(tmp_path: Path) -> None:
    bus = [ask(21, "parked on share-open"), ask(22, "share-open-pick needs his word", kind="blocker"), ask(23, "parked on share-open-2")]
    shell = RecordedShell(bus)
    run(tmp_path, shell)

    assert shell.posted("--resolves") == ["21,22"] * 4


def test_a_refused_post_is_reported_and_the_watch_keeps_recording(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    captured = run(tmp_path, RecordedShell(refused=True))

    assert len(captured) == 7
    assert capsys.readouterr().out.count("failed: refused") == 7
