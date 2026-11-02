"""The owner's board answers as ccn answer rows, one per answer board-watch posts, labelled by board, card and block so a card's rows read back before it leaves the board.

`present` runs `cc-present <argv>` and `ccn` runs `ccn -R <drive checkout> <argv>`; each returns stdout and raises when the command fails.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

ROW = "board-answer"
OWNER = "from:owner"
PACIFIC = ZoneInfo("America/Los_Angeles")

Run = Callable[[list[str]], str]


def answered(event: dict) -> bool:
    """Whether a board event carries the owner's answer, verdict or words."""
    match event:
        case {"type": "choice.selected"}:
            return bool(event.get("optionIds") or event.get("other"))
        case {"type": "input.submitted" | "feedback.created"}:
            return bool(event.get("text"))
        case {"type": "decision.created"}:
            return bool(event.get("verdict"))
        case {"type": "pack.interaction"}:
            return bool(event.get("payload"))
    return False


def add_argv(program: str, session: str, card: str, block: str, title: str, words: str, moment: datetime, proof: str) -> list[str]:
    """The `ccn answer add` argv for one answer: the card's question as the title, and the owner's words verbatim, the card and block ids, the board and the Pacific time as the body."""
    when = moment.astimezone(PACIFIC).strftime("%b %-d %-I:%M %p PT")
    body = f"{words}\nBoard question: {title}\nCard {card}, block {block}, board {session}, {when}."
    labels = (OWNER, ROW, f"program:{program}", f"board-session:{session}", f"board-card:{card}", f"board-block:{block}", proof)
    return ["answer", "add", title, "--body", body, *(arg for label in labels for arg in ("--label", label)), "--json"]


def rowed(ccn: Run, session: str, card: str | None = None) -> set[str]:
    """The block ids of board `session`, on `card` when given, that have an answer row."""
    filters = ("--label", ROW, "--label", f"board-session:{session}", *(("--label", f"board-card:{card}") if card else ()))
    listed = json.loads(ccn(["answer", "list", *filters, "--limit", "0", "--full", "--json"]) or "[]")
    return {tag.removeprefix("board-block:") for answer in listed for tag in answer.get("tags") or () if tag.startswith("board-block:")}


def newest(shown: dict, blocks: set[str] | None = None) -> dict[str, dict]:
    """The newest owner answer on each block `outcomes --block` lists, limited to `blocks` when given."""
    found: dict[str, dict] = {}
    for answer in shown.get("answers") or []:
        payload = answer["payload"]
        if answer["origin"] == "human" and answered(payload) and (blocks is None or payload["blockId"] in blocks):
            found.setdefault(payload["blockId"], answer)
    return found


def unrowed(present: Run, ccn: Run, session: str, card: str, blocks: set[str] | None = None) -> list[dict]:
    """The newest owner answer on each block of `card`, or of `blocks` when given, that has no answer row."""
    answers = newest(json.loads(present(["outcomes", "--block", card, "--session", session])), blocks)
    if not answers:
        return []
    have = rowed(ccn, session, card)
    return [answer for block, answer in answers.items() if block not in have]
