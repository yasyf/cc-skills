from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from ..compaction_handoff import CompactionState, script

FIXTURES = Path(__file__).parent / "fixtures"
STATES = FIXTURES / "register"
OWNER_QUESTION = FIXTURES / "owner-question"
GO_PATH = FIXTURES / "register-records" / "go.md"
DRIVE_CONFIG = {"CLAUDE_CONFIG_DIR": str(FIXTURES / "register-config"), "CLAUDE_LONG_RUNNING_DRIVE": "brook-drive"}
CWD = "/repo"
ACTIVE = [CompactionState(active=True, slug="brook")]
ROWS = f"{sys.executable} {script('rulings.py')} rows --program brook"
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


def answer_show(token: str) -> str:
    return f"ccn -R {CWD} answer show {token}"


def readings(state: str, *answers: tuple[str, str | tuple[int, str]]) -> dict[str, str | tuple[int, str]]:
    return {ROWS: register(state), "cci post": "#1", **{answer_show(token): outcome for token, outcome in answers}}


def fixture(name: str) -> str:
    return (OWNER_QUESTION / f"{name}.md").read_text()


def reply(block: str, body: str) -> str:
    return f"cc-present reply --block {block} --body {shlex.quote(body)}"
