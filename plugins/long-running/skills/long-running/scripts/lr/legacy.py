"""The records today's producers post, read once per pass from their own cursor into the store's fact rows.

The finder and fidelity jobs post `review` lines, the board posts the owner's `merge-prs`
answers as `owner` records, and root or a go lane names an exempt class on a `go`. Until those producers
post typed `lr.grade` and `lr.tap` records, this reader turns each into the fact row the
typed record would have written, keyed by the head the record names, short sha included, so
:func:`lr.machine.classify` sees the grades and taps the live drive already has. A record
that names no head writes nothing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime

from long_running import grades
from lr.store import Store

CURSOR = "legacy"
KINDS = ("review", "owner", "go", "decide", "decision")
FIDELITY_LANES = ["fidelity-pipeline", "fidelity-desk"]
FINDER = {grades.FINDER_PASS: "clear", grades.FINDER_FAIL: "ask"}
TAPS = {"approved": "tapped", "rejected": "withdrawn"}
EXEMPT = re.compile(r"\[exempt:\s*([^\],;\s]+)", re.IGNORECASE)
PAGE_BYTES = 4_000_000
FIRST_WINDOW = "72h"


def read(run: Callable[[list[str]], str], drive: str, since: int) -> list[dict]:
    rows: list[dict] = []
    start = str(since) if since else FIRST_WINDOW
    while True:
        out = run(["cci", "tail", "--drive", drive, "--since", start, "--json", "--budget", str(PAGE_BYTES), *(f"--kind={kind}" for kind in KINDS)])
        page = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
        rows += page
        if not page or len(out.encode()) < PAGE_BYTES // 2:
            return rows
        start = str(page[-1]["seq"])


def facts_of(record: dict, repo: str, go_lanes: tuple[str, ...] = ()) -> list[tuple[int, str, str, str]]:
    """Each `(pr, head, kind, value)` fact `record` states, empty for a record that grades, taps or exempts nothing."""
    text, lane, kind = record["text"], record["lane"], record["kind"]
    if kind == "review" and grades.finder_desk_line(record):
        grade, head = FINDER.get(grades.finder_grade(text)), grades.graded_head(text)
        return [(int(pr), head, "finder", grade) for pr, _ in grades.repo_prs(record, repo)] if grade and head else []
    if kind == "review" and grades.delegated(lane, FIDELITY_LANES) and text.startswith(grades.FIDELITY_GRADES):
        grade = "pass" if text.startswith(grades.FIDELITY_OK) else "fail"
        head = grades.graded_head(text.split(":", 1)[1])
        return [(int(pr), head, "fidelity", grade) for pr, _ in grades.repo_prs(record, repo)] if head else []
    if kind == "owner":
        attrs = (record.get("fields") or {}).get("attrs") or {}
        if attrs.get("block") == "merge-prs" and attrs.get("verdict") in TAPS and attrs.get("head") and str(attrs.get("row", "")).isdigit():
            return [(int(attrs["row"]), attrs["head"].lower(), "tap", TAPS[attrs["verdict"]])]
        return []
    if not grades.delegated(lane, [grades.ROOT, *go_lanes]):
        return []
    whole = grades.full_text(record)
    out = [(int(pr), sha, "verdict", "accept") for _, pr, sha in grades.overrides(whole)[0]]
    if (exempt := EXEMPT.search(whole)) and grades.is_go(record):
        out += [(int(pr), sha, "exempt", exempt[1].lower()) for pr, sha in grades.go_heads(record).items()]
    return out


def ingest(store: Store, repo: str, records: list[dict], go_lanes: tuple[str, ...] = ()) -> None:
    for record in records:
        at = datetime.fromisoformat(record["at"]).astimezone(UTC)
        for number, head, kind, value in facts_of(record, repo, go_lanes):
            store.put_fact(repo, number, head, kind, value, at)
        store.advance(CURSOR, record["seq"])
