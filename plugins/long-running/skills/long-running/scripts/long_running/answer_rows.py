"""The owner's board answers as ccn answer rows, one per answer board-watch posts, labelled by board, card and block so a card's rows read back before it leaves the board.

`present` runs `cc-present <argv>` and `ccn` runs `ccn -R <drive checkout> <argv>`; each returns stdout and raises when the command fails.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

ROW = "board-answer"
OWNER = "from:owner"
PACIFIC = ZoneInfo("America/Los_Angeles")
PERMISSION_CARD = re.compile(r"slack:([A-Z0-9]+):(\d+\.\d+)?:[0-9a-f]{12}")
SLACK_LABELS = {"Yes": "slack-grant:", "No": "slack-revoke:"}
TITLE_BYTES = 256
ELLIPSIS = "…"

Run = Callable[[list[str]], str]


def ccn_title(text: str) -> str:
    """`text` as a `ccn answer add` TITLE: whole when it fits ccn's byte cap, else cut at the last word that fits and marked with an ellipsis, so every caller keeps the whole text in the body."""
    if len(text.encode()) <= TITLE_BYTES:
        return text
    kept = text.encode()[: TITLE_BYTES - len(ELLIPSIS.encode())].decode(errors="ignore")
    return kept.rsplit(None, 1)[0] + ELLIPSIS


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


def slack_labels(card: dict, event: dict) -> tuple[str, ...]:
    """The label a pick on a Slack permission card records, read from the label the board showed on the tapped option: `slack-grant:` for Yes and `slack-revoke:` for No, naming the card's channel, and its thread when the card named one."""
    if (place := PERMISSION_CARD.fullmatch(card.get("id", ""))) is None or event.get("other"):
        return ()
    choice = next((child for child in card.get("children", []) if child.get("id") == event.get("blockId")), {})
    shown = [option.get("label") for option in choice.get("options", []) if option.get("id") in (event.get("optionIds") or [])]
    if len(shown) != 1 or (prefix := SLACK_LABELS.get(shown[0])) is None:
        return ()
    channel, thread = place.groups()
    return (f"{prefix}{channel}/{thread}" if thread else f"{prefix}{channel}",)


def card_words(card: dict) -> str:
    """The card's summary, markdown, prompts and option text on one line, so a search for a pid or script the card names finds its answer."""
    children = card.get("children", ())
    words = [card.get("summary"), *(child.get(name) for child in children for name in ("md", "prompt")), *(option.get("hint") for child in children for option in child.get("options", ()))]
    return " ".join(" ".join(word for word in words if word).split())


def add_argv(program: str, session: str, card: str, block: str, title: str, said: str, words: str, moment: datetime, proof: str, anchors: list[str], granted: tuple[str, ...]) -> list[str]:
    """The `ccn answer add` argv for one answer: the card's question as the title, cut to ccn's cap, and the owner's words verbatim, the question, what the card `said`, the card and block ids, the board and the Pacific time as the body, after the `anchors` of each vocabulary topic it names and the `granted` Slack grant labels. The process guard finds a tap on a card naming a process by searching these words, then reads the tap and what the card said from the board's own log."""
    when = moment.astimezone(PACIFIC).strftime("%b %-d %-I:%M %p PT")
    card_said = f"Card said: {said}\n" if said else ""
    body = f"{words}\nBoard question: {title}\n{card_said}Card {card}, block {block}, board {session}, {when}."
    labels = (*granted, OWNER, ROW, f"program:{program}", f"board-session:{session}", f"board-card:{card}", f"board-block:{block}", proof)
    return ["answer", "add", ccn_title(title), "--body", body, *anchors, *(arg for label in labels for arg in ("--label", label)), "--json"]


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
