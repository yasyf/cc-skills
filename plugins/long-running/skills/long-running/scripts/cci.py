"""Page through cci records in sequence order for bus.py, standing.py, ledger.py, and desk-runner.py, and read which PRs open `hold` records name."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

PAGE_BYTES = 4_000_000
INSTALL_WINDOW_SECONDS = 120
INSTALL_RETRY_SECONDS = 2
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


@dataclass
class Kept:
    """The records one query read for the runner's life, from `floor`, and the seq it has read through."""

    floor: int
    upto: int
    records: list[dict] = field(default_factory=list)


class Tail:
    """A `cci tail` runner whose `records` answers repeat a query, between `remember` and `stop`, from what it read since the last `forget`, so a pass reads each query once. A query from a sequence keeps what it read for the runner's life, since a record's text, kind, lane and closures never change after it is posted: it reads its history once, and after that every query that differs only in `--pr` shares one read of the records after the newest it kept, matched to each PR here as cci matches them. So a pass runs one cci call per query shape, never one per PR, and each reads only the records posted since the last."""

    def __init__(self, run: Callable[[list[str]], str]):
        self.run = run
        self.read: dict[tuple[str, tuple[str, ...], str], list[dict]] | None = None
        self.kept: dict[tuple[str, tuple[str, ...], str], Kept] = {}
        self.marks: dict[tuple[str, tuple[str, ...]], int] = {}
        self.topped: set[tuple[str, tuple[str, ...]]] = set()

    def __call__(self, argv: list[str]) -> str:
        return self.run(argv)

    def remember(self) -> None:
        self.read = {}
        self.topped = set()

    def forget(self) -> None:
        if self.read is not None:
            self.read = {}
            self.topped = set()

    def stop(self) -> None:
        self.read = None

    def follow(self, drive: str, filters: tuple[str, ...], since: int) -> list[dict]:
        shape, pr = without_pr(filters)
        kept = self.kept.get((drive, shape, pr))
        if kept is None or since < kept.floor:
            mark = self.mark(drive, shape)
            found = paged(self, drive, filters, since)
            kept = self.kept[(drive, shape, pr)] = Kept(since, max([since, mark, *(record["seq"] for record in found)]), found)
        elif self.read is None or (drive, shape) not in self.topped:
            self.top_up(drive, shape)
        now = datetime.now(timezone.utc)
        return [record for record in kept.records if record["seq"] > since and not expired(record, now)]

    def top_up(self, drive: str, shape: tuple[str, ...]) -> None:
        """Read once the records of `shape` after the oldest seq any of its queries has read through, and add each to every query it matches."""
        queries = {pr: kept for (on, of, pr), kept in self.kept.items() if (on, of) == (drive, shape)}
        found = paged(self, drive, shape, min(kept.upto for kept in queries.values()))
        for pr, kept in queries.items():
            kept.records += [record for record in found if record["seq"] > kept.upto and (not pr or names_pr(record, pr))]
            kept.upto = max([kept.upto, *(record["seq"] for record in found)])
        self.marks[(drive, shape)] = max([self.marks.get((drive, shape), 0), *(kept.upto for kept in queries.values())])
        self.topped.add((drive, shape))

    def mark(self, drive: str, shape: tuple[str, ...]) -> int:
        """The newest seq `shape` matches, read once with `--limit 1` and kept current by each top-up, so a query read from its start never makes the next top-up read again what the shape's other queries already hold."""
        if (drive, shape) not in self.marks:
            out = self.run(["cci", "tail", "--drive", drive, "--since", "0", "--json", "--limit", "1", *shape])
            self.marks[(drive, shape)] = max((json.loads(line)["seq"] for line in out.splitlines() if line.strip()), default=0)
        return self.marks[(drive, shape)]


