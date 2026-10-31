from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

BOARDS = Path(__file__).parent / "fixtures" / "boards"
STATE = BOARDS / "state"
PROGRAM = "brook"
REPO = "/repo"
FIDELITY_LANES = ("fidelity-desk", "fidelity-desk-2")
SCOPE_KEY = "rule/5c0e9a1"
SCOPE_WORDS = "a note started on the phone and then edited on the laptop is out of scope"
UPDATE_WORDS = "download the update in the background, show the banner, and only restart when i click it, and only between edits"
SWEEPS_LOG = json.dumps([{"id": "e" * 40, "title": f"sweeps: {PROGRAM}"}, {"id": "f" * 40, "title": "sweeps: another-program"}])
GLOSSARY = "note\nlaptop\nphone\ncloud\nshare link\nbanner\nupdate\nbackup\nserver\nteam\n"
ANSWERS = json.dumps(
    [
        {"id": "a" * 40, "title": "When should the upload run?", "body": 'Owner tap on Slack card cards:upload: "Upload after the nightly backup". The night-courier lane carries it.', "tags": ["from:owner"]},
        {"id": "b" * 40, "title": "Keep the held copy?", "body": 'Owner chat: "never a held copy on the laptop"', "tags": ["from:owner"]},
    ]
)
ROWS = json.dumps(
    {
        "ledger": "5" * 40,
        "program": PROGRAM,
        "state_dir": str(STATE),
        "rows": {
            SCOPE_KEY: {"kind": "scope", "words": SCOPE_WORDS, "names": json.dumps(["held copy", "night copier"]), "status": "in-force"},
            "rule/7d1f3b2": {"kind": "order", "words": UPDATE_WORDS, "names": "[]", "status": "in-force"},
        },
        "classified": [],
    }
)
FAILING = (
    "r1-card-op6-ruled.json",
    "r2-card-r1-v1.json",
    "r2-card-r1-v2.json",
    "r2-card-r1-v8.json",
    "confirm-adds-clause.json",
    "title-not-question.json",
    "r3-titles.pre-grade.json",
    "r3-confirm-loops-v1.json",
    "r3-spikes-queue-v1.json",
    "unverified-approval.json",
)

JUDGE = "claude -p"
HEREDOC = (BOARDS / "clean-card.json").read_text()
HEREDOC_SHA = hashlib.sha256(HEREDOC.encode()).hexdigest()[:12]
BREACHES = {
    "r1-card-op6-ruled.json": r"n6-confirm: the confirm does not quote[\s\S]*card-n6: the title does not end",
    "r2-card-r1-v1.json": r"r2-card-r1: the title does not end in a question mark",
    "r2-card-r1-v2.json": r"r2-r1-n3-confirm: the confirm does not quote",
    "r2-card-r1-v8.json": r"r2-r1-n3-confirm: the confirm does not quote",
    "confirm-adds-clause.json": r"update-confirm-ok: the confirm does not quote your words verbatim",
    "title-not-question.json": r"share-open: the title does not end in a question mark",
    "r3-titles.pre-grade.json": r"r3-build: the title does not end[\s\S]*r3-answers: the title does not end",
    "r3-confirm-loops-v1.json": r"names \"held copy\"",
    "r3-spikes-queue-v1.json": r"r3-spikes-queue: a claim about what exists cites no path:line @ ref",
    "unverified-approval.json": r"banner-ok: an approval carries an unverified claim",
}
HELD = {"r3-confirm-loops-v1.json": f"open - {SCOPE_KEY} {BOARDS / 'r3-confirm-loops-v1.json'}:12 held copy\n"}


def board(name: str) -> Path:
    return BOARDS / name


def redraft(name: str) -> str:
    return "clean-card.json" if name == "title-not-question.json" else name.replace(".pre-grade", "").replace(".json", ".redraft.json").replace(".md", ".redraft.md")


def sha12(name: str) -> str:
    return hashlib.sha256(board(name).read_bytes()).hexdigest()[:12]


def held_copy_hit(name: str) -> str:
    return f"open - {SCOPE_KEY} {board(name)}:12 held copy\n"


def fidelity(*shas: str, lane: str = FIDELITY_LANES[0]) -> str:
    return "".join(
        json.dumps({"seq": seq, "lane": lane, "kind": "review", "text": f"fidelity: ok text {sha} checked {SCOPE_KEY}"}) + "\n"
        for seq, sha in enumerate(shas, 1)
    )


def missing(sha: str, *, seq: int = 90, lane: str = FIDELITY_LANES[1]) -> str:
    return json.dumps({"seq": seq, "lane": lane, "kind": "review", "text": f"fidelity: missing text {sha} {SCOPE_KEY} the card offers a held copy"}) + "\n"


def sweeps(minutes_ago: int) -> str:
    stamp = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"id": "e" * 40, "title": f"sweeps: {PROGRAM}", "entries": [{"ts": stamp, "text": "pass 1: keep 3, delete 0"}]})


def judged(phrase: str = "", sentence: str = "Say it in plain words.") -> dict[str, str]:
    answer = {"verdict": "yes" if phrase else "no", "artifact_phrase": phrase, "evidence_phrase": "", "sentence": sentence if phrase else "It reads in plain words."}
    return {JUDGE: json.dumps({"structured_output": answer, "total_cost_usd": 0.01, "usage": {"input_tokens": 1000, "output_tokens": 50}})}
