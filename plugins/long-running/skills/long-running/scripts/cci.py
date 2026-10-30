"""Page through cci records in sequence order for bus.py, standing.py, ledger.py, and desk-runner.py, and read which PRs open `hold` records name."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

PAGE_BYTES = 4_000_000
PAGE_ROWS = 500
CLAIM_KINDS = ("opened", "head")
INCIDENT_LANE = re.compile(r"^(?:inc|incident|outage)-")
FIX_LANE = re.compile(r"-(?:fix(?:-\d+)?|opus|case\d+)$")
INCIDENT_TAG = re.compile(r"\b([A-Z]{2,})(?:-([0-9A-Z]*\d[0-9A-Z]*)|(\d[0-9A-Z]*))\b")
ANSWER_TEXT = re.compile(r"^(?:OPENED|READY)\b")
ANSWER_KINDS = frozenset({*CLAIM_KINDS, "ready", "fix-live"})
ROOT_ANSWER_KINDS = frozenset({"go", "decide", "decision"})
ROOT_RULINGS = frozenset({"decide", "decision"})
MECHANISM_TEXT = re.compile(r"^(?P<mark>MECHANISM|CANDIDATE)\b")
SETTLING_TEXT = re.compile(r"^(?:OPENED|BLOCKER|READY|NOT-OURS|DONE|STOPPED)\b")
TEXT_KINDS = ("report", "incident", "state")
REPORT_VERDICTS = ("clean", "red", "conflicting", "held")
SETTLING_KINDS = frozenset({*CLAIM_KINDS, "ready", "blocker", "blocked", "fix-live", "not-ours", "stopped", "stand-down", "done"})
TRUNK_TOPIC = "dev-red"
TRUNK_RUNNER = "desk-runner"
TRUNK_STATE = re.compile(r"^(?P<state>DEV-RED|POST-MERGE-RED|DEV-GREEN)\b(?: since (?P<since>\d{1,2}:\d{2} [AP]M))?")
NAMED_PR = re.compile(r"#(\d+)\b")
LEADING_HOLD = re.compile(r"^\W*hold\s+#(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Trunk:
    """The trunk's landing state the runner last posted on the `dev-red` topic: `blocked` while a required landing job is red, since the Pacific time its first red commit landed, and the PR named as its fix, or empty."""

    blocked: bool
    since: str
    fix: str


def lane_report(record: dict) -> bool:
    return record.get("kind") == "report" and (record.get("text") or "").split(" ", 1)[0] in REPORT_VERDICTS


class Tail:
    """A `cci tail` runner whose `records` answers repeat a query, between `remember` and `stop`, from what it read since the last `forget`, so a pass reads each query once. A `--pr` query read from the start keeps what it read for the runner's life and reads only the records after the newest it kept: cci scans every record of the drive for a `--pr` match from the start, and only from a recent seq is that scan cheap, while a record's text, kind, lane and closures never change after it is posted."""

    def __init__(self, run: Callable[[list[str]], str]):
        self.run = run
        self.read: dict[tuple[str, tuple[str, ...], str], list[dict]] | None = None
        self.grown: dict[tuple[str, tuple[str, ...]], list[dict]] = {}

    def __call__(self, argv: list[str]) -> str:
        return self.run(argv)

    def remember(self) -> None:
        self.read = {}

    def forget(self) -> None:
        if self.read is not None:
            self.read = {}

    def stop(self) -> None:
        self.read = None


def records(run: Callable[[list[str]], str], drive: str, *filters: str, since: int | str = 0) -> list[dict]:
    """Every record on `drive` after `since`, a sequence or any `cci tail --since` time, that matches the `cci tail` filters, paged by sequence; a page is the last when it holds fewer than the `PAGE_ROWS` records cci prints per call and fills under half the byte budget, since cci caps a record far below that; touches no cursor. A `Tail` answers a query it read since its last `forget` without running cci."""
    key = (drive, filters, str(since))
    read = run.read if isinstance(run, Tail) else None
    if read is not None and (kept := read.get(key)) is not None:
        return list(kept)
    grown = run.grown if isinstance(run, Tail) and "--pr" in filters and str(since) == "0" else None
    now = datetime.now(timezone.utc)
    found = [record for record in (grown or {}).get((drive, filters), []) if not expired(record, now)]
    since = found[-1]["seq"] if found else since
    while True:
        out = run(["cci", "tail", "--drive", drive, "--since", str(since), "--json", "--budget", str(PAGE_BYTES), *filters])
        page = [json.loads(line) for line in out.splitlines() if line.strip()]
        found += page
        if len(page) < PAGE_ROWS and len(out.encode()) < PAGE_BYTES // 2:
            break
        since = page[-1]["seq"]
    if grown is not None:
        grown[(drive, filters)] = found
    if read is not None:
        read[key] = found
    return list(found)


