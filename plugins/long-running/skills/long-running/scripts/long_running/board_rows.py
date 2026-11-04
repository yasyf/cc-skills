"""What the board watch and the rulings read of the owner's board: its rows, verdict records and snapshots."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from zoneinfo import ZoneInfo

ROW_ID = re.compile(r"^merge-row-(?P<pr>\d+)(?:-(?P<sha>[0-9a-f]{7,40}))?$")
ROW_HEAD = re.compile(r"\bcommit (?P<sha>[0-9a-f]{7,40})\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
WORD = re.compile(r"[a-z0-9]+")
LABEL_NUMBER = re.compile(r"^#\d+:\s*")
FIELDS = ("For users", "Differs from plan", "Risk", "Finder", "Build (CI)", "Gate")
PR_FIELDS = ("change", "plan", "risk", "review", "blocker")
PR_GROUPS = "pr-groups"
TEXT_AT = "textAt"
SENTENCE_WORDS = 5
PACIFIC = ZoneInfo("America/Los_Angeles")
SINCE = "since"
OWNER_LANE = "board-watch"
RUNNER_LANE = "desk-runner"
SUPERVISOR_LANE = "supervisor"
SNAPSHOT_TOPIC = "board-rows"
WRITE_TOPIC = "board-write"
MERGE_CARD = re.compile(r"^merge:(\d+):([0-9a-f]{7,40})$")


@dataclass
class Row:
    id: str
    block: str
    pr: str
    head: str
    text_at: str
    label: str
    sentence: str
    facts: dict[str, str]
    words: str = ""
    group: str = ""
    carried: tuple[str, ...] = ()
    required: tuple[str, ...] = FIELDS
    since: dict[str, str] = field(default_factory=dict)
    opener: str = ""
    entry: dict | None = None
    pure: bool = False
    pull: Pull | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pull:
    state: str
    head: str
    committed: str
    base: str = ""
    branch: str = ""


def triage_items(node: object) -> Iterator[tuple[str, dict]]:
    if isinstance(node, dict):
        if node.get("type") == "triage":
            yield from ((node["id"], item) for item in node.get("items", []))
        for value in node.values():
            yield from triage_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from triage_items(value)


def pr_group_rows(node: object) -> Iterator[tuple[str, dict]]:
    if isinstance(node, dict):
        if node.get("type") == PR_GROUPS:
            yield from ((node["id"], group.get("id", ""), pr) for group in node.get("groups", []) for pr in group.get("prs", []))
        for value in node.values():
            yield from pr_group_rows(value)
    elif isinstance(node, list):
        for value in node:
            yield from pr_group_rows(value)


def first_sentence(md: str, fallback: str) -> str:
    sentences = [part.strip() for part in SENTENCE.split(md.strip())]
    return next((part for part in sentences if len(WORD.findall(part.lower())) >= SENTENCE_WORDS), fallback)


def rows_of(block: dict) -> list[Row]:
    rows = []
    for block_id, item in triage_items(block):
        if not (named := ROW_ID.match(item.get("id", ""))):
            continue
        head = ROW_HEAD.search(item.get("hint", ""))
        facts = {fact["label"]: str(fact.get("value", "")).strip() for fact in item.get("facts", [])}
        label = item.get("label", "")
        words = "\n".join([label, item.get("hint", ""), item.get("md", ""), *facts.values()])
        rows.append(Row(item["id"], block_id, named["pr"], head["sha"] if head else "", item.get(TEXT_AT, ""), label, first_sentence(item.get("md", ""), LABEL_NUMBER.sub("", label)), facts, words))
    for block_id, group, pr in pr_group_rows(block):
        facts = {name: str(pr.get(name, "")).strip() for name in PR_FIELDS}
        words = "\n".join([pr.get("title", ""), pr.get("md", ""), *facts.values()])
        rows.append(Row(str(pr["number"]), block_id, str(pr["number"]), pr.get("head", ""), pr.get(TEXT_AT, ""), pr.get("title", ""), first_sentence(facts["change"], facts["change"]), facts, words, group, tuple(pr.get("carried") or ()), PR_FIELDS, pr.get(SINCE) or {}))
    return rows


def pr_of(row_id: str) -> str | None:
    if row_id.isdigit():
        return row_id
    return named["pr"] if (named := ROW_ID.match(row_id)) else None


def attrs(record: dict) -> dict[str, str]:
    return (record.get("fields") or {}).get("attrs") or {}


def verdicts_of_records(records: list[dict]) -> dict[str, dict[str, dict]]:
    """Each block's standing verdict per row, an entry of verdict, head, time, the board session it stands on, and the head a carry came from, from the board-watch `owner` records that carry the row and verdict and the runner's own `board-write` carries, the newest tap time winning and a later record breaking a tie."""
    entries = []
    for record in records:
        found = attrs(record)
        owner = (record["lane"], record["kind"]) == (OWNER_LANE, "owner") and found.get("row") and found.get("verdict")
        carry = record["lane"] in (RUNNER_LANE, SUPERVISOR_LANE) and record.get("topic") == WRITE_TOPIC and found.get("op") == "carry"
        if not (owner or carry):
            continue
        entry = {"verdict": found["verdict"] if owner else "approved", "head": found.get("head", ""), "at": found.get("at") or record["at"], "session": found.get("session", "")}
        if carried := found.get("carried_from" if owner else "from"):
            entry["carriedFrom"] = carried
        entries.append((found["block"], found["row"], entry, record["seq"]))
    verdicts: dict[str, dict[str, dict]] = {}
    for block, row, entry, _ in sorted(entries, key=lambda item: (parsed(item[2]["at"]) or datetime.min.replace(tzinfo=PACIFIC), item[3])):
        verdicts.setdefault(block, {})[row] = entry
    return verdicts


def answered_of_records(records: list[dict]) -> set[str]:
    """Each choice block the owner picked an option on or wrote into, from board-watch `owner` records."""
    return {found["block"] for record in records if (record["lane"], record["kind"]) == (OWNER_LANE, "owner") and (found := attrs(record)).get("event") == "choice.selected" and (found.get("picked") or found.get("other"))}


def snapshot(doc: dict) -> dict:
    """What the runner and the stall watch read of the board's document: each block holding PR rows, each PR row's PR, head and write time, each merge card's PR and head, and each card's choice blocks and PRs."""
    rows = rows_of(doc)
    return {
        "blocks": sorted({row.block for row in rows}),
        "rows": [[row.pr, row.head, row.text_at] for row in rows if row.head],
        "merges": [[found[1], found[2]] for key in block_ids(doc) if (found := MERGE_CARD.match(key))],
        "cards": [[card["id"], sorted(choice["id"] for choice in typed(card, "choice")), sorted(str(found["number"]) for found in typed(card, "pr"))] for card in typed(doc, "card")],
    }


def snapshot_of_records(records: list[dict]) -> dict | None:
    """The newest board snapshot board-watch posted, with the session of the board it shows and empty lists for any part an older board-watch does not post, or None while it has posted none."""
    found = [attrs(record) for record in records if record["lane"] == OWNER_LANE and record.get("topic") == SNAPSHOT_TOPIC and "board" in attrs(record)]
    return {"blocks": [], "rows": [], "merges": [], "cards": []} | json.loads(found[-1]["board"]) | {"session": found[-1]["session"]} if found else None


def block_ids(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            yield node["id"]
        for value in node.values():
            yield from block_ids(value)
    elif isinstance(node, list):
        for value in node:
            yield from block_ids(value)


def typed(node: object, kind: str) -> Iterator[dict]:
    if isinstance(node, dict):
        if node.get("type") == kind:
            yield node
        for value in node.values():
            yield from typed(value, kind)
    elif isinstance(node, list):
        for value in node:
            yield from typed(value, kind)


def parsed(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None
