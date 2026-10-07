from __future__ import annotations

import json
import re
import urllib.request

from livedash import Col, Context, Table, component, view

PRESENT_PORT = re.compile(r"port (?P<port>\d+)")
PASSIVE_BLOCKS = frozenset({"markdown", "code", "diagram", "table", "section"})
CACHE: dict[str, tuple[str, dict]] = {}


def listing(ctx: Context) -> tuple[int, list[dict]]:
    port = int(PRESENT_PORT.search(ctx.run(["cc-present", "sessions"]))["port"])
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/sessions", timeout=ctx.timeout) as response:
        return port, json.loads(response.read())


def outcomes(ctx: Context, board: dict) -> dict:
    marker = f"{board.get('updatedAt')}:{board.get('revision')}:{board.get('eventCount')}"
    if (cached := CACHE.get(board["slug"])) and cached[0] == marker:
        return cached[1]
    found = ctx.json(["cc-present", "outcomes", "--session", board["sessionId"]])
    outcome = found["interactions"]
    facts = {
        "submitted": "submitted" if (outcome.get("submitted") or {}).get("value") else "not submitted",
        "answered": sum(len(outcome.get(kind) or {}) for kind in ("decisions", "choices", "inputs")),
        "asks": sum(not (block["type"].startswith("display.") or block["type"] in PASSIVE_BLOCKS) for block in found["doc"]["blocks"]),
        "closed": bool((outcome.get("closed") or {}).get("value")),
    }
    CACHE[board["slug"]] = (marker, facts)
    return facts


def board_rows(ctx: Context, since: str | None) -> list[dict]:
    port, boards = listing(ctx)
    floor = view.stamp(since)
    rows = []
    for board in boards:
        if floor and (view.stamp(board.get("updatedAt")) or floor) < floor:
            continue
        row = {"key": board["slug"], "cite": f"board:{board['slug']}", "title": board.get("title") or board["slug"], "title_url": f"http://127.0.0.1:{port}/p/{board['slug']}", "status": board["status"], "updated": board.get("updatedAt"), "session": board.get("sessionId")}
        if board["status"] == "open":
            row |= outcomes(ctx, board)
        rows.append(row)
    return rows


def waiting(row: dict) -> bool:
    return row["status"] == "open" and row.get("submitted") != "submitted" and bool(row.get("asks")) and not row.get("closed")


@component("boards", "cc-present boards", every="2m", timeout="60s")
def boards(ctx: Context, *, started_at: str | None = None) -> Table:
    """cc-present boards updated since the drive started; an open, unsubmitted board that asks something reads warn."""
    rows = [row | {"tone": "warn" if waiting(row) else None} for row in board_rows(ctx, started_at)]
    rows.sort(key=lambda row: row["updated"] or "", reverse=True)
    rows.sort(key=lambda row: not waiting(row))
    return Table([Col("title", "Board", "link"), Col("status", "Status", "badge"), Col("submitted", "Submitted", "badge"), Col("answered", "Answered", "num"), Col("updated", "Updated", "age")], rows, note=None if rows else "No boards since the drive started.")
