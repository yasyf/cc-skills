"""Page through cci records in sequence order for ledger.py, and read which PRs open `hold` records name."""

from __future__ import annotations

import base64
import http.client
import io
import json
import os
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from zoneinfo import ZoneInfo

PAGE_BYTES = 4_000_000
PAGE_ROWS = 500
INTERNED = ("drive", "lane", "kind", "to", "topic", "source", "status")
CLAIM_KINDS = ("opened", "head")
INCIDENT_LANE = re.compile(r"^(?:inc|incident|outage)-")
INCIDENT_TAG = re.compile(r"\b([A-Z]{2,})(?:-([0-9A-Z]*\d[0-9A-Z]*)|(\d[0-9A-Z]*))\b")
ANSWER_TEXT = re.compile(r"^(?:OPENED|READY)\b")
ANSWER_KINDS = frozenset({*CLAIM_KINDS, "ready", "fix-live"})
ROOT_ANSWER_KINDS = frozenset({"go", "decide", "decision"})
ROOT_RULINGS = frozenset({"decide", "decision"})
MECHANISM_TEXT = re.compile(r"^(?:\d{1,2}:\d{2} [AP]M )?(?P<mark>MECHANISM|CANDIDATE)\b")
SETTLING_TEXT = re.compile(r"^(?:\d{1,2}:\d{2} [AP]M )?(?:OPENED|BLOCKER|READY|NOT-OURS|DONE|STOPPED)\b")
TEXT_KINDS = ("report", "incident", "state")
REPORT_VERDICTS = ("clean", "red", "conflicting", "held")
SETTLING_KINDS = frozenset({*CLAIM_KINDS, "ready", "blocker", "blocked", "fix-live", "not-ours", "stopped", "stand-down", "done"})
TRUNK_TOPIC = "dev-red"
NOT_LIVE_TOPIC = "landed-not-live"
TRUNK_RUNNERS = frozenset({"desk-runner", "supervisor"})
TRUNK_STATE = re.compile(r"^(?P<state>DEV-RED|POST-MERGE-RED|DEV-GREEN)\b")
NAMED_PR = re.compile(r"#(\d+)\b")
NOT_OWNERS = frozenset({"root", "main", "all", ""})
ENDED_KINDS = frozenset({"stand-down", "stopped", "done"})
OWNER_WINDOW = "2h"
PACIFIC = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class Trunk:
    """The trunk's landing state the runner last posted on the `dev-red` topic: `blocked` while a required landing job is red, the Pacific time and seq of the runner's first red record since its last DEV-GREEN, and the PR named as its fix, or empty."""

    blocked: bool
    since: str
    seq: int
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
    """A `cci tail` runner whose `records` answers repeat a query, between `remember` and `stop`, from what it read since the last `forget`, so a pass reads each query once. A query from a sequence keeps what it read for the runner's life, since a record's text, kind, lane and closures never change after it is posted: it reads its history once, and after that every query that differs only in `--pr` shares one read of the records after the newest it kept, matched to each PR here as cci matches them. So a pass runs one cci call per query shape, never one per PR, and each reads only the records posted since the last. A record two queries read the same is held once, and a Tail made `sharing` another keeps that one's history under its lock, so a runner's two Landings hold the drive's history once while each keeps its own pass reads."""

    def __init__(self, run: Callable[[list[str]], str], sharing: Tail | None = None):
        self.run = run
        self.read: dict[tuple[str, tuple[str, ...], str, bool], list[dict]] | None = None
        self.kept: dict[tuple[str, tuple[str, ...], str], Kept] = sharing.kept if sharing else {}
        self.marks: dict[tuple[str, tuple[str, ...]], int] = sharing.marks if sharing else {}
        self.by_seq: dict[int, dict] = sharing.by_seq if sharing else {}
        self.keeping = sharing.keeping if sharing else threading.RLock()
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
        with self.keeping:
            kept = self.kept.get((drive, shape, pr))
            if kept is None or since < kept.floor:
                mark = self.mark(drive, shape) if pr else since
                found = self.once(paged(self, drive, filters, since))
                kept = self.kept[(drive, shape, pr)] = Kept(since, max([since, mark, *(record["seq"] for record in found)]), found)
            elif self.read is None or (drive, shape) not in self.topped:
                self.top_up(drive, shape)
            now = datetime.now(timezone.utc)
            return [record for record in kept.records if record["seq"] > since and not expired(record, now)]

    def top_up(self, drive: str, shape: tuple[str, ...]) -> None:
        """Read once the records of `shape` after the oldest seq any of its queries has read through, and add each to every query it matches."""
        queries = {pr: kept for (on, of, pr), kept in self.kept.items() if (on, of) == (drive, shape)}
        found = self.once(paged(self, drive, shape, min(kept.upto for kept in queries.values())))
        for pr, kept in queries.items():
            kept.records += [record for record in found if record["seq"] > kept.upto and (not pr or names_pr(record, pr))]
            kept.upto = max([kept.upto, *(record["seq"] for record in found)])
        self.marks[(drive, shape)] = max([self.marks.get((drive, shape), 0), *(kept.upto for kept in queries.values())])
        self.topped.add((drive, shape))

    def once(self, found: list[dict]) -> list[dict]:
        """`found` with each record equal to one already held replaced by that one, so two queries that read the same record keep one copy of it."""
        by_seq = self.by_seq
        for index, record in enumerate(found):
            if (kept := by_seq.get(record["seq"])) == record:
                found[index] = kept
            else:
                by_seq[record["seq"]] = record
        return found

    def mark(self, drive: str, shape: tuple[str, ...]) -> int:
        """The newest seq `shape` matches, read once with `--limit 1` and kept current by each top-up, so a `--pr` query read from its start never makes the next top-up read again what the shape's other queries already hold; a query naming no PR reads every record of its shape, so its own newest seq is that mark."""
        if (drive, shape) not in self.marks:
            out = self.run(["cci", "tail", "--drive", drive, "--since-seq", "0", "--json", "--limit", "1", *shape])
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


