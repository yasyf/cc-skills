from __future__ import annotations

from datetime import datetime

from livedash import Context, view

PAGE = 500
CLOSERS = {
    "hold": frozenset({"lift"}),
    "blocker": frozenset({"withdraw", "answer", "done", "unblock"}),
    "blocked": frozenset({"unblock", "answer", "done"}),
    "defect": frozenset({"fix-live", "done"}),
}


def records(ctx: Context, since: str | None, **query) -> list[dict]:
    found: list[dict] = []
    while True:
        page = ctx.cci("records", since_time=since, limit=PAGE, **({"since": found[-1]["seq"]} if found else {}), **query)
        found += page
        if len(page) < PAGE:
            return found


def answered(ctx: Context, opened: list[dict]) -> set[int]:
    if not opened:
        return set()
    kinds = {record["seq"]: record["kind"] for record in opened}
    found = set()
    for record in records(ctx, min(record["at"] for record in opened)):
        if (seq := record.get("resolves")) in kinds and record["seq"] > seq:
            found.add(seq)
        if (seq := record.get("re")) in kinds and record["seq"] > seq and record["kind"] in CLOSERS.get(kinds[seq], frozenset({record["kind"]})):
            found.add(seq)
    return found


def expired(record: dict, moment: datetime, within: str) -> bool:
    return moment - view.stamp(record["at"]) > view.window(within)
