from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

BOARDS = Path(__file__).parent / "fixtures" / "boards"
STATE = BOARDS / "state"
NO_DRIVES = BOARDS / "config"
PROGRAM = "brook"
REPO = "/repo"
FIDELITY_LANES = ("fidelity-desk", "fidelity-desk-2")
SCOPE_KEY = "rule/5c0e9a1"
SCOPE_WORDS = "a note started on the phone and then edited on the laptop is out of scope"
UPDATE_WORDS = "download the update in the background, show the banner, and only restart when i click it, and only between edits"
SWEEPS_LOG = json.dumps([{"id": "e" * 40, "title": f"sweeps: {PROGRAM}"}, {"id": "f" * 40, "title": "sweeps: another-program"}])
GLOSSARY = "note\nlaptop\nphone\ncloud\nshare link\nbanner\nupdate\nbackup\nserver\nteam\n"
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
    "r1-op2-rest.json",
    "r2-card-r1-v1.json",
    "r2-card-r1-v2.json",
    "r2-card-r1-v8.json",
    "answer-only-to-change.json",
    "default-in-option.json",
    "confirm-adds-clause.json",
    "title-not-question.json",
    "r3-titles.pre-grade.json",
    "r3-confirm-row-v1.json",
    "r3-confirm-loops-v1.json",
    "r3-spikes-queue-v1.json",
    "r3-evict-v2.json",
    "unverified-approval.json",
)

HEREDOC = (BOARDS / "clean-card.json").read_text()
HEREDOC_SHA = hashlib.sha256(HEREDOC.encode()).hexdigest()[:12]
BREACHES = {
    "r1-card-op6-ruled.json": r"n6-confirm: the confirm does not quote[\s\S]*card-n6: the title does not end",
    "r1-op2-rest.json": r"op2-rest: decision text outside a choice or approval",
    "r2-card-r1-v1.json": r"the title does not end in a question mark[\s\S]*a drive word, \"lane\"",
    "r2-card-r1-v2.json": r"r2-r1-n3-confirm: the confirm does not quote[\s\S]*r2-r1: a choice of how something works has no none option",
    "r2-card-r1-v8.json": r"r2-r1-n3-confirm: the confirm does not quote[\s\S]*r2-r1-n11: decision text outside",
    "answer-only-to-change.json": r"restore-rest: decision text outside a choice or approval, \"Answer only to change\"",
    "default-in-option.json": r"reader-pick: a default sits in option text",
    "confirm-adds-clause.json": r"update-confirm-ok: the confirm does not quote your words verbatim",
    "title-not-question.json": r"share-open: the title does not end in a question mark",
    "r3-titles.pre-grade.json": r"r3-build: the title does not end[\s\S]*r3-answers: the title does not end",
    "r3-confirm-row-v1.json": r"r3-confirm-row: a choice of how something works has no none option[\s\S]*not asked as whether",
    "r3-confirm-loops-v1.json": r"has no none option[\s\S]*names \"held copy\"[\s\S]*a drive word, \"mount lease\"",
    "r3-spikes-queue-v1.json": r"cites no path:line @ ref[\s\S]*a plan row number, \"C77\"[\s\S]*a PR number, \"#4120\"",
    "r3-evict-v2.json": r"r3-evict-how: a choice of how something works has no none option",
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
