from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from cc_transcript.query import Session
from captain_hook import Action
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent, UserPromptSubmitEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import owner_ledger, owner_returns, root_context
from hooks.compaction_handoff import CompactionState

SESSION = "0123456789abcdef"
TESTDATA = Path(__file__).parents[1] / "lr" / "internal" / "owner" / "testdata"
HAND_LEDGER = "# Owner message ledger (communicator).\n| time | message (short) | routed | replied |\n|---|---|---|---|\n| 7:32 PM | a hand row | coordinator 346167 | yes 7:33 |\n"
OPEN = "| m1 | Nov 2 8:05 PM | keep the nightly report |  | open |\n"
MARKED = "| m1 | Nov 2 8:05 PM | keep the nightly report | 346705 | responded |\n"
ASKED = {"questions": [{"question": "Merge #48401 now?", "header": "Ask", "options": [{"label": "Go"}, {"label": "Hold"}], "multiSelect": False}]}


class Window:
    def __init__(self, home: Path, *, active: bool = True, coordinator: bool = False) -> None:
        self.ledger = home / "scratch" / "brook" / "owner-relay" / "owner-ledger.md"
        self.transcript = home / "root.jsonl"
        self.transcript.write_text("")
        self.session_dir = home / "session"
        CompactionState(active=active, coordinator=coordinator, slug="brook").save(self.event(PostToolUseEvent, tool_name="Bash"))

    def event(self, cls, **raw):
        payload = {"session_id": SESSION, "transcript_path": str(self.transcript), "cwd": str(self.transcript.parent)}
        return cls(_raw=payload | raw, ctx=build_context(transcript=Session.from_path(self.transcript), session_dir=self.session_dir))

    def fire(self, cls, **raw) -> list:
        return fire(owner_ledger, self.event(cls, **raw))

    def prompt(self, text: str, **raw) -> list:
        return self.fire(UserPromptSubmitEvent, prompt=text, **raw)

    def stop(self) -> list:
        return self.fire(StopEvent)

    def seed(self, text: str) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(text)

    def rows(self) -> list[str]:
        return [line for line in self.ledger.read_text().splitlines() if re.match(r"\| m\d+ \|", line)]

    def queued(self, text: str) -> None:
        entry = {"isSidechain": False, "uuid": "u1", "parentUuid": None, "sessionId": SESSION, "timestamp": "2026-10-28T03:53:25.695Z"}
        entry |= {"type": "attachment", "attachment": {"type": "queued_command", "prompt": text, "commandMode": "prompt"}}
        self.transcript.write_text(json.dumps(entry) + "\n")


