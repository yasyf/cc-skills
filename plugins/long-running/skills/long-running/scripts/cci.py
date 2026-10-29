"""Page through cci records in sequence order for bus.py, standing.py, ledger.py, and desk-runner.py, and read which PRs open `hold` records name."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

PAGE_BYTES = 4_000_000
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
PR_FLAG = re.compile(r"^(?:https://github\.com/(?P<url>[\w.-]+/[\w.-]+)/pull/|(?P<ref>[\w.-]+/[\w.-]+)#)?(?P<number>\d+)(?:[/?#]\S*)?$")
SEQ_SINCE = re.compile(r"^#?(\d+)$")
NODE_PEER = re.compile(r"^peer:[0-9a-f]{16}/(?P<key>.+)$")
LISTED_FLAGS = ("--lane", "--kind", "--topic")
STATUS_KINDS = frozenset({"hold"})
INDEX_FRESH = timedelta(seconds=30)


@dataclass(frozen=True)
class Trunk:
    """The trunk's landing state the runner last posted on the `dev-red` topic: `blocked` while a required landing job is red, since the Pacific time its first red commit landed, and the PR named as its fix, or empty."""

    blocked: bool
    since: str
    fix: str


def lane_report(record: dict) -> bool:
    return record.get("kind") == "report" and (record.get("text") or "").split(" ", 1)[0] in REPORT_VERDICTS


@dataclass(frozen=True)
class Query:
    lanes: frozenset[str]
    kinds: frozenset[str]
    topics: frozenset[str]
    pr: tuple[str, int] | None
    limit: int


class Index:
    """Every record `cci tail` gives each drive read so far, held in memory by PR, kind and lane and read forward from the newest seq held, so a pass answers its filtered reads with one `cci tail` per drive instead of one per filter.

    It answers `--lane`, `--kind`, `--topic`, `--pr` and `--limit` over a seq or RFC3339 `--since` as `cci tail` does, the caller's peer mail passing every selector, and leaves every other read to `cci tail`: one addressed with `--to` or `--reader`, which reaches records outside the drive, and a `hold` read, whose open or closed status a later record changes. A drive reads forward when its last read is `INDEX_FRESH` old or `stale` named it since."""

    def __init__(self, run: Callable[[list[str]], str], now: Callable[[], datetime]):
        self.run = run
        self.now = now
        self.held: dict[str, list[dict]] = {}
        self.keyed: dict[str, dict[tuple[str, object], list[dict]]] = {}
        self.read_at: dict[str, datetime] = {}
        self.lock = threading.Lock()

    def __call__(self, argv: list[str]) -> str:
        return self.run(argv)

    def stale(self, drive: str) -> None:
        with self.lock:
            self.read_at.pop(drive, None)

    def select(self, drive: str, filters: tuple[str, ...], since: int | str) -> list[dict] | None:
        if (query := parse_query(filters)) is None or (after := parse_since(since)) is None:
            return None
        with self.lock:
            self.read_forward(drive)
            candidates = self.candidates(drive, query)
            peer = peer_addresses()
            now = self.now()
            found = [dict(record) for record in candidates if after(record) and live(record, now) and (record["drive"] == drive and query_matches(query, record) or peer_mail(record, drive, peer)) and pr_matches(query, record)]
        return found[-query.limit :] if query.limit else found

    def candidates(self, drive: str, query: Query) -> list[dict]:
        keys = [("pr", query.pr[1])] if query.pr else [("kind", kind) for kind in query.kinds] or [("lane", lane) for lane in query.lanes]
        if not keys:
            return self.held[drive]
        keyed = self.keyed[drive]
        merged = {record["seq"]: record for key in [*keys, ("peer", None)] for record in keyed.get(key, [])}
        return [merged[seq] for seq in sorted(merged)]

    def read_forward(self, drive: str) -> None:
        if (read := self.read_at.get(drive)) and self.now() - read < INDEX_FRESH:
            return
        held = self.held.setdefault(drive, [])
        keyed = self.keyed.setdefault(drive, {})
        since = held[-1]["seq"] if held else 0
        while page := [json.loads(line) for line in self.run(["cci", "tail", "--drive", drive, "--since", str(since), "--json", "--budget", str(PAGE_BYTES)]).splitlines() if line.strip()]:
            held += page
            for record in page:
                peer = record["drive"] != drive or any(to.startswith("peer:") for to in record.get("to") or [])
                numbers = {number for ref in (record.get("refs") or {}).get("prs", []) if (number := ref_number(ref))}
                for key in [("kind", record["kind"]), ("lane", record["lane"]), *(("pr", number) for number in numbers), *([("peer", None)] if peer else [])]:
                    keyed.setdefault(key, []).append(record)
            since = page[-1]["seq"]
        self.read_at[drive] = self.now()