def expired(record: dict, now: datetime) -> bool:
    return bool(record.get("expires_at")) and datetime.fromisoformat(record["expires_at"]) <= now


def open_holds(run: Callable[[list[str]], str], drive: str) -> list[dict]:
    """Every open `hold` record on `drive`, the only place a hold lives; a record that resolves one closes it.

    `cci tail --kind=hold` also returns the caller's peer mail of any kind and drive, which carries no status, so only the drive's hold records count."""
    return [record for record in records(run, drive, "--kind=hold") if (record["kind"], record["drive"], record["status"]) == ("hold", drive, "open")]


def held_prs(run: Callable[[list[str]], str], drive: str) -> dict[str, int]:
    """Each PR an open `hold` record on `drive` holds, mapped to that record's seq."""
    return {pr: record["seq"] for record in open_holds(run, drive) for pr in held_by(record)}


def held_by(record: dict) -> list[str]:
    """The PR numbers a hold record holds: the one its text leads with as `hold #<n>`, every later `#<n>` being prose, else every PR its refs name."""
    return [found[1]] if (found := LEADING_HOLD.match(record["text"])) else [str(pr).rsplit("#", 1)[-1] for pr in record["refs"].get("prs", [])]


def mechanism_mark(record: dict) -> str:
    """MECHANISM or CANDIDATE when a lane's record names its incident's mechanism: a `mechanism` record, or a `report`, `incident`, or `state` whose text starts with either word."""
    if record["kind"] == "mechanism":
        return "MECHANISM"
    found = MECHANISM_TEXT.match(record.get("text") or "") if record["kind"] in TEXT_KINDS else None
    return found["mark"] if found else ""


def settles_mechanism(record: dict) -> bool:
    """Whether a lane's record answers its mechanism: a PR, a blocker, or the lane's end or DONE, by kind or by a text record starting OPENED, BLOCKER, READY, NOT-OURS, DONE, or STOPPED."""
    return record["kind"] in SETTLING_KINDS or (record["kind"] in TEXT_KINDS and bool(SETTLING_TEXT.match(record.get("text") or "")))


def incident_keys(record: dict) -> set[str]:
    """What names the incident a record is about: its topic, and each incident tag in its topic or text, such as `SAND-5A`, `[SAND-5A]`, or `SAND5A`, all read as `tag:SAND5A`."""
    text = f"{record.get('topic') or ''} {record.get('text') or ''}"
    tags = {f"tag:{found[1]}{found[2] or found[3]}" for found in INCIDENT_TAG.finditer(text)}
    return tags | ({f"topic:{record['topic']}"} if record.get("topic") else set())


def answers_mechanism(record: dict) -> set[str]:
    """The mechanisms a record answers, as keys `incident_keys` gives a mechanism, `seq:<n>` for one it names with `--re`, or `lane:<name>` for each lane a root ruling is addressed to: a lane's PR, ready, or fix-live, or the root's go, decide, or decision; empty for any other record."""
    root = record["lane"] == "root"
    kind = record["kind"]
    if not (kind in ROOT_ANSWER_KINDS if root else kind in ANSWER_KINDS or (kind in TEXT_KINDS and ANSWER_TEXT.match(record.get("text") or ""))):
        return set()
    addressed = {f"lane:{lane}" for lane in record.get("to") or []} if root and kind in ROOT_RULINGS else set()
    return incident_keys(record) | addressed | ({f"seq:{record['re']}"} if record.get("re") else set())


def trunk(records: list[dict]) -> Trunk:
    """The trunk's state from the records on the `dev-red` topic: the landing runner's newest DEV-RED, POST-MERGE-RED, or DEV-GREEN record sets it, and the fix is the PR that the newest record by any other lane names after the runner's last DEV-GREEN, an incident record excepted."""
    records = [record for record in records if record.get("topic") == TRUNK_TOPIC]
    states = [(record, found) for record in records if record["lane"] == TRUNK_RUNNER and (found := TRUNK_STATE.match(record.get("text") or ""))]
    if not states or states[-1][1]["state"] == "DEV-GREEN":
        return Trunk(False, "", "")
    green = max((record["seq"] for record, found in states if found["state"] == "DEV-GREEN"), default=0)
    fixes = [pr for record in records if record["seq"] > green and record["lane"] != TRUNK_RUNNER and record["kind"] != "incident" and (pr := named_pr(record))]
    newest = states[-1][1]
    return Trunk(newest["state"] == "DEV-RED", newest["since"] or "", fixes[-1] if fixes else "")


def named_pr(record: dict) -> str:
    refs = [str(ref).rsplit("#", 1)[-1] for ref in record["refs"].get("prs", [])]
    return next(iter([*refs, *NAMED_PR.findall(record.get("text") or "")]), "")