@pytest.fixture
def drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "brook-drive.json").write_text(json.dumps({"drive": "brook-drive", "cci_drive": "brook", "sessions": [SESSION], "state_dir": str(tmp_path / "scratch" / "brook")}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


@pytest.fixture
def communicator(drive: Path) -> Window:
    return Window(drive)


class Notes:
    def __init__(self) -> None:
        self.added: list[tuple[str, ...]] = []

    def __call__(self, cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
        assert args[:3] == ("answer", "add", "--json")
        self.added.append(args[3:])
        return subprocess.CompletedProcess(args, 0, json.dumps({"id": "4ffc9a5e0d" * 4}), "")


@pytest.fixture(autouse=True)
def notes(monkeypatch: pytest.MonkeyPatch) -> Notes:
    fake = Notes()
    monkeypatch.setattr(root_context, "ccn", fake)
    return fake


class Bus:
    def __init__(self, home: Path) -> None:
        self.log = home / "cci.jsonl"

    def posts(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


@pytest.fixture(autouse=True)
def bus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bus:
    fake = Bus(tmp_path)
    cci = tmp_path / "bin" / "cci"
    cci.parent.mkdir()
    cci.write_text(f"#!{sys.executable}\nimport json, sys\nwith open({str(fake.log)!r}, 'a') as log:\n    log.write(json.dumps({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}) + '\\n')\nprint('#1')\n")
    cci.chmod(0o755)
    monkeypatch.setenv("PATH", f"{cci.parent}:{Path(sys.executable).parent}:/usr/bin:/bin")
    return fake


def owner_record(row: str, text: str) -> dict:
    return {"argv": ["post", "--drive", "brook", "--lane", "owner-relay", "--kind", "owner", "--id", f"owner:{row}", "--attr", f"row={row}", "--text", text], "stdin": ""}


def slack_dm(**fields) -> str:
    payload = {"type": "slack.message", "kind": "dm", "channel_id": "D0DIRECT001", "thread_ts": "1792842193.708029", "ts": "1793159602.693499", "user": "U05FTPXU8PP", "from_claude": False, "text": "Meant to approve 43807"} | fields
    return f'<channel source="plugin:cc-slack:cc-slack" subject_id="50c3b930" type="slack.message">\n{json.dumps(payload)}\n</channel>'


def test_a_prompt_on_the_communicator_adds_a_row_with_its_full_text(communicator: Window) -> None:
    [result] = communicator.prompt("keep the report | the nightly one\nand the weekly")

    [row] = communicator.rows()
    assert re.fullmatch(r"\| m1 \| \w{3} \d{1,2} \d{1,2}:\d{2} [AP]M \| keep the report \\\| the nightly one<br>and the weekly \|  \| open \|", row)
    assert f"`{owner_ledger.LR} owner mark m1 responded|addressed [--routed <seq>] --drive brook-drive`" in result.message


def test_rows_continue_the_hand_ledger_and_keep_it(communicator: Window) -> None:
    communicator.seed(HAND_LEDGER)

    communicator.prompt("first")
    communicator.prompt("second")

    text = communicator.ledger.read_text()
    assert text.startswith(HAND_LEDGER)
    assert text.count("| id | time | message | routed | state |") == 1
    assert [row.split(" | ")[0] for row in communicator.rows()] == ["| m1", "| m2"]


def test_a_credential_in_a_prompt_never_reaches_the_ledger(communicator: Window) -> None:
    communicator.prompt("use ghp_abcdefghijklmnopqrstuvwxyz0123 for this")

    [row] = communicator.rows()
    assert "ghp_" not in row and "[credential redacted]" in row


@pytest.mark.parametrize("text", ["/compact", "<task-notification>done</task-notification>", '<cross-session-message from="uds:/tmp/s.sock">p3 DONE</cross-session-message>'])
def test_what_the_owner_did_not_say_adds_no_row(communicator: Window, text: str) -> None:
    assert communicator.prompt(text) == []
    assert not communicator.ledger.exists()


def test_a_subagent_prompt_adds_no_row(communicator: Window) -> None:
    assert communicator.prompt("keep the nightly report", agent_id="a1b2c3") == []
    assert not communicator.ledger.exists()


def test_stop_with_an_open_row_refuses_naming_the_row_and_the_verb(communicator: Window) -> None:
    communicator.seed(owner_ledger.HEADER + OPEN)

    [result] = communicator.stop()

    assert result.action == Action.block
    assert f"`{owner_ledger.LR} owner mark <row> responded|addressed [--routed <seq>] --drive brook-drive`" in result.message
    assert "m1 Nov 2 8:05 PM: keep the nightly report" in result.message


def test_the_marks_the_hooks_name_run_the_packs_own_lr() -> None:
    assert owner_ledger.LR == Path(__file__).parents[1] / "bin" / "lr"
    assert owner_ledger.LR.exists()


def test_marked_rows_allow_stop(communicator: Window) -> None:
    communicator.seed(HAND_LEDGER + "\n" + owner_ledger.HEADER + MARKED)

    assert communicator.stop() == []


def test_hand_rows_alone_allow_stop(communicator: Window) -> None:
    communicator.seed(HAND_LEDGER)

    assert communicator.stop() == []


def test_a_drive_with_no_ledger_yet_allows_stop(communicator: Window) -> None:
    assert communicator.stop() == []


def test_the_rows_lr_marks_are_the_rows_the_stop_hook_reads() -> None:
    assert owner_ledger.open_rows(TESTDATA / "ledger.md") == [
        "m1 Nov 2 8:05 PM: keep the nightly report",
        r"m2 Nov 2 8:06 PM: one \| two<br>three",
        "m12 Nov 2 8:07 PM: release everything",
    ]
    assert owner_ledger.open_rows(TESTDATA / "marked.md") == ["m12 Nov 2 8:07 PM: release everything"]
    assert owner_ledger.open_rows(TESTDATA / "added.md")[-1] == r"m13 Nov 2 9:10 PM: a pick \| on the board<br>and a line"


def test_an_owner_slack_dm_adds_one_row_with_its_slack_time(communicator: Window) -> None:
    communicator.queued(slack_dm())

    communicator.fire(PostToolUseEvent, tool_name="Bash", tool_input={"command": "ls"})
    communicator.fire(PostToolUseEvent, tool_name="Bash", tool_input={"command": "ls"})

    assert communicator.rows() == ["| m1 | Oct 27 8:53 PM | Meant to approve 43807 |  | open |"]


@pytest.mark.parametrize("fields", [{"user": "U01OTHER"}, {"from_claude": True}, {"channel_id": "C0TEAM0001"}])
def test_a_slack_message_that_is_not_the_owners_dm_adds_no_row(communicator: Window, fields: dict) -> None:
    communicator.queued(slack_dm(**fields))

    communicator.fire(PostToolUseEvent, tool_name="Bash", tool_input={"command": "ls"})

    assert not communicator.ledger.exists()


def test_an_owner_slack_dm_in_the_turn_reaches_the_reminder_judge(communicator: Window, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(owner_returns, "dm_owed", lambda _evt: {"replies": [], "holder_session": SESSION})
    communicator.queued(slack_dm(text="you forgot the slack root again"))

    assert owner_returns.owner_words(communicator.event(StopEvent)) == "you forgot the slack root again"


def test_the_coordinator_gets_neither_hook(drive: Path) -> None:
    coordinator = Window(drive, coordinator=True)

    assert coordinator.prompt("keep the nightly report") == []
    assert not coordinator.ledger.exists()

    coordinator.seed(owner_ledger.HEADER + OPEN)
    assert coordinator.stop() == []


def test_a_session_outside_a_drive_gets_neither_hook(drive: Path) -> None:
    session = Window(drive, active=False)

    assert session.prompt("keep the nightly report") == []
    assert not session.ledger.exists()

    session.seed(owner_ledger.HEADER + OPEN)
    assert session.stop() == []


def test_ask_user_question_is_refused_on_the_communicator_with_both_routes(communicator: Window) -> None:
    [result] = communicator.fire(PreToolUseEvent, tool_name="AskUserQuestion", tool_input=ASKED)

    assert result.action == Action.block
    assert "`cc-present push`" in result.message and "`cc-present ask <card>`" in result.message


def test_ask_user_question_passes_outside_the_communicator(drive: Path) -> None:
    assert Window(drive, coordinator=True).fire(PreToolUseEvent, tool_name="AskUserQuestion", tool_input=ASKED) == []
    assert Window(drive, active=False).fire(PreToolUseEvent, tool_name="AskUserQuestion", tool_input=ASKED) == []


def test_an_owner_prompt_posts_its_row_as_one_owner_record_and_registers_one_answer(communicator: Window, bus: Bus, notes: Notes) -> None:
    communicator.prompt("hook should just do it for you")

    [row] = communicator.rows()
    at = row.split(" | ")[1]
    assert bus.posts() == [owner_record("m1", "hook should just do it for you")]
    [answer] = notes.added
    assert {"--label=from:owner", "--label=program:brook"} <= set(answer)
    assert answer[-2:] == ("--", f"What did the owner say at {at}?")
    assert next(flag for flag in answer if flag.startswith("--body=")).startswith("--body=hook should just do it for you\n\nOwner, ")


def test_a_repeat_delivery_of_one_prompt_records_nothing(communicator: Window, bus: Bus, notes: Notes) -> None:
    communicator.prompt("hook should just do it for you")

    assert communicator.prompt("hook should just do it for you") == []

    assert len(communicator.rows()) == len(bus.posts()) == len(notes.added) == 1


def test_the_same_words_after_their_row_is_marked_are_a_new_message(communicator: Window, bus: Bus, notes: Notes) -> None:
    communicator.prompt("go")
    communicator.ledger.write_text(communicator.ledger.read_text().replace("|  | open |", "| 346705 | responded |"))

    communicator.prompt("go")

    assert [post["argv"][8] for post in bus.posts()] == ["owner:m1", "owner:m2"] and len(notes.added) == 2


def test_a_credential_reaches_neither_the_owner_record_nor_the_answer(communicator: Window, bus: Bus, notes: Notes) -> None:
    communicator.prompt("use ghp_abcdefghijklmnopqrstuvwxyz0123 for this")

    assert not any("ghp_" in word for word in bus.posts()[0]["argv"]) and not any("ghp_" in flag for flag in notes.added[0])


def test_a_coordinator_window_posts_and_registers_nothing(drive: Path, bus: Bus, notes: Notes) -> None:
    assert Window(drive, coordinator=True).prompt("hook should just do it for you") == []

    assert bus.posts() == [] and notes.added == []


def test_an_owner_slack_dm_row_posts_its_owner_record(communicator: Window, bus: Bus, notes: Notes) -> None:
    communicator.queued(slack_dm())

    communicator.fire(PostToolUseEvent, tool_name="Bash", tool_input={"command": "ls"})

    assert bus.posts() == [owner_record("m1", "Meant to approve 43807")] and notes.added == []
