from __future__ import annotations

from datetime import datetime

from livedash import Context, view

PAGE = 500


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
    wanted = {record["seq"] for record in opened}
    since = min(record["at"] for record in opened)
    return {seq for record in records(ctx, since) for seq in (record.get("re"), record.get("resolves")) if seq in wanted and record["seq"] > seq}


def expired(record: dict, moment: datetime, within: str) -> bool:
    return moment - view.stamp(record["at"]) > view.window(within)