def _native_json(path: str, request: dict, deadline: float | None) -> tuple[int, dict]:
    def remaining():
        if deadline is None:
            return None
        seconds = deadline - monotonic()
        if seconds <= 0:
            raise TimeoutError("native CCI read budget expired")
        return seconds

    connection = http.client.HTTPConnection("127.0.0.1", 7377, timeout=remaining())
    try:
        connection.request("POST", path, json.dumps(request).encode(), {"Content-Type": "application/json"})
        wire = connection.sock
        wire.settimeout(remaining())
        response = connection.getresponse()
        chunks = []
        while not response.isclosed():
            wire.settimeout(remaining())
            chunk = response.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
        packet = b"".join(chunks)
        chunks.clear()
        if response.length:
            raise http.client.IncompleteRead(packet, response.length)
        result = json.loads(packet)
        del packet
        return response.status, result
    finally:
        connection.close()


def native_home(environment: dict[str, str] | None = None, cwd: str | None = None) -> str:
    environment = dict(os.environ) if environment is None else environment
    home = environment.get("CCI_HOME") or os.path.join(environment["HOME"], ".cc-inbox")
    return os.path.abspath(os.path.join(cwd or os.getcwd(), home))


def native_tail(argv: list[str], timeout: float, env: dict[str, str] | None = None, cwd: str | None = None) -> tuple[int, str, str]:
    """Read a cursor-free tail through the daemon's native CLI handler."""
    deadline = monotonic() + timeout
    environment = os.environ | (env or {})
    request = {"argv": argv, "session": environment.get("CLAUDE_CODE_SESSION_ID") or environment.get("CODEX_THREAD_ID", ""), "agent": environment.get("CCI_AGENT_ID", ""), "home": native_home(environment, cwd)}
    try:
        status, result = _native_json("/v1/tail", request, deadline)
        if status != 200:
            return 1, "", f"cci: {result['error']}\n"
        return result["returncode"], _tail_output(result["stdout_b64"]), _tail_output(result["stderr_b64"])
    except TimeoutError:
        raise
    except (OSError, http.client.HTTPException) as error:
        return 1, "", f"cci: native tail: {error}\n"


def _tail_output(encoded: str, encoding: str | None = None) -> str:
    with io.TextIOWrapper(io.BytesIO(base64.b64decode(encoded, validate=True)), encoding=encoding, errors="replace") as output:
        return output.read()


class CapturedBody:
    def __init__(self, envelope: dict):
        self.envelope = envelope

    def is_file(self) -> bool:
        return self.envelope["is_file"]

    def open(self):
        if self.envelope["error"]:
            raise OSError(self.envelope["error"])
        return io.TextIOWrapper(io.BytesIO(base64.b64decode(self.envelope["body_b64"], validate=True)), encoding="utf-8")


def board_request(drive: str, repo: str, go_lanes: list[str], prs: list[str], row_ids: list[str]) -> dict:
    environment = os.environ
    cwd = os.getcwd()
    return {
        "contract": "board-query-v1",
        "home": native_home(environment, cwd),
        "session": environment.get("CLAUDE_CODE_SESSION_ID") or environment.get("CODEX_THREAD_ID", ""),
        "agent": environment.get("CCI_AGENT_ID", ""),
        "drive": drive,
        "repo": repo,
        "cwd": cwd,
        "go_lanes": go_lanes,
        "ruling_argv": record_argv(drive, tuple(f"--kind={kind}" for kind in sorted(ROOT_ANSWER_KINDS | {"answer"})), 0),
        "withdraw_argv": record_argv(drive, ("--lane", "root", "--kind=withdraw"), 0),
        "review_argv": record_argv(drive, ("--kind=review", "--kind=head"), 0),
        "prs": prs,
        "row_ids": row_ids,
    }


