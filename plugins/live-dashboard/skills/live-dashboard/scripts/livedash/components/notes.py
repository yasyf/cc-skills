from __future__ import annotations

import json

from livedash import Col, Context, Table, component

KINDS = ("plan", "doc", "log", "investigation", "answer")


@component("notes", "cc-notes records", every="5m", timeout="60s")
def notes(ctx: Context, *, kinds: list[str] = ["plan", "doc", "log", "investigation"], label: str | None = None, limit: int = 20) -> Table:
    """cc-notes plans, docs, logs, investigations and answers in the drive checkout, newest first; `label` narrows docs."""
    if unknown := sorted(set(kinds) - set(KINDS)):
        raise ValueError(f"kinds {', '.join(unknown)} are not cc-notes kinds; use {', '.join(KINDS)}")
    rows = []
    for kind in kinds:
        argv = [kind, "list", "--json", *(["--limit", str(limit)] if kind in ("doc", "answer") else []), *(["--label", label] if label and kind == "doc" else [])]
        for item in json.loads(ctx.ccn(*argv) or "[]"):
            rows.append({"key": item["id"], "cite": f"ccn:{item['id'][:8]}", "kind": kind, "title": item.get("title", ""), "status": item.get("status"), "updated": item.get("updated_at"), "title_url": f"/ccn/{item['id']}"})
    rows.sort(key=lambda row: row["updated"] or "", reverse=True)
    return Table([Col("kind", "Kind", "badge"), Col("title", "Title", "link"), Col("status", "Status", "badge"), Col("updated", "Updated", "age")], rows[:limit], note=None if rows else "No records.")
