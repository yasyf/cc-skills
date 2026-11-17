"""The first line of a lane stack's Approve card, as the old drive's cards opened: what the stack changes for a user and what waits on his tap. The owner's own words for it, a PR row's `why` from `lane pr add --why`, stand when given; otherwise a small model writes it from the PR bodies and the lane's plan, once per stack at its heads, kept in card-lines.json."""

import json

from pydantic import BaseModel, ValidationError
from spawnllm import BackendCallError, extract_sync

from lane import store

LINES = "card-lines.json"
WRITE_SECONDS = 40
PROMPT = """Write the first line of his Approve card for this stack of pull requests: one plain sentence saying what it changes for a user of the product, then, when the lane's plan or wait names work that waits on this stack merging, one short sentence saying what waits on his tap. Plain words a product owner reads at a glance: no code identifiers, file paths, function names or PR numbers.

The lane: {title}. Its plan: {plan}. What its wait says: {wait}.

The pull requests, bottom first:

{prs}"""


class Line(BaseModel):
    line: str


def written(drive: str, lane: dict, members: list[str], reads: dict[str, dict]) -> str:
    """The owner's why on the stack's bottom row, else the model's line for the stack at these heads, written once and kept; empty when the model gives none."""
    rows = {f"{row['repo']}#{row['number']}": row for row in lane["prs"]}
    if why := (rows.get(members[0]) or {}).get("why"):
        return why
    key = ",".join(f"{member}@{reads[member]['headRefOid']}" for member in members)
    path = store.drive_dir(drive) / LINES
    kept = json.loads(path.read_text()) if path.is_file() else {}
    if key in kept:
        return kept[key]
    prs = "\n\n".join(f"## {reads[member]['title']}\n{reads[member].get('body') or ''}" for member in members)
    prompt = PROMPT.format(title=lane.get("title") or lane["id"], plan=lane.get("says") or lane.get("next") or "none", wait=(lane.get("wait") or {}).get("why") or "none", prs=prs)
    try:
        line = extract_sync(prompt, Line, model="small", timeout=WRITE_SECONDS).line.strip()
    except (BackendCallError, ValidationError, TimeoutError):
        return ""
    path.write_text(json.dumps(kept | {key: line}, ensure_ascii=False) + "\n")
    return line
