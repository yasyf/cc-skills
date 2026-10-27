from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from cc_transcript.query import Session
from captain_hook.events import PostToolUseEvent, StopEvent, UserPromptSubmitEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import register_capture
from hooks.compaction_handoff import CompactionState, script

SESSION = "0123456789abcdef"
ROWS = (sys.executable, str(script("rulings.py")), "rows")
SWEEP = (sys.executable, str(script("rulings.py")), "sweep")
SLOT = "A relay slot polls its queue every 30 seconds on workers and laptops"
TIMERS = {
    "id": "c7-timers",
    "type": "choice",
    "prompt": "7. These timers stay in the plan. Which may stay?",
    "options": [
        {"id": "lease", "label": "Workers renew a 90-second lease while a job is mounted"},
        {"id": "queue-poll", "label": SLOT},
    ],
}
LOOKUP = {"id": "c6-lookup", "type": "choice", "prompt": "6. How does the export learn a report's owner?"}
CATCH = "One catch: each laptop's export key is minted against the owner lookup and turned away when that lookup is missing."
REGISTER_ROWS = {
    "rule/0a1b2c3": {"words": "No laptop writes a job the server started, for any reason.", "names": json.dumps(["relay slot", "queue poll"])},
    "rule/4d5e6f7": {"words": "The export never needs to know about laptop-only reports.", "names": json.dumps(["lookup table", "export key"])},
}

def board(*blocks: dict) -> str:
    return json.dumps({"schemaVersion": 1, "doc": {"title": "round 3", "blocks": list(blocks)}, "interactions": {"closed": {"value": False}}})


class Root:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.state_dir = home / "scratch" / "brook"
        self.state_dir.mkdir(parents=True)
        self.transcript = home / "root.jsonl"
        self.transcript.write_text("")
        self.session_dir = home / "session"
        self.calls: list[tuple[str, ...]] = []
        self.routes: dict[tuple[str, ...], str | tuple[int, str]] = {
            ROWS: json.dumps({"ledger": "1a2b3c4d", "program": "brook", "state_dir": str(self.state_dir), "rows": REGISTER_ROWS, "classified": []}),
            ("cc-present", "outcomes"): (1, f'cc-present: no cc-present artifact for session "{SESSION}"; run `cc-present start` first'),
            SWEEP: "open rule/0a1b2c3 plan.md:305 relay slot\n",
        }
        CompactionState(active=True, slug="users-plan-stem-of-brook").save(self.event(PostToolUseEvent, tool_name="Bash"))

    def line(self, entry: dict, at: str) -> None:
        envelope = {"isSidechain": False, "uuid": f"u{len(self.transcript.read_text().splitlines())}", "parentUuid": None, "sessionId": SESSION, "timestamp": at}
        with self.transcript.open("a") as file:
            file.write(json.dumps(envelope | entry) + "\n")

    def typed(self, text: str, at: str) -> None:
        self.line({"type": "user", "message": {"role": "user", "content": text}}, at)

    def queued(self, text: str, at: str) -> None:
        self.line({"type": "attachment", "attachment": {"type": "queued_command", "prompt": text, "commandMode": "prompt"}}, at)

    def event(self, cls, **raw):
        payload = {"session_id": SESSION, "transcript_path": str(self.transcript), "cwd": str(self.home)}
        return cls(_raw=payload | raw, ctx=build_context(transcript=Session.from_path(self.transcript), session_dir=self.session_dir))

    def run(self, args, *pargs, **kwargs) -> subprocess.CompletedProcess:
        argv = tuple(str(arg) for arg in args)
        self.calls.append(argv)
        prefix = max((prefix for prefix in self.routes if argv[: len(prefix)] == prefix), key=len, default=None)
        outcome = self.routes[prefix] if prefix else "#1"
        code, out, err = (0, outcome, "") if isinstance(outcome, str) else (outcome[0], "", outcome[1])
        if kwargs.get("check") and code:
            raise subprocess.CalledProcessError(code, args, out, err)
        return subprocess.CompletedProcess(args, code, stdout=out, stderr=err)

    def fire(self, cls, **raw) -> list[str]:
        with mock.patch.object(subprocess, "run", self.run):
            return [result.message for result in fire(register_capture, self.event(cls, **raw))]

    def prompt(self, text: str) -> list[str]:
        return self.fire(UserPromptSubmitEvent, prompt=text)

    def captures(self) -> list[dict]:
        path = self.state_dir / "owner-prompts.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []

    def questions(self) -> list[dict]:
        return [json.loads(path.read_text()) for path in sorted((self.state_dir / "register").glob("question-*.json"))]


