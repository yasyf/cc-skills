"""Page through cci records in sequence order for bus.py, standing.py, ledger.py, and desk-runner.py, and read which PRs open `hold` records name."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

PAGE_BYTES = 16000
CLAIM_KINDS = ("opened", "head")
INCIDENT_LANE = re.compile(r"^(?:inc|incident|outage)-")
FIX_LANE = re.compile(r"-(?:fix(?:-\d+)?|opus|case\d+)$")
MECHANISM_TEXT = re.compile(r"^(?P<mark>MECHANISM|CANDIDATE)\b")
SETTLING_TEXT = re.compile(r"^(?:OPENED|BLOCKER|READY|NOT-OURS)\b")
TEXT_KINDS = ("report", "incident", "state")
SETTLING_KINDS = frozenset({*CLAIM_KINDS, "ready", "blocker", "blocked", "fix-live", "not-ours", "stopped", "stand-down"})


def records(run: Callable[[list[str]], str], drive: str, *filters: str, since: int | str = 0) -> list[dict]:
    """Every record on `drive` after `since`, a sequence or any `cci tail --since` time, that matches the `cci tail` filters, paged by sequence; touches no cursor."""
    found: list[dict] = []
    while True:
        out = run(["cci", "tail", "--drive", drive, "--since", str(since), "--json", "--budget", str(PAGE_BYTES), *filters])
        page = [json.loads(line) for line in out.splitlines() if line.strip()]
        if not page:
            return found
        found += page
        since = page[-1]["seq"]


def held_prs(run: Callable[[list[str]], str], drive: str) -> dict[str, int]:
    """Each PR an open `hold` record on `drive` names, mapped to that record's seq; a `lift --resolves <seq>` closes the record and frees the PR.

    `cci tail --kind=hold` also returns the caller's peer mail of any kind and drive, which carries no status, so only the drive's hold records count."""
    holds = [record for record in records(run, drive, "--kind=hold") if record["kind"] == "hold" and record["drive"] == drive]
    return {str(pr): record["seq"] for record in holds if record["status"] == "open" for pr in record["refs"].get("prs", [])}


def mechanism_mark(record: dict) -> str:
    """MECHANISM or CANDIDATE when a lane's record names its incident's mechanism: a `mechanism` record, or a `report`, `incident`, or `state` whose text starts with either word."""
    if record["kind"] == "mechanism":
        return "MECHANISM"
    found = MECHANISM_TEXT.match(record.get("text") or "") if record["kind"] in TEXT_KINDS else None
    return found["mark"] if found else ""


def settles_mechanism(record: dict) -> bool:
    """Whether a lane's record answers its mechanism: a PR, a blocker, or the lane's end, by kind or by a text record starting OPENED, BLOCKER, READY, or NOT-OURS."""
    return record["kind"] in SETTLING_KINDS or (record["kind"] in TEXT_KINDS and bool(SETTLING_TEXT.match(record.get("text") or "")))