def without_pr(filters: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    if "--pr" not in filters:
        return filters, ""
    at = filters.index("--pr")
    return (*filters[:at], *filters[at + 2 :]), filters[at + 1]


def names_pr(record: dict, pr: str) -> bool:
    """Whether `record`'s PR refs name `pr`, a number or `owner/repo#N`, as `cci tail --pr` matches: an integer ref equal to its number, or a text ref equal to `owner/repo#N`, or ending `#N` when `pr` names no repository."""
    repo, _, number = pr.rpartition("#")
    return any(ref == int(number) if isinstance(ref, int) else ref == pr if repo else ref.endswith(f"#{number}") for ref in record.get("refs", {}).get("prs", []))


@dataclass(frozen=True)
class Posted:
    seq: int
    num: str


@dataclass(frozen=True)
class PostResult:
    code: int
    out: str
    err: str
    records: tuple[Posted, ...]


def posted(output: str) -> tuple[Posted, ...]:
    """Retain the internal sequence and human number of every posted record."""
    records = (json.loads(line) for line in output.splitlines())
    return tuple(Posted(record["seq"], record["num"]) for record in records)


def receipt(seq: int) -> str:
    """Encode the existing Book receipt without changing its persisted schema."""
    return f"#{seq}"


def receipt_seq(value: str) -> int | None:
    """Read an existing internal sequence receipt, including the CLI duplicate suffix."""
    if not value:
        return None
    found = re.fullmatch(r"#([0-9]+)(?: \(duplicate\))?", value)
    if found is None:
        raise ValueError(f"invalid internal sequence receipt: {value!r}")
    return int(found[1])


def records(run: Callable[[list[str]], str], drive: str, *filters: str, since: int | str = 0) -> list[dict]:
    """Every record on `drive` after `since`, a sequence or any `cci tail --since` time, that matches the `cci tail` filters; touches no cursor. A `Tail` answers a query it read since its last `forget` without running cci, and follows a query from a sequence as `Tail.follow` says."""
    key = (drive, filters, str(since))
    read = run.read if isinstance(run, Tail) else None
    if read is not None and (kept := read.get(key)) is not None:
        return list(kept)
    found = run.follow(drive, filters, int(since)) if isinstance(run, Tail) and str(since).isdigit() and "--limit" not in filters else paged(run, drive, filters, since)
    if read is not None:
        read[key] = found
    return list(found)


def paged(run: Callable[[list[str]], str], drive: str, filters: tuple[str, ...], since: int | str) -> list[dict]:
    """The records `cci tail` prints for `filters` after `since`, paged by sequence; a page is the last when it holds fewer than the `PAGE_ROWS` records cci prints per call and fills under half the byte budget, since cci caps a record far below that."""
    found: list[dict] = []
    while True:
        out = run(["cci", "tail", "--drive", drive, "--since", str(since), "--json", "--budget", str(PAGE_BYTES), *filters])
        page = [json.loads(line) for line in out.splitlines() if line.strip()]
        found += page
        if len(page) < PAGE_ROWS and len(out.encode()) < PAGE_BYTES // 2:
            return found
        since = page[-1]["seq"]


def resolved(record: dict) -> set[int]:
    """Every seq `record` resolves: its `resolves` and each of its `also_resolves`, which a `--resolves a,b` post stores past the first."""
    first = record.get("resolves") or []
    return (set(first) if isinstance(first, list) else {first}) | set(record.get("also_resolves") or [])


def expired(record: dict, now: datetime) -> bool:
    return bool(record.get("expires_at")) and datetime.fromisoformat(record["expires_at"]) <= now


def open_holds(run: Callable[[list[str]], str], drive: str) -> list[dict]:
    """Every open `hold` record on `drive`, the only place a hold lives; a record that resolves one closes it.

    `cci tail --kind=hold` also returns the caller's peer mail of any kind and drive, which carries no status, so only the drive's hold records count."""
    return [record for record in records(run, drive, "--kind=hold") if (record["kind"], record["drive"], record.get("status")) == ("hold", drive, "open")]


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


def installed[T](argv: list[str], start: Callable[[], T]) -> T:
    """`start()`, the launch of `argv`, tried again every `INSTALL_RETRY_SECONDS` for up to `INSTALL_WINDOW_SECONDS` while `argv[0]` is missing or not executable, as a binary is for seconds while a Homebrew upgrade relinks it, so no pass dies in an install window (root #233635)."""
    deadline = time.monotonic() + INSTALL_WINDOW_SECONDS
    while True:
        try:
            return start()
        except (FileNotFoundError, PermissionError) as error:
            if error.filename != argv[0] or time.monotonic() >= deadline:
                raise
            time.sleep(INSTALL_RETRY_SECONDS)