@pytest.fixture
def root(tmp_path, monkeypatch) -> Root:
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "brook-drive.json").write_text(json.dumps({"drive": "brook-drive", "sessions": [SESSION], "program": "brook", "state_dir": str(tmp_path / "scratch" / "brook")}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    return Root(tmp_path)


def digest(text: str) -> str:
    return hashlib.sha256(f"{SESSION}\0{text}".encode()).hexdigest()[:16]


def test_an_owner_prompt_lands_with_its_transcript_stamp_at_the_next_hook(root):
    root.prompt("keep the nightly report")
    assert root.captures() == []

    root.typed("keep the nightly report", "2026-10-23T11:20:00.000Z")
    root.fire(PostToolUseEvent, tool_name="Bash", tool_input={"command": "ls"})
    root.fire(StopEvent)

    assert root.captures() == [
        {"digest": digest("keep the nightly report"), "at": "2026-10-23T11:20:00Z", "session": SESSION, "source": "prompt", "text": "keep the nightly report"}
    ]


@pytest.mark.parametrize("token", ["api-0f3c2b1a-9d8e-4f7a-b6c5-1a2b3c4d5e6f", "ghp_aB3dE6gH9jK2mN5pQ8sT1vW4yZ7", "sk-ant-api03-Zx9Yw8Vu7Ts6Rq5Po4Nm", "Xq7Lp2Rz9Tk4Wm8Nc3Vb6Hj1Gf5Dd0Ss2", "ASIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"])
def test_a_credential_typed_in_a_prompt_is_redacted_in_its_capture(root, token):
    text = f"use {token} for the flags api, and keep 6e74115 on Release-2026-10-26-owner-capture-redaction as the head"
    root.prompt(text)
    root.typed(text, "2026-10-23T11:20:00.000Z")
    root.fire(StopEvent)

    assert [capture["text"] for capture in root.captures()] == ["use [credential redacted] for the flags api, and keep 6e74115 on Release-2026-10-26-owner-capture-redaction as the head"]


def test_a_prompt_whose_line_has_not_landed_waits_for_a_later_hook(root):
    root.prompt("ship the register gates")
    root.fire(PostToolUseEvent, tool_name="Bash", tool_input={"command": "ls"})
    assert root.captures() == []

    root.typed("ship the register gates", "2026-10-23T11:31:00.000Z")
    root.fire(StopEvent)

    assert [capture["at"] for capture in root.captures()] == ["2026-10-23T11:31:00Z"]


def test_a_queued_prompt_takes_its_queued_command_stamp(root):
    root.queued("how many times do we rerun the export?", "2026-10-22T19:43:49.000Z")
    root.prompt("how many times do we rerun the export?")
    root.fire(StopEvent)

    assert [capture["at"] for capture in root.captures()] == ["2026-10-22T19:43:49Z"]


def test_a_what_is_question_marks_its_block_and_names_the_rows_bearing_on_it(root):
    root.routes[("cc-present", "outcomes")] = board(LOOKUP, TIMERS)
    text = "waht is a relay slot? why is this here?"

    [context] = root.prompt(text)

    assert root.questions() == [
        {"digest": digest(text), "text": text, "term": "relay slot", "sentence": SLOT, "rows": ["rule/0a1b2c3"], "blocks": ["c7-timers"]}
    ]
    [revising] = [call for call in root.calls if call[:2] == ("cc-present", "revising")]
    assert revising == ("cc-present", "revising", "c7-timers", "--note", text, "--session", SESSION)
    assert [call[2:4] for call in root.calls if call[: len(SWEEP)] == SWEEP] == [("sweep", "rule/0a1b2c3")]
    assert "open rule/0a1b2c3 plan.md:305 relay slot" in context


def test_a_question_on_a_nested_card_marks_the_card_and_not_its_group(root):
    root.routes[("cc-present", "outcomes")] = board({"id": "round-3", "type": "group", "blocks": [LOOKUP, TIMERS]})

    root.prompt("waht is a relay slot? why is this here?")

    [question] = root.questions()
    assert question["blocks"] == ["c7-timers"]


def test_a_quoted_sentence_is_the_sentence_under_question_with_no_board(root):
    text = f"> {CATCH}\n\nwhat is this workaround and can we drop it"

    root.prompt(text)

    [question] = root.questions()
    assert (question["term"], question["sentence"], question["blocks"], question["rows"]) == ("workaround", CATCH, [], ["rule/4d5e6f7"])
    assert not [call for call in root.calls if call[:2] == ("cc-present", "revising")]


@pytest.mark.parametrize(
    "text",
    ["that is fine, ship it", "what? I meant the merge card", "do we keep the board open"],
)
def test_a_prompt_that_asks_nothing_opens_no_question(root, text):
    assert root.prompt(text) == []
    assert root.questions() == []


def test_why_does_it_need_this_opens_a_question_with_no_term(root):
    root.prompt("why does it need this, honestly?")

    [question] = root.questions()
    assert (question["term"], question["sentence"], question["rows"], question["blocks"]) == ("", "", [], [])