def parse_query(filters: tuple[str, ...]) -> Query | None:
    listed: dict[str, set[str]] = {flag: set() for flag in LISTED_FLAGS}
    pr, limit, rest = None, 0, list(filters)
    while rest:
        flag, _, value = rest.pop(0).partition("=")
        if not value:
            if not rest:
                return None
            value = rest.pop(0)
        if flag in listed:
            listed[flag] |= {item for item in value.split(",") if item}
        elif flag == "--pr" and (found := PR_FLAG.fullmatch(value)):
            pr = (found["url"] or found["ref"] or "", int(found["number"]))
        elif flag in ("--limit", "-n") and value.isdigit():
            limit = int(value)
        else:
            return None
    if listed["--kind"] & STATUS_KINDS:
        return None
    return Query(frozenset(listed["--lane"]), frozenset(listed["--kind"]), frozenset(listed["--topic"]), pr, limit)


def parse_since(since: int | str) -> Callable[[dict], bool] | None:
    if isinstance(since, int) or (found := SEQ_SINCE.fullmatch(since)):
        after = since if isinstance(since, int) else int(found[1])
        return lambda record: record["seq"] > after
    try:
        at = datetime.fromisoformat(since)
    except ValueError:
        return None
    if at.tzinfo is None:
        return None
    return lambda record: "at" in record and datetime.fromisoformat(record["at"]) >= at


def live(record: dict, now: datetime) -> bool:
    return not record.get("expires_at") or datetime.fromisoformat(record["expires_at"]) > now


def peer_addresses() -> frozenset[str]:
    session = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
    if not session:
        return frozenset()
    agent = os.environ.get("CCI_AGENT_ID")
    return frozenset({f"{session}/{agent}" if agent else session})


def peer_mail(record: dict, drive: str, peer: frozenset[str]) -> bool:
    if record["drive"] != drive:
        return True
    return any(to.startswith("peer:") and (to[5:] in peer or ((found := NODE_PEER.fullmatch(to)) and found["key"] in peer)) for to in record.get("to") or [])


def query_matches(query: Query, record: dict) -> bool:
    return (not query.lanes or record["lane"] in query.lanes) and (not query.kinds or record["kind"] in query.kinds) and (not query.topics or (record.get("topic") or "") in query.topics)


def pr_matches(query: Query, record: dict) -> bool:
    if not query.pr:
        return True
    repo, number = query.pr
    return any(ref == number if isinstance(ref, int) else isinstance(ref, str) and (ref == f"{repo}#{number}" if repo else ref.endswith(f"#{number}")) for ref in (record.get("refs") or {}).get("prs", []))


def ref_number(ref: int | str) -> int:
    if isinstance(ref, int):
        return ref
    tail = str(ref).rsplit("#", 1)[-1]
    return int(tail) if "#" in str(ref) and tail.isdigit() else 0


def records(run: Callable[[list[str]], str], drive: str, *filters: str, since: int | str = 0) -> list[dict]:
    """Every record on `drive` after `since`, a sequence or any `cci tail --since` time, that matches the `cci tail` filters, paged by sequence; touches no cursor. An `Index` answers what it holds without a `cci tail`."""
    if isinstance(run, Index) and (held := run.select(drive, filters, since)) is not None:
        return held
    found: list[dict] = []
    while True:
        out = run(["cci", "tail", "--drive", drive, "--since", str(since), "--json", "--budget", str(PAGE_BYTES), *filters])
        page = [json.loads(line) for line in out.splitlines() if line.strip()]
        if not page:
            return found
        found += page
        since = page[-1]["seq"]


def held_prs(run: Callable[[list[str]], str], drive: str) -> dict[str, int]:
    """Each PR an open `hold` record on `drive` holds, mapped to that record's seq; a `lift --resolves <seq>` closes the record and frees the PR.

    `cci tail --kind=hold` also returns the caller's peer mail of any kind and drive, which carries no status, so only the drive's hold records count."""
    holds = [record for record in records(run, drive, "--kind=hold") if record["kind"] == "hold" and record["drive"] == drive]
    return {pr: record["seq"] for record in holds if record["status"] == "open" for pr in held_by(record)}


def held_by(record: dict) -> list[str]:
    """The PR numbers a hold record holds: the one its text leads with as `hold #<n>`, every later `#<n>` being prose as in the holds file, else every PR its refs name."""
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