def since_args(since: int | str) -> list[str]:
    """Select an internal sequence bound or the CLI's time-bound syntax."""
    value = str(since)
    if re.fullmatch(r"#?[+-]?[0-9]+", value):
        return ["--since-seq", str(int(value.removeprefix("#")))]
    return ["--since", value]


def records(run: Callable[[list[str]], str], drive: str, *filters: str, since: int | str = 0) -> list[dict]:
    """Record history on `drive` after `since`, matching the CLI filters without touching a cursor. A `Tail` follows sequence queries as `Tail.follow` says; use `current_records` for current native annotations."""
    return _read_records(run, drive, filters, since, history=True)


def current_records(run: Callable[[list[str]], str], drive: str, *filters: str, since: int | str = 0) -> list[dict]:
    """Records with current native annotations, read once between a `Tail`'s freshness barriers."""
    return _read_records(run, drive, filters, since, history=False)


def selected_records(run: Callable[[list[str]], str], drive: str, seqs: list[int]) -> list[dict]:
    """Read complete current annotations for selected internal sequences without retaining history."""
    selected = sorted(set(seqs))
    found: dict[int, dict] = {}
    for start in range(0, len(selected), PAGE_ROWS):
        batch = selected[start : start + PAGE_ROWS]
        out = run(["cci", "tail", "--drive", drive, "--since-seq", "0", "--seqs", ",".join(map(str, batch)), "--json", "--budget", "0"])
        requested = set(batch)
        for line in out.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            seq = record["seq"]
            if seq not in requested or seq in found:
                raise ValueError(f"unexpected selected cci sequence: {seq}")
            found[seq] = record
    return [found[seq] for seq in selected if seq in found]


def _read_records(run: Callable[[list[str]], str], drive: str, filters: tuple[str, ...], since: int | str, *, history: bool) -> list[dict]:
    key = (drive, filters, str(since), history)
    read = run.read if isinstance(run, Tail) else None
    if read is not None and (kept := read.get(key)) is not None:
        return list(kept)
    found = run.follow(drive, filters, int(since)) if history and isinstance(run, Tail) and str(since).isdigit() and "--limit" not in filters else paged(run, drive, filters, since)
    if read is not None:
        read[key] = found
    return list(found)


def paged(run: Callable[[list[str]], str], drive: str, filters: tuple[str, ...], since: int | str) -> list[dict]:
    """The records `cci tail` prints for `filters` after `since`, paged by sequence; a page is the last when it holds fewer than the `PAGE_ROWS` records cci prints per call and fills under half the byte budget, since cci caps a record far below that."""
    found: list[dict] = []
    while True:
        out = run(record_argv(drive, filters, since))
        page = [interned(json.loads(line)) for line in out.splitlines() if line.strip()]
        found += page
        if len(page) < PAGE_ROWS and len(out.encode()) < PAGE_BYTES // 2:
            return found
        since = page[-1]["seq"]


def interned(record: dict) -> dict:
    """`record` with its keys and its few-valued fields interned, so a hundred thousand records share one copy of each lane, kind and drive name."""
    for key in INTERNED:
        if (value := record.get(key)).__class__ is str:
            record[key] = sys.intern(value)
    return {sys.intern(key): value for key, value in record.items()}


def record_argv(drive: str, filters: tuple[str, ...], since: int | str) -> list[str]:
    return ["cci", "tail", "--drive", drive, *since_args(since), "--json", "--budget", str(PAGE_BYTES), *filters]


def resolved(record: dict) -> set[int]:
    """Every seq `record` resolves: its `resolves` and each of its `also_resolves`, which a `--resolves a,b` post stores past the first."""
    first = record.get("resolves") or []
    return (set(first) if isinstance(first, list) else {first}) | set(record.get("also_resolves") or [])


def dead_owner(run: Callable[[list[str]], str], drive: str, lane: str) -> str:
    """Why `lane` cannot own work on `drive`, or "" when it posted there within OWNER_WINDOW and its newest record ends nothing, or a `spawn` record naming it in `--lane-ref` is newer than that record."""
    if lane in NOT_OWNERS:
        return f"{lane or 'no lane'} is the coordinator, not a worker; name the worker that builds it, or ask root to spawn one"
    out = run(["cci", "grep", ".", "--drive", drive, "--lane", lane, "--since", OWNER_WINDOW, "-n", "1", "--json"])
    newest = next((json.loads(line) for line in out.splitlines() if line.startswith("{")), None)
    if newest is not None and newest["kind"] not in ENDED_KINDS:
        return ""
    spawned = run(["cci", "grep", lane, "--drive", drive, "--kind", "spawn", "--since", OWNER_WINDOW, "-n", "1", "--json"])
    spawn = next((json.loads(line) for line in spawned.splitlines() if line.startswith("{")), None)
    if spawn is not None and lane in spawn.get("refs", {}).get("lanes", []) and (newest is None or spawn["seq"] > newest["seq"]):
        return ""
    return f"{lane} has posted nothing on {drive} in {OWNER_WINDOW}{' since its ' + newest['kind'] if newest else ''}; name a live worker, or ask root to spawn one"


