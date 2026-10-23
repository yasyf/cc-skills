from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import register_gates
from hooks.compaction_handoff import CompactionState
from hooks.tests.register_fixtures import LEDGER, ROWS

SESSION = "0123456789abcdef"
DRIVE = "brook-drive"
CLASSIFIED = "c4936ff0405c2d0f"
UNCLASSIFIED = "629293ae14388dc9"


class Root:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.state_dir = home / "scratch" / "brook"
        (self.state_dir / "register").mkdir(parents=True)
        self.session_dir = home / "session"
        self.calls: list[tuple[str, ...]] = []
        self.rows: str | tuple[int, str] = json.dumps(
            {"ledger": LEDGER, "program": "brook", "state_dir": str(self.state_dir), "rows": {}, "classified": [CLASSIFIED]}
        )
        CompactionState(active=True, slug="brook").save(self.event(PostToolUseEvent, tool_name="Bash"))

    def event(self, cls, **raw):
        payload = {"session_id": SESSION, "cwd": str(self.home)}
        return cls(_raw=payload | raw, ctx=build_context(session_dir=self.session_dir))

    def run(self, args, *pargs, **kwargs) -> subprocess.CompletedProcess:
        argv = tuple(str(arg) for arg in args)
        self.calls.append(argv)
        outcome = self.rows if argv[: len(ROWS.split())] == tuple(ROWS.split()) else "#1"
        code, out, err = (0, outcome, "") if isinstance(outcome, str) else (outcome[0], "", outcome[1])
        if kwargs.get("check") and code:
            raise subprocess.CalledProcessError(code, args, out, err)
        return subprocess.CompletedProcess(args, code, stdout=out, stderr=err)

    @contextmanager
    def shell(self):
        with mock.patch.object(subprocess, "run", self.run):
            yield

    def fire(self, cls, **raw) -> list[str]:
        with self.shell():
            return [result.message for result in fire(register_gates, self.event(cls, **raw))]

    def bash(self, cls, command: str) -> list[str]:
        return self.fire(cls, tool_name="Bash", tool_input={"command": command})

    def blockers(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[:2] == ("cci", "post") and "blocker" in call]

    def question(self, digest: str, text: str, blocks: list[str]) -> Path:
        path = self.state_dir / "register" / f"question-{digest}.json"
        path.write_text(json.dumps({"digest": digest, "text": text, "term": "", "sentence": "", "rows": [], "blocks": blocks}))
        return path


@pytest.fixture
def root(tmp_path, monkeypatch) -> Root:
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / f"{DRIVE}.json").write_text(json.dumps({"drive": DRIVE, "sessions": [SESSION], "cci_drive": DRIVE}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    return Root(tmp_path)


def option(call: tuple[str, ...], name: str) -> str:
    return call[call.index(name) + 1]


def test_an_unclassified_owner_capture_posts_one_blocker_and_never_refuses(root):
    captures = [
        {"digest": CLASSIFIED, "at": "Oct 23 4:12 AM", "session": SESSION, "source": "prompt", "text": "rename the export button"},
        {"digest": UNCLASSIFIED, "at": "Oct 23 4:20 AM", "session": SESSION, "source": "prompt", "text": "keep the nightly report"},
    ]
    (root.state_dir / "owner-prompts.jsonl").write_text("".join(json.dumps(capture) + "\n" for capture in captures))
    go = "cci post --lane root --kind go --to walker --text 'cut web'"

    assert root.bash(PreToolUseEvent, go) == []
    assert root.bash(PreToolUseEvent, go) == []

    [blocker] = root.blockers()
    assert option(blocker, "--drive") == DRIVE
    assert option(blocker, "--lane") == "root"
    assert option(blocker, "--to") == "root"
    assert option(blocker, "--text").startswith(f'Owner capture {UNCLASSIFIED} (Oct 23 4:20 AM) has no row in register brook: "keep the nightly report".')


def test_a_register_read_error_passes_the_call_and_posts_one_blocker(root):
    root.rows = (1, "ccn: no ledger titled register: brook")
    go = "cci post --lane root --kind go --to walker --text 'cut web'"

    assert root.bash(PreToolUseEvent, go) == []
    assert root.bash(PreToolUseEvent, go) == []
    assert root.fire(PreToolUseEvent, tool_name="Agent", tool_input={"name": "lane-x", "prompt": "Fix it."}) == []

    texts = [option(blocker, "--text") for blocker in root.blockers()]
    assert [text.split()[0] for text in texts] == ["register_gate", "owner_question_gate"]
    assert all("could not read the register (ccn: no ledger titled register: brook)" in text for text in texts)


def test_a_reply_on_a_block_closes_only_the_question_asked_on_it(root):
    loops = root.question("5f1d2c3b4a596877", "what is a relay slot? why is this here?", ["c7-timers"])
    row = root.question("6e2f3d4c5b6a7988", "why does it need this lookup?", ["c6-lookup"])
    chat = root.question("0123456789abcdef", "what is the merge card?", [])

    root.bash(PostToolUseEvent, "cc-present reply --block c7-timers --body 'No laptop writes a server-started job.'")

    assert json.loads(loops.read_text())["closed"] == "reply c7-timers"
    assert "closed" not in json.loads(row.read_text())
    assert "closed" not in json.loads(chat.read_text())


def test_the_turn_end_closes_chat_questions_and_leaves_board_questions_open(root):
    loops = root.question("5f1d2c3b4a596877", "what is a relay slot? why is this here?", ["c7-timers"])
    chat = root.question("0123456789abcdef", "what is the merge card?", [])

    root.fire(StopEvent)

    assert json.loads(chat.read_text())["closed"] == "turn end"
    assert "closed" not in json.loads(loops.read_text())


def test_a_closed_question_no_longer_gates_a_writer_spawn(root):
    root.question("0123456789abcdef", "what is the merge card?", [])
    spawn = {"name": "lane-x", "prompt": "Lane lane-x: fix the flaky api test."}

    [refusal] = root.fire(PreToolUseEvent, tool_name="Agent", tool_input=spawn)
    root.fire(StopEvent)

    assert refusal.startswith("This reply or brief does not quote the owner's words.")
    assert root.fire(PreToolUseEvent, tool_name="Agent", tool_input=spawn) == []
