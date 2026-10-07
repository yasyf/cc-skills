from __future__ import annotations

import json

from livedash import Col, Context, Entry, Feed, Table, component
from livedash.components import cci

KINDS = ("plan", "doc", "log", "investigation", "answer")
ROUTED_KINDS = ["decision", "go", "answer"]
DECIDERS = frozenset({"root", "owner", "main"})
RULING_CHARS = 280


@component("notes", "cc-notes records", question="What did cc-notes record lately for this work?", reads=["ccn"], every="5m", timeout="60s")
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


def drive_answers(ctx: Context, program: str | None, branch: str | None, ids: list[str]) -> list[dict]:
    durable = ["answer", "list", "--json", "--label", "scope:durable", "--limit", "0"]
    found = json.loads(ctx.ccn(*durable, "--label", f"program:{program}") or "[]") if program else []
    found += json.loads(ctx.ccn(*durable, "--branch", branch) or "[]") if branch else []
    if ids:
        found += [answer for answer in json.loads(ctx.ccn("answer", "list", "--json", "--limit", "0") or "[]") if answer["id"][:7] in ids]
    return list({answer["id"]: answer for answer in found}.values())


@component("rulings", "Rulings", question="What did the owner and root rule, and which lanes was each routed to?", reads=["ccn answer list", "cci records"], every="5m", timeout="60s")
def rulings(ctx: Context, *, program: str | None = None, branch: str | None = None, ids: list[str] = [], limit: int = 30) -> Feed:
    """The drive's rulings, newest first: durable cc-notes answers labelled `program:<program>` or scoped to `branch`, and any
    answer whose short id is in `ids`, each with the option the owner picked; then root and owner decision, go and answer
    records on cci since the drive started, each with the lanes it was routed to."""
    entries = []
    for answer in drive_answers(ctx, program, branch, ids):
        picked = (answer.get("body") or "").splitlines()[0] if answer.get("body") else ""
        entries.append(Entry(answer["updated_at"], "owner", f"{answer['title']} → {picked}"[:RULING_CHARS], f"/ccn/{answer['id']}", key=answer["id"], cite=f"ccn:{answer['id'][:7]}"))
    if ctx.facts.get("cci_drive"):
        for record in cci.records(ctx, ctx.facts.get("started_at"), kind=ROUTED_KINDS):
            if record["lane"] in DECIDERS:
                routed = ", ".join(record.get("to") or []) or "every lane"
                entries.append(Entry(record["at"], f"{record['lane']} → {routed}", record["text"][:RULING_CHARS], key=f"cci:{record['seq']}", cite=f"cci:{record['seq']}"))
    entries.sort(key=lambda entry: entry.at, reverse=True)
    return Feed(entries[:limit], note=None if entries else "No rulings yet.")