def expired(record: dict, now: datetime) -> bool:
    return bool(record.get("expires_at")) and datetime.fromisoformat(record["expires_at"]) <= now


def open_holds(run: Callable[[list[str]], str], drive: str) -> list[dict]:
    """Every open `hold` record on `drive`, the only place a hold lives; a record that resolves one closes it, and a `hold` that resolves an earlier hold is that lift, never a new hold.

    `cci tail --kind=hold` also returns the caller's peer mail of any kind and drive, which carries no status, so only the drive's hold records count."""
    holds = [record for record in current_records(run, drive, "--kind=hold") if (record["kind"], record["drive"]) == ("hold", drive)]
    seqs = {record["seq"] for record in holds}
    return [record for record in holds if record.get("status") == "open" and not resolved(record) & seqs]


def held_prs(run: Callable[[list[str]], str], drive: str) -> dict[str, int]:
    """Each PR an open `hold` record on `drive` holds, mapped to that record's seq."""
    return {pr: record["seq"] for record in open_holds(run, drive) for pr in held_by(record)}


def held_by(record: dict) -> list[str]:
    """The PR numbers a hold record holds: the ones it was posted with as `--pr`, its text being prose."""
    return [str(pr).rsplit("#", 1)[-1] for pr in record["refs"].get("prs", [])]


def mechanism_mark(record: dict) -> str:
    """MECHANISM or CANDIDATE when a lane's record names its incident's mechanism: a `mechanism` record, or a `report`, `incident`, or `state` whose text starts with either word, after the clock an older record may open with."""
    if record["kind"] == "mechanism":
        return "MECHANISM"
    found = MECHANISM_TEXT.match(record.get("text") or "") if record["kind"] in TEXT_KINDS else None
    return found["mark"] if found else ""


def settles_mechanism(record: dict) -> bool:
    """Whether a lane's record answers its mechanism: a PR, a blocker, or the lane's end or DONE, by kind or by a text record starting OPENED, BLOCKER, READY, NOT-OURS, DONE, or STOPPED, after the clock an older record may open with (root #305423)."""
    return record["kind"] in SETTLING_KINDS or (record["kind"] in TEXT_KINDS and bool(SETTLING_TEXT.match(record.get("text") or "")))


def incident_keys(record: dict) -> set[str]:
    """What names the incident a record is about: its topic, and each incident tag in its topic or text, such as `SAND-5A`, `[SAND-5A]`, or `SAND5A`, all read as `tag:SAND5A`."""
    text = f"{record.get('topic') or ''} {record.get('text') or ''}"
    tags = {f"tag:{found[1]}{found[2] or found[3]}" for found in INCIDENT_TAG.finditer(text)}
    return tags | ({f"topic:{record['topic']}"} if record.get("topic") else set())


def trunk(records: list[dict]) -> Trunk:
    """The trunk's state from the records on the `dev-red` topic: the landing runner's newest DEV-RED, POST-MERGE-RED, or DEV-GREEN record sets it, and the fix is the PR that the newest record by any other lane names after the runner's last DEV-GREEN, an incident record excepted."""
    records = [record for record in records if record.get("topic") == TRUNK_TOPIC]
    states = [(record, found) for record in records if record["lane"] in TRUNK_RUNNERS and (found := TRUNK_STATE.match(record.get("text") or ""))]
    if not states or states[-1][1]["state"] == "DEV-GREEN":
        return Trunk(False, "", 0, "")
    green = max((record["seq"] for record, found in states if found["state"] == "DEV-GREEN"), default=0)
    fixes = [pr for record in records if record["seq"] > green and record["lane"] not in TRUNK_RUNNERS and record["kind"] != "incident" and (pr := named_pr(record))]
    first = next(record for record, _ in states if record["seq"] > green)
    since = datetime.fromisoformat(first["at"]).astimezone(PACIFIC).strftime("%-I:%M %p")
    return Trunk(states[-1][1]["state"] == "DEV-RED", since, first["seq"], fixes[-1] if fixes else "")


def named_pr(record: dict) -> str:
    refs = [str(ref).rsplit("#", 1)[-1] for ref in record["refs"].get("prs", [])]
    return next(iter([*refs, *NAMED_PR.findall(record.get("text") or "")]), "")
