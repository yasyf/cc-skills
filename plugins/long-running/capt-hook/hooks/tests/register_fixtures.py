from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from captain_hook import TranscriptFixture

from ..compaction_handoff import CompactionState, script
from .handoff_fixtures import OPUS, assistant, user

FIXTURES = Path(__file__).parent / "fixtures"
STATES = FIXTURES / "register"
OWNER_QUESTION = FIXTURES / "owner-question"
GO_PATH = FIXTURES / "register-records" / "go.md"
DRIVE_CONFIG = {"CLAUDE_CONFIG_DIR": str(FIXTURES / "register-config"), "CLAUDE_LONG_RUNNING_DRIVE": "brook-drive"}
CWD = "/repo"
ACTIVE = [CompactionState(active=True, slug="brook")]
ROWS = f"{sys.executable} {script('rulings.py')} rows --drive brook-drive"
LEDGER = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
ROWED = "8b1048a2f5ba37d1580a1db0848e6e8f13447cc3"
UNROWED = "1dfd09d6b2c4e8f0a1b3c5d7e9f1a2b4c6d8e0f2"
SHA = "d9dc7e919c7d44165d8ae1c7da1add0973cd55c3"
ROWS_BY_KEY = {
    "rule/8b1048a": {"words": "The merge refusal names no next command.", "answer": json.dumps([ROWED]), "kind": "scope", "status": "in-force"},
    "rule/0a1b2c3": {
        "words": "No laptop writes a job the server started, for any reason.",
        "answer": json.dumps(["0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3"]),
        "kind": "scope",
        "names": json.dumps(["relay slot", "queue poll", "roster refresh"]),
        "status": "in-force",
    },
    "rule/7e8f901": {
        "words": "Run every scenario before the flag reaches anyone past the first cohort.",
        "answer": json.dumps(["7e8f901a2b3c4d5e6f708192a3b4c5d6e7f80910"]),
        "kind": "gate",
        "blocks": json.dumps(["widen", "1.7"]),
        "status": "owed",
    },
    "rule/4d5e6f7": {
        "words": "The export never needs to know about laptop-only reports.",
        "answer": json.dumps(["4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70"]),
        "kind": "scope",
        "names": json.dumps(["lookup table", "owner lookup"]),
        "status": "in-force",
    },
}
CLASSIFIED = "c4936ff0405c2d0f"


def register(state: str) -> str:
    return json.dumps({"ledger": LEDGER, "program": "brook", "state_dir": str(STATES / state), "rows": ROWS_BY_KEY, "classified": [CLASSIFIED]})


def shown(answer: str) -> str:
    return json.dumps({"id": answer, "title": "Does the merge refusal name a next command?", "body": "No"})


ANSWER_LIST = f"ccn -R {CWD} answer list"
CHAT_QUESTION = "what is this record repair step? the server has no need to know the chat format"


def answer_show(token: str) -> str:
    return f"ccn -R {CWD} answer show {token}"


def readings(state: str, *answers: tuple[str, str | tuple[int, str]]) -> dict[str, str | tuple[int, str]]:
    return {ROWS: register(state), "cci post": "#1", ANSWER_LIST: "[]", **{answer_show(token): outcome for token, outcome in answers}}


def chat(prompt: str, *replies: str, queued: bool = False) -> TranscriptFixture:
    asked = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": prompt}} if queued else user(prompt)
    return TranscriptFixture([asked, *(assistant(OPUS, (10, 0, 1000), content=[{"type": "text", "text": text}]) for text in replies)])


def fixture(name: str) -> str:
    return (OWNER_QUESTION / f"{name}.md").read_text()


def reply(block: str, body: str) -> str:
    return f"cc-present reply --block {block} --body {shlex.quote(body)}"
GATE_OWED = {"stops": [{"key": "gate:rule/7e8f901", "text": "gate row rule/7e8f901 is owed and blocks widen"}], "blockers": []}
TWO_STEPS = {"stops": [{"key": "steps", "text": "two steps"}], "blockers": []}
CLEAR = {"stops": [], "blockers": []}
DURING_INCIDENT = {"stops": [], "blockers": [{"key": "ungraded:41555", "text": "#41555 merged at 2026-10-23T08:00+00:00 and its head has no fidelity ok"}]}
OPEN_FIX = json.dumps([{"incident_id": "inc-1", "status": "open", "opened_at": "2999-01-01T00:00:00+00:00", "facts": {"repair_pr": 41555}}])
CUT_HELD = r"^This cut is refused"
WIDEN_HELD = (
    r"^This flag widening is refused while the register's cut rules below hold\. Clear each one, then run it again\.\n~{12}\n"
    r"- gate row rule/7e8f901 is owed and blocks widen\n~{12}$"
)
