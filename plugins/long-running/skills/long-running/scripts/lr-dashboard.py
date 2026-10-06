#!/usr/bin/env python3
"""
    lr-dashboard.py serve    [--drive ID] [--host HOST] [--port N] [--interval S]
    lr-dashboard.py start    [--drive ID] [--session ID] [--host HOST]
    lr-dashboard.py url      [--drive ID] [--session ID]
    lr-dashboard.py snapshot [--drive ID] [--session ID]
    lr-dashboard.py open     [--drive ID] [--session ID]
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit
from urllib.parse import unquote as unquote_url
from zoneinfo import ZoneInfo

import drive
import ledger
from lrdash import chat, overview, platy, views, yamlish

PACIFIC = ZoneInfo("America/Los_Angeles")
PAGE = Path(__file__).parents[1] / "templates" / "lr-dashboard.html"
DEFAULT_VIEWS = Path(__file__).parents[1] / "reference" / "dashboard-views.yaml"
CHAT_WIDGET = Path(__file__).resolve().parents[1] / "templates" / "lr-dashboard-chat.html"
SERVER_FILE = Path("dashboard") / "server.json"
SERVER_LOG = Path("dashboard") / "server.log"
START_LOCK = Path("dashboard") / "start.lock"
SEEN_FILE = Path("dashboard") / "seen.json"
BUILDS_FILE = Path("dashboard") / "builds.json"
ACTIONS_FILE = Path("dashboard") / "owner-actions.json"
ACTION_HEADER = "X-Dashboard-Action"
CCI_BIN = Path.home() / ".local" / "bin" / "cci"
CCI_TEXT = 400
OWNER_ACTIONS = {"question": "Question", "complete": "Mark complete", "reply": "Reply"}
TOKEN_HEADER = "X-Dashboard-Token"
MANUAL_FILE = "dashboard.yaml"
PORT_BASE = 8700
PORT_SPAN = 300
INTERVAL_SECONDS = 15.0
BUILDS_INTERVAL = timedelta(minutes=1)
RATE_LIMIT_BACKOFF = timedelta(minutes=5)
RATE_LIMITED = "429 Too Many Requests"
NEVER = datetime.min.replace(tzinfo=timezone.utc)
COMMAND_TIMEOUT_SECONDS = 30
START_WAIT_SECONDS = 10.0
HEALTH_TIMEOUT_SECONDS = 2.0
LANE_WINDOW = timedelta(hours=48)
SLACK = timedelta(hours=2)
FOLLOW = timedelta(hours=16)
HEADER_CHARS = 80
CCI_URL = "http://127.0.0.1:7377/v1"
LOOPBACK = {"127.0.0.1", "localhost"}
CCI_OPEN = ("open_defects", "open_blockers", "open_holds")
CCI_PAGE = 500
INCIDENT_WINDOW = timedelta(hours=72)
CENSUS_WINDOW = timedelta(hours=48)
LANDED_WINDOW = timedelta(hours=26)
NEW_YEAR = timedelta(days=180)
CONTEXT_LINES = 25
DOC_LIMIT = 20
VIEW_KEYS = ("id", "title", "section", "type", "note", "columns", "width")
MANUAL_SECTIONS = ("owner", "pinned")
NOTE_SOURCES = ("plans", "progress", "handoffs", "docs", "logs", "investigations", "answers")
OWNER_KINDS = frozenset({"owner-item", "owner-ask"})
OWNER_DONE_ASKS = frozenset({"LIVE", "dropped", "answered"})
OWNER_VERBS = frozenset({"DECIDE", "ASK", "RULING"})
OWNER_WORD = re.compile(r"(?:->|→)\s*owner\b|\b(?:for|to|asks?|needs|awaits?|waiting on) the owner\b|\bowner (?:pick|decision|call|ruling|answer) (?:needed|pending|required)\b", re.IGNORECASE)
DECIDER = re.compile(r"\bDECIDE (?:msg_\w+ )?(?P<lane>[\w.:-]+?)(?::| \()")
OWNER_BULLET = re.compile(r"^- \*\*(?P<title>[^*]+)\*\*:?[ \t]*(?P<detail>.*)$", re.MULTILINE)
PRESENT_PORT = re.compile(r"port (?P<port>\d+)")
COMPACT_BOUNDARY = b'"subtype":"compact_boundary"'
CCN_ID = re.compile(r"^[0-9a-f]{7,40}$")
CLOSING_KINDS = ["done", "lift", "go", "decision", "owner", "answer"]
PASSIVE_BLOCKS = frozenset({"markdown", "code", "diagram", "table", "section"})

VERB = r"[A-Z][A-Z-]*[A-Z]"
LANE = r"[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*"
LINE_ID = r"(?:[RGL]\d+|L-[\w-]+)"
CLOCK = re.compile(
    r"(?<![\d:])(?P<h>[0-2]?\d):(?P<m>[0-5][\dx]|[\dx])(?::\d{2})?(?:\.\d+)?\s*(?P<ampm>[AaPp][Mm])?\s*(?P<tz>PT|PDT|PST|Z|UTC)?(?![\w:])"
)
ISO = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<h>\d{2}):(?P<m>\d{2}|\dx)(?::\d{2})?(?:\.\d+)?\s?(?P<tz>Z|UTC|PT|[+-]\d{2}:?\d{2})?")
MONTH_DAY = re.compile(r"\b(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (?P<day>\d{1,2})\b")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
HEADS = (
    re.compile(rf"^(?P<verb>{VERB})\s+(?:(?P<lane>{LANE})\s+)?\((?P<when>[^)]*)\)\s*(?:(?P<lane2>{LANE})\s*:)?"),
    re.compile(rf"^(?P<lane>{LANE})\s*\((?P<when>[^)]*)\)\s*(?:(?:->|→)\s*(?P<to>{LANE})\s*:?)?\s*(?P<verb>{VERB})?\b"),
    re.compile(rf"^(?P<id>{LINE_ID})\s*\((?P<when>[^)]*)\)"),
    re.compile(rf"^(?P<when>\d{{1,2}}:\d{{2}}(?:\s*[AaPp][Mm])?(?:\s*(?:PT|PDT|PST|Z|UTC))?)\s+(?P<verb>{VERB})\b"),
    re.compile(rf"^(?P<verb>{VERB})\s+(?P<lane>{LANE})\b"),
    re.compile(rf"^(?P<lane>{LANE})\s*(?:->|→)\s*(?P<to>{LANE})\s*:?\s*(?P<verb>{VERB})?\b"),
    re.compile(rf"^(?P<lane>{LANE}):\s+(?P<verb>{VERB})?\b"),
)
LEADER = re.compile(r"^(?:[-*]\s+|#+\s+)+")
PREFIX_LANE = re.compile(rf"^(?P<lane>{LANE})\s*,")
DESK_LANE = re.compile(rf"^\s*(?P<lane>{LANE}):\s+(?P<verb>[a-z]+)\b")
LOOSE_VERB = re.compile(rf"\b(?P<verb>{VERB})\b")
NOT_VERBS = frozenset({"PT", "PDT", "PST", "PM", "AM", "UTC", "PR", "PRS", "CI", "API", "AWS", "SHA", "URL", "IAM", "DNS", "ID", "OK", "UI", "DB", "SQL", "JSON", "HTTP", "GH", "MCP", "LGTM"})
PR_REF = re.compile(r"(?:https://github\.com/(?P<url_repo>[\w.-]+/[\w.-]+)/pull/(?P<url_pr>\d+))|(?:(?<![\w/])(?P<repo>[\w.-]+/[\w.-]+))?#(?P<pr>\d{2,6})\b")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def epoch(value: float | int | None) -> datetime | None:
    if not value:
        return None
    seconds = value / 1000 if value > 10**11 else value
    return datetime.fromtimestamp(seconds, timezone.utc)


@dataclass
class Clock:
    hour: int
    minute: int
    zone: ZoneInfo | timezone
    day: date | None = None
    year_known: bool = False


def minute_of(text: str) -> int:
    return int(text.replace("x", "0"))


def zone_of(tz: str | None) -> ZoneInfo | timezone:
    if tz in ("Z", "UTC"):
        return timezone.utc
    if tz and tz[0] in "+-":
        digits = tz[1:].replace(":", "")
        offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        return timezone(-offset if tz[0] == "-" else offset)
    return PACIFIC


def clock_in(text: str, year: int) -> Clock | None:
    if match := ISO.search(text):
        return Clock(int(match["h"]), minute_of(match["m"]), zone_of(match["tz"]), date.fromisoformat(match["date"]), True)
    if not (match := CLOCK.search(text)):
        return None
    hour = int(match["h"])
    if hour > 23:
        return None
    if ampm := (match["ampm"] or "").upper():
        hour = hour % 12 + (12 if ampm == "PM" else 0)
    zone = zone_of(match["tz"])
    day = None
    if month := MONTH_DAY.search(text):
        day = date(year, MONTHS.index(month["mon"]) + 1, int(month["day"]))
    return Clock(hour, minute_of(match["m"]), zone, day)


def placed(clock: Clock, cursor: datetime) -> datetime:
    if clock.day:
        moment = datetime(clock.day.year, clock.day.month, clock.day.day, clock.hour, clock.minute, tzinfo=clock.zone)
        return moment if clock.year_known or moment - cursor <= NEW_YEAR else moment.replace(year=moment.year - 1)
    local = cursor.astimezone(clock.zone)
    moment = local.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    while moment > cursor + SLACK:
        moment -= timedelta(days=1)
    return moment


@dataclass
class Line:
    file: str
    number: int
    text: str
    lane: str | None = None
    to: str | None = None
    verb: str | None = None
    ref: str | None = None
    when: str | None = None
    at: datetime | None = None
    approx: bool = False
    prs: list[str] = field(default_factory=list)

    def row(self) -> dict:
        return {
            "file": self.file,
            "line": self.number,
            "text": self.text,
            "lane": self.lane,
            "to": self.to,
            "verb": self.verb,
            "ref": self.ref,
            "at": iso(self.at),
            "approx": self.approx,
            "prs": self.prs,
        }


def verb_of(value: str | None) -> str | None:
    return value if value and value not in NOT_VERBS else None


def parse_line(file: str, number: int, raw: str) -> Line:
    text = raw.rstrip()
    body = LEADER.sub("", text)
    line = Line(file, number, text)
    for head in HEADS:
        if not (match := head.match(body)):
            continue
        groups = match.groupdict()
        line.ref = groups.get("id")
        line.when = groups.get("when")
        line.lane = groups.get("lane") or groups.get("lane2")
        line.to = groups.get("to")
        line.verb = verb_of(groups.get("verb"))
        rest = body[match.end() :]
        if line.when and (inner := PREFIX_LANE.match(line.when)):
            line.lane = line.lane or inner["lane"]
        if line.ref and not line.lane and (desk := DESK_LANE.match(rest)):
            line.lane, line.verb = desk["lane"], line.verb or desk["verb"].upper()
        if not line.verb:
            line.verb = next((verb for found in LOOSE_VERB.finditer(" ".join(rest.split()[:4])) if (verb := verb_of(found["verb"]))), None)
        break
    else:
        line.verb = next((verb for found in LOOSE_VERB.finditer(" ".join(body.split()[:3])) if (verb := verb_of(found["verb"]))), None)
    line.prs = sorted(
        {f"{m['url_repo']}#{m['url_pr']}" if m["url_pr"] else (f"{m['repo']}#{m['pr']}" if m["repo"] else m["pr"]) for m in PR_REF.finditer(text)}
    )
    return line


def parse_inbox(file: str, text: str, anchors: list[tuple[int, datetime]]) -> list[Line]:
    lines = [parse_line(file, number, raw) for number, raw in enumerate(text.splitlines(), 1) if raw.strip()]
    pending = sorted(anchors)
    cursor = pending.pop()[1]
    for line in reversed(lines):
        while pending and line.number <= pending[-1][0]:
            cursor = min(cursor, pending.pop()[1])
        header = line.when if line.when is not None else LEADER.sub("", line.text)[:HEADER_CHARS]
        if clock := clock_in(header, cursor.year):
            moment = placed(clock, cursor)
            if cursor - FOLLOW <= moment <= cursor:
                line.at = cursor = moment
            elif clock.day or moment > cursor:
                line.at = moment
            else:
                line.at, line.approx = cursor, True
        else:
            line.at, line.approx = cursor, True
    return lines


def manual_items(items) -> list[dict]:
    return [item if isinstance(item, dict) else {"text": str(item)} for item in items or []]


def compactions_in(path: Path, offset: int) -> tuple[list[dict], int]:
    found = []
    with path.open("rb") as transcript:
        transcript.seek(offset)
        for raw in transcript:
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            if COMPACT_BOUNDARY in raw:
                event = json.loads(raw)
                meta = event.get("compactMetadata") or {}
                found.append({"at": event.get("timestamp"), "session": event.get("sessionId"), "trigger": meta.get("trigger"), "pre_tokens": meta.get("preTokens"), "post_tokens": meta.get("postTokens")})
    return found, offset


def read_tasks(directory: Path) -> list[dict]:
    tasks = []
    for path in directory.glob("*.json"):
        task = json.loads(path.read_text())
        tasks.append(task | {"updated_at": iso(epoch(path.stat().st_mtime))})
    archive = directory / ".archive.ndjson"
    if archive.exists():
        archived = [json.loads(line) for line in archive.read_text().splitlines() if line.strip()]
        tasks += [task | {"archived": True, "updated_at": task.get("archivedAt")} for task in archived]
    return tasks


def is_owner_task(task: dict) -> bool:
    metadata = task.get("metadata") or {}
    return (task.get("subject") or "").startswith("Owner item") or metadata.get("kind") in OWNER_KINDS or bool(OWNER_KINDS & set(metadata.get("labels") or []))


def open_decisions(lines: list[dict], floor: str) -> list[dict]:
    later: list[dict] = []
    waiting = []
    for line in lines:
        if (line["at"] or "") < floor:
            break
        if line["verb"] in OWNER_VERBS and OWNER_WORD.search(line["text"]):
            asker = line["lane"] or ((found := DECIDER.search(line["text"])) and found["lane"])
            named = asker and platy.mentions(asker)
            if not asker or not any(after["lane"] == asker or named.search(after["text"]) for after in later):
                waiting.append(line)
        later.append(line)
    return waiting


def owner_bullets(path: Path) -> list[dict]:
    text = path.read_text()
    return [{"title": match["title"], "detail": match["detail"], "line": text.count("\n", 0, match.start()) + 1} for match in OWNER_BULLET.finditer(text)]


def inbox_files(directory: Path) -> list[Path]:
    return sorted([*directory.glob("*.md"), *directory.glob("*.md.archive/*.md")])


def project_slug(checkout: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", checkout)


def transcript_of(entry: dict, session: str) -> Path:
    projects = Path.home() / ".claude" / "projects"
    expected = projects / project_slug(entry["checkout"]) / f"{session}.jsonl"
    return expected if expected.exists() else next(projects.glob(f"*/{session}.jsonl"), expected)


def task_list_dir(session: str, transcript: Path) -> Path | None:
    root = Path(os.environ.get("CAPTAIN_HOOK_TASKS_DIR") or Path.home() / ".claude" / "tasks")
    if explicit := os.environ.get("CLAUDE_CODE_TASK_LIST_ID"):
        return root / explicit if (root / explicit).is_dir() else None
    metas = sorted((transcript.with_suffix("") / "subagents").glob("agent-*.meta.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    team = next((name for meta in metas if (name := json.loads(meta.read_text()).get("teamName"))), None)
    names = [re.sub(r"[^\w.-]", "-", team)] if team else []
    names += [session, f"session-{session[:8]}"]
    return next((root / name for name in names if (root / name).is_dir()), None)


def build_rows(builds: list[dict]) -> list[dict]:
    return [build | {"cite": f"build:{build['number']}", "text": f"#{build['number']} {build['state']} {build['message']}"} for build in builds]


def run(argv: list[str], cwd: str | None = None) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True, cwd=cwd, timeout=COMMAND_TIMEOUT_SECONDS).stdout


def resolve(drive_id: str | None, session: str | None) -> dict:
    entry = drive.find(drive_id, session) if drive_id or session else drive.find(drive.current_drive(), None)
    if not entry:
        raise SystemExit(f"no drive matches drive={drive_id or '-'} session={session or '-'}; register one with drive.py start")
    return entry


def program_of(entry: dict) -> str:
    return Path(entry["state_dir"]).name


def failure_text(failure: BaseException) -> str:
    detail = getattr(failure, "stderr", "") or str(failure)
    detail = detail.decode(errors="replace") if isinstance(detail, bytes) else detail
    last = (detail.strip().splitlines() or [type(failure).__name__])[-1]
    if isinstance(failure, subprocess.TimeoutExpired):
        last = f"timed out after {failure.timeout:g}s: {last}"
    elif isinstance(failure, subprocess.CalledProcessError):
        last = f"exit {failure.returncode}: {last}"
    return last[:300]


def cci_blockers(digest: dict) -> list[dict]:
    records = [record for key in CCI_OPEN for record in digest[key] or [] if record["refs"].get("stacks") or record["refs"].get("targets")]
    return sorted(records, key=lambda record: (views.stamp(record["at"]), record["seq"]), reverse=True)


def with_actions(rows: list[dict], actions: list[dict], replies: list[dict]) -> list[dict]:
    answering = {reply.get("re") or reply.get("resolves"): reply for reply in replies}
    out = []
    for row in rows:
        mine = [action | {"reply": answering.get(action["seq"])} for action in actions if action["cite"] == row["cite"]]
        out.append(row | {"actions": mine})
    return out


def still_open(rows: list[dict], closed: set[str]) -> list[dict]:
    return [row for row in rows if row["cite"] not in closed and not any(action["action"] == "complete" for action in row["actions"])]


def open_items(sources: dict[str, list[dict]]) -> list[str]:
    rows = [(row["cite"], row["kind"], row["at"], row["title"]) for row in sources["owner"]]
    rows += [(group["cite"], f"incident {group['status']}", group["at"], f"{group['title']} (cci {', '.join(str(record['seq']) for record in group['records'])})") for group in sources["incident_groups"] if group["active"]]
    rows += [(f"cci:{hold['seq']}", f"hold by {hold['lane']}", hold["at"], hold["text"]) for hold in sources["holds"]]
    rows += [(row["cite"], f"pr of {row['lane']}", row["at"], row["title"]) for row in sources["prs"]]
    return ["\t".join(str(field or "-").replace("\n", " ") for field in row) for row in rows]


def ccn_row(kind: str, item: dict) -> dict:
    return item | {"at": item.get("updated_at"), "url": f"/ccn/{item['id']}", "cite": f"ccn:{item['id'][:8]}", "kind": kind, "text": item.get("title", "")}


@dataclass
class Collector:
    entry: dict
    inbox_cache: dict[str, tuple[float, int, list[Line]]] = field(default_factory=dict)
    seen: dict[str, list[tuple[int, datetime]]] | None = None
    compaction_cache: dict[str, tuple[int, list[dict]]] = field(default_factory=dict)
    board_cache: dict[str, tuple[str, dict]] = field(default_factory=dict)
    builds: platy.Builds | None = None
    builds_due: datetime = NEVER
    ancestry: dict[tuple[str, str], bool] = field(default_factory=dict)
    subject_cache: dict[str, str] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return Path(self.entry["state_dir"])

    @property
    def inbox_dir(self) -> Path:
        return self.state_dir / "inbox"

    def config(self) -> dict:
        path = self.state_dir / MANUAL_FILE
        return yamlish.loads(path.read_text()) if path.exists() else {}

    def ccn(self, *args: str) -> list[dict]:
        return json.loads(run(["ccn", "-R", self.entry["checkout"], *args]) or "[]")

    def inbox(self) -> list[Line]:
        lines: list[Line] = []
        for path in inbox_files(self.inbox_dir):
            name = str(path.relative_to(self.inbox_dir))
            stat = path.stat()
            cached = self.inbox_cache.get(name)
            if not cached or cached[:2] != (stat.st_mtime, stat.st_size):
                text = path.read_text(errors="replace")
                parsed = parse_inbox(name, text, self.anchors(name, len(text.splitlines()), epoch(stat.st_mtime)))
                self.inbox_cache[name] = cached = (stat.st_mtime, stat.st_size, parsed)
            lines += cached[2]
        return sorted(lines, key=lambda line: (line.at or datetime.min.replace(tzinfo=timezone.utc), line.number), reverse=True)

    def anchors(self, name: str, count: int, modified: datetime) -> list[tuple[int, datetime]]:
        path = self.state_dir / SEEN_FILE
        if self.seen is None:
            stored = json.loads(path.read_text()) if path.exists() else {}
            self.seen = {file: [(lines, datetime.fromisoformat(at)) for lines, at in marks] for file, marks in stored.items()}
        marks = self.seen.setdefault(name, [])
        if marks and marks[-1][0] > count:
            marks.clear()
        if not marks or marks[-1][0] < count:
            marks.append((count, modified))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({file: [(lines, iso(at)) for lines, at in seen] for file, seen in self.seen.items()}))
        return [*marks, (count, modified)]

    def lanes(self, lines: list[dict], tasks: list[dict], moment: datetime) -> list[dict]:
        latest: dict[str, dict] = {}
        for line in lines:
            if line["lane"] and line["lane"] not in latest:
                latest[line["lane"]] = line
        owners: dict[str, list[dict]] = {}
        for task in tasks:
            if task.get("owner") and task.get("status") in ("in_progress", "pending"):
                owners.setdefault(task["owner"], []).append(task)
        names = {lane for lane, line in latest.items() if line["at"] and moment - datetime.fromisoformat(line["at"].replace("Z", "+00:00")) <= LANE_WINDOW} | set(owners)
        rows = []
        for name in names:
            line = latest.get(name) or {}
            at = line.get("at")
            idle = int((moment - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() // 60) if at else None
            subjects = "; ".join(f"#{task['id']} {task.get('subject', '')}" for task in owners.get(name, []))
            rows.append({"lane": name, "at": at, "idle_minutes": idle, "verb": line.get("verb"), "text": line.get("text", ""), "tasks": subjects, "url": line.get("url"), "cite": line.get("cite"), "approx": line.get("approx")})
        return rows

    def incidents(self) -> list[dict]:
        directory = self.state_dir / "incidents"
        folders = []
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_dir():
                    files = sorted((child for child in path.iterdir() if child.is_file()), key=lambda child: child.stat().st_mtime, reverse=True)
                    modified = max([path.stat().st_mtime, *(child.stat().st_mtime for child in files)])
                    folders.append({"slug": path.name, "path": str(path), "at": iso(epoch(modified)), "text": ", ".join(child.name for child in files[:8]), "cite": f"incident:{path.name}"})
        return folders

    def ledger(self, moment: datetime, squashed: set[int], closed: set[str]) -> dict[str, list[dict]]:
        rows = {row["key"]: row["fields"] for row in json.loads(run(["ccn", "-R", self.entry["checkout"], "ledger", "show", self.entry["ledger"], "--json"]))["rows"]}
        prs = {key: fields for key, fields in rows.items() if key.isdigit()}
        live = ledger.live_since(rows)
        asks = [
            {
                "key": key,
                "state": ledger.ask_state(fields, prs, moment, live) or "new",
                "at": fields.get("asked_at"),
                "cite": f"ask:{key}",
                **{name: fields.get(name) for name in ("lane", "text", "accept", "prs", "answer")},
            }
            for key, fields in rows.items()
            if key.startswith(ledger.ASK_PREFIX)
        ]
        open_rows, landed = [], []
        for pr, fields in prs.items():
            summary = {
                "pr": int(pr),
                "url": f"https://github.com/{self.entry['repo']}/pull/{pr}",
                "cite": f"pr:{pr}",
                "lane": fields.get("lane"),
                "title": fields.get("title"),
                "text": fields.get("title") or "",
                "head": ledger.current_head(fields)[:12],
                "checks": fields.get("test_state"),
                "mergeable": fields.get("mergeable_state"),
                "hold": fields.get("hold_reason") if ledger.is_held(fields) else None,
                "waiting": ledger.waiting_reason(fields) if fields.get("reported_head") or fields.get("registered") else "",
                "queued": "queued" if ledger.carries_label(fields) else "",
                "rules": "rules-blocked" if ledger.rules_blocked(rows, pr) else "",
                "at": fields.get("landed_at") or fields.get("reported_at") or fields.get("registered_at") or fields.get("created_at"),
                "state": fields.get("state", "open"),
            }
            if ledger.is_open(fields) and int(pr) not in squashed and summary["cite"] not in closed:
                open_rows.append(summary)
            elif fields.get("state") == ledger.LANDED and fields.get("landed_at"):
                landed.append(summary)
        return {"asks": asks, "prs": open_rows, "landed": landed}

    def squashed(self) -> set[int]:
        log = run(["git", "log", "origin/HEAD", f"-{ledger.SQUASH_DEPTH}", "--format=%s"], cwd=self.entry["checkout"])
        return {int(match[1]) for subject in log.splitlines() if (match := ledger.SQUASH_SUBJECT.search(subject))}

    def tasks(self, sessions: list[str]) -> tuple[list[dict], str | None]:
        for session in reversed(sessions):
            if directory := task_list_dir(session, transcript_of(self.entry, session)):
                return [task | {"at": task.get("updated_at"), "text": task.get("subject", ""), "cite": f"task:{task['id']}"} for task in read_tasks(directory)], str(directory)
        return [], None

    def compactions(self, sessions: list[str]) -> list[dict]:
        found = []
        for session in sessions:
            path = transcript_of(self.entry, session)
            if not path.exists():
                continue
            offset, seen = self.compaction_cache.get(session, (0, []))
            if path.stat().st_size < offset:
                offset, seen = 0, []
            fresh, offset = compactions_in(path, offset)
            seen = seen + fresh
            self.compaction_cache[session] = (offset, seen)
            found += seen
        return [item | {"cite": f"compaction:{item['at']}", "text": f"{item['trigger']} compaction {item['pre_tokens']} -> {item['post_tokens']} tokens"} for item in found]

    def boards(self) -> list[dict]:
        port = PRESENT_PORT.search(run(["cc-present", "sessions"]))
        with urllib.request.urlopen(f"http://127.0.0.1:{port['port']}/api/sessions", timeout=HEALTH_TIMEOUT_SECONDS) as response:
            listing = json.loads(response.read())
        other_roots = {session for entry in drive.drives() if entry["drive"] != self.entry["drive"] for session in entry["sessions"]}
        rows = []
        for board in listing:
            if board.get("sessionId") in other_roots:
                continue
            row = {
                "title": board.get("title") or board["slug"],
                "slug": board["slug"],
                "status": board["status"],
                "at": board.get("updatedAt"),
                "events": board.get("eventCount"),
                "url": f"http://127.0.0.1:{port['port']}/p/{board['slug']}",
                "cite": f"board:{board['slug']}",
                "session": board.get("sessionId"),
            }
            if board["status"] == "open":
                row |= self.board_outcomes(board)
            rows.append(row | {"text": row["title"]})
        return rows

    def board_outcomes(self, board: dict) -> dict:
        marker = f"{board.get('updatedAt')}:{board.get('revision')}:{board.get('eventCount')}"
        cached = self.board_cache.get(board["subject"])
        if cached and cached[0] == marker:
            return cached[1]
        outcomes = json.loads(run(["cc-present", "outcomes", "--session", board["sessionId"]]))
        outcome = outcomes["interactions"]
        answered = sum(len(outcome.get(kind) or {}) for kind in ("decisions", "choices", "inputs"))
        asks = sum(not (block["type"].startswith("display.") or block["type"] in PASSIVE_BLOCKS) for block in outcomes["doc"]["blocks"])
        facts = {"submitted": "submitted" if (outcome.get("submitted") or {}).get("value") else "not submitted", "answered": answered, "asks": asks, "closed": bool((outcome.get("closed") or {}).get("value"))}
        self.board_cache[board["subject"]] = (marker, facts)
        return facts

    def notes(self) -> dict[str, list[dict]]:
        program = program_of(self.entry)
        return {
            "plans": [ccn_row("plan", item) for item in self.ccn("plan", "list", "--json")],
            "progress": [ccn_row("progress", item) for item in self.ccn("doc", "list", "--label", f"progress:{program}", "--json")],
            "handoffs": [ccn_row("handoff", item) for item in self.ccn("doc", "list", "--label", "handoff", "--json", "--limit", str(DOC_LIMIT))],
            "docs": [ccn_row("doc", item) for item in self.ccn("doc", "list", "--label", program, "--json", "--limit", str(DOC_LIMIT * 2))],
            "logs": [ccn_row("log", item) for item in self.ccn("log", "list", "--json")],
            "investigations": [ccn_row("investigation", item) for item in self.ccn("investigation", "list", "--json")],
            "answers": [ccn_row("answer", item) for item in self.ccn("answer", "list", "--json", "--limit", str(DOC_LIMIT * 2))],
        }

    def orca(self) -> dict[str, list[dict]]:
        if not (run_id := self.entry.get("orca_run")):
            return {"orca": [], "orca_attention": []}
        tasks = json.loads(run(["orca", "orchestration", "task-list", "--run", run_id, "--json"]))["result"]["tasks"]
        workers = self.orca_workers(run_id)
        names = {task["id"]: task.get("display_name") or task.get("task_title") for task in tasks}
        live = {task["id"] for task in tasks if task["status"] not in ("completed", "failed")}
        rows = [
            {
                "id": task["id"],
                "name": names[task["id"]],
                "status": task["status"],
                "at": task.get("completed_at") or task.get("created_at"),
                "text": f"{names[task['id']]} {task['status']}",
                "cite": f"orca:{task['id']}",
            }
            for task in tasks
        ]
        attention = [
            {
                "dispatch": worker["dispatchId"],
                "name": names.get(worker["taskId"]),
                "categories": ", ".join((worker["projection"].get("attention") or {}).get("categories", [])),
                "activity": (worker["projection"].get("stage") or {}).get("activity"),
                "text": names.get(worker["taskId"]) or worker["dispatchId"],
                "cite": f"orca:{worker['dispatchId']}",
            }
            for worker in workers
            if worker["taskId"] in live and (worker.get("projection", {}).get("attention") or {}).get("requiresAction")
        ]
        return {"orca": rows, "orca_attention": attention}

    def orca_workers(self, run_id: str) -> list[dict]:
        workers: list[dict] = []
        cursor: list[str] = []
        while True:
            page = json.loads(run(["orca", "orchestration", "worker-list", "--run", run_id, "--json", "--limit", "100", *cursor]))["result"]
            workers += page["workers"]
            if not page["page"].get("hasMore"):
                return workers
            cursor = ["--cursor", page["page"]["nextCursor"]]

    def watches(self, moment: datetime) -> list[dict]:
        rows = []
        for path in sorted([*self.inbox_dir.glob(".*.beat"), *self.state_dir.glob("*.beat"), *self.inbox_dir.glob(".*.json")]):
            stat = path.stat()
            row = {"name": path.name, "path": str(path), "at": iso(epoch(stat.st_mtime)), "age_minutes": int(moment.timestamp() - stat.st_mtime) // 60, "text": path.name, "cite": f"watch:{path.name}"}
            if path.suffix == ".json":
                payload = json.loads(path.read_text())
                row["pending"] = len(payload.get("pending", [])) if isinstance(payload, dict) else None
            rows.append(row)
        return rows

    def release_builds(self, config: dict, moment: datetime) -> list[dict]:
        pipeline = config.get("pipeline", "release")
        if self.builds is None:
            self.builds = platy.Builds(self.state_dir / BUILDS_FILE, lambda query: json.loads(run(["bk", "api", f"/pipelines/{pipeline}/builds?{urlencode({'per_page': platy.PER_PAGE} | query, doseq=True)}"], cwd=self.entry["checkout"])))
        if moment >= self.builds_due:
            self.builds_due = moment + BUILDS_INTERVAL
            try:
                self.builds.refresh(moment)
            except subprocess.CalledProcessError as failure:
                if RATE_LIMITED not in (failure.stderr or ""):
                    raise
                self.builds_due = moment + RATE_LIMIT_BACKOFF
        return build_rows(self.builds.known())

    def known_builds(self) -> list[dict]:
        return build_rows(self.builds.known()) if self.builds else []

    def pipeline_change(self, config: dict) -> dict:
        sha, at, subject = run(["git", "log", "-1", "--format=%H%x09%cI%x09%s", config["trunk"], "--", config["release_code"]], cwd=self.entry["checkout"]).strip().split("\t", 2)
        return {"sha": sha, "at": iso(datetime.fromisoformat(at)), "subject": subject}

    def contains(self, ancestor: str, commit: str) -> bool:
        key = (ancestor, commit)
        if key not in self.ancestry:
            verdict = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, commit], cwd=self.entry["checkout"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
            if verdict.returncode not in (0, 1):
                raise subprocess.CalledProcessError(verdict.returncode, verdict.args, verdict.stdout, verdict.stderr)
            self.ancestry[key] = verdict.returncode == 0
        return self.ancestry[key]

    def cci(self, path: str, timeout: float = HEALTH_TIMEOUT_SECONDS, **query):
        with urllib.request.urlopen(f"{CCI_URL}/{path}?{urlencode({'drive': program_of(self.entry)} | query, doseq=True)}", timeout=timeout) as response:
            return json.load(response)

    def digest(self) -> dict:
        return self.cci("digest", since_time=self.entry["started_at"])

    def records(self, since: datetime, **query) -> list[dict]:
        found: list[dict] = []
        while True:
            page = self.cci("records", COMMAND_TIMEOUT_SECONDS, since_time=iso(since), limit=CCI_PAGE, **({"since": found[-1]["seq"]} if found else {}), **query)
            found += page
            if len(page) < CCI_PAGE:
                return found

    def subjects(self, commits: set[str]) -> dict[str, str]:
        missing = sorted(commit for commit in commits if commit and commit not in self.subject_cache)
        if missing:
            data = subprocess.run(["git", "cat-file", "--batch"], input="\n".join(missing).encode() + b"\n", cwd=self.entry["checkout"], capture_output=True, check=True, timeout=COMMAND_TIMEOUT_SECONDS).stdout
            offset = 0
            for commit in missing:
                end = data.index(b"\n", offset)
                header = data[offset:end].split()
                offset = end + 1
                if header[-1] in (b"missing", b"ambiguous"):
                    continue
                size = int(header[2])
                body = data[offset : offset + size].decode(errors="replace")
                offset += size + 1
                if header[1] == b"commit":
                    self.subject_cache[commit] = body.split("\n\n", 1)[1].split("\n", 1)[0]
        return self.subject_cache

    def actions(self) -> list[dict]:
        path = self.state_dir / ACTIONS_FILE
        return json.loads(path.read_text()) if path.exists() else []

    def act(self, row: dict, action: str, text: str) -> dict:
        label = OWNER_ACTIONS[action]
        tail = f" — {text}" if text else ""
        room = CCI_TEXT - len(f"{label}:  [{row['cite']}]{tail}")
        if room < HEADER_CHARS // 4:
            raise ValueError(f"the text is {len(text)} characters; keep it under {CCI_TEXT - HEADER_CHARS} so the item still fits")
        item = row["text"] if len(row["text"]) <= room else row["text"][: room - 1] + "…"
        stored = json.loads(run([str(CCI_BIN), "post", "--drive", program_of(self.entry), "--lane", "owner", "--kind", "owner", "--to", "main", "--text", f"{label}: {item} [{row['cite']}]{tail}", "--json"]))
        record = {"cite": row["cite"], "action": action, "text": text, "at": stored["at"], "seq": stored["seq"]}
        path = self.state_dir / ACTIONS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
        staged.write_text(json.dumps([*self.actions(), record]))
        staged.replace(path)
        return record

    def replies(self, actions: list[dict]) -> list[dict]:
        if not actions:
            return []
        return self.records(min(views.stamp(action["at"]) for action in actions), to="owner")

    def platy(self, config: dict, builds: list[dict], lines: list[dict], blockers: list[dict]) -> list[dict]:
        report = views.newest(self.state_dir, config["census"])
        if report is None:
            raise FileNotFoundError(f"no census report matches {config['census']} under {self.state_dir}")
        targets = platy.target_map(Path(self.entry["checkout"]) / config.get("targets", "release/targets.yaml"))
        rows = platy.stack_rows(platy.census_rows(report), targets, builds, lines, blockers, config.get("overrides") or {}, self.pipeline_change(config), self.contains)
        return [row | {"cite": f"stack:{row['stack']}", "census_report": report.name} for row in rows]

    def owner(self, tasks: list[dict], asks: list[dict], lines: list[dict], boards: list[dict], config: dict, moment: datetime) -> list[dict]:
        rows = []
        for task in tasks:
            if task.get("status") != "completed" and not task.get("archived") and is_owner_task(task):
                rows.append({"kind": "task", "title": task.get("subject"), "state": task.get("status"), "at": task.get("at"), "url": None, "cite": task["cite"]})
        for ask in asks:
            if ask["state"] not in OWNER_DONE_ASKS:
                rows.append({"kind": "ask", "title": ask.get("text"), "state": ask["state"], "at": ask.get("at"), "url": None, "cite": ask["cite"]})
        for line in open_decisions(lines, iso(moment - LANE_WINDOW)):
            rows.append({"kind": "decide", "title": line["text"], "state": line["lane"], "at": line["at"], "url": line["url"], "cite": line["cite"]})
        started = views.stamp(self.entry["started_at"])
        for board in boards:
            if board["status"] == "open" and board.get("submitted") != "submitted" and board.get("asks") and not board.get("closed") and views.stamp(board["at"]) >= started:
                rows.append({"kind": "board", "title": board["title"], "state": f"{board.get('submitted', '')}, {board.get('answered', 0)} answered", "at": board["at"], "url": board["url"], "cite": board["cite"]})
        for name in config.get("owner_files") or []:
            path = self.state_dir / name
            if path.exists():
                rows += [{"kind": "pending", "title": f"{item['title']}: {item['detail']}", "state": name, "at": iso(epoch(path.stat().st_mtime)), "url": None, "cite": f"file:{name}:{hashlib.sha1(item['title'].encode()).hexdigest()[:8]}"} for item in owner_bullets(path)]
        for number, item in enumerate(manual_items(config.get("owner")), 1):
            rows.append({"kind": "manual", "title": item.get("text"), "state": "manual", "at": None, "url": item.get("url"), "cite": f"manual:owner:{number}"})
        return [row | {"text": row["title"] or "", "banner": row["kind"] == "board"} for row in rows]

    def snapshot(self) -> tuple[dict, dict[str, list[dict]]]:
        moment = now()
        errors: dict[str, str] = {}

        def guarded(name: str, read, default):
            try:
                return read()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError, KeyError, TypeError) as failure:
                errors[name] = failure_text(failure)
                return default

        self.entry = guarded("registry", lambda: drive.find(self.entry["drive"], None), None) or self.entry
        config = guarded("dashboard.yaml", self.config, {})
        sessions = self.entry["sessions"]
        lines = [
            line.row() | {"url": f"/inbox/{line.file}?line={line.number}", "cite": f"inbox:{line.file}:{line.number}"}
            for line in guarded("inbox", self.inbox, [])
        ]
        tasks, task_dir = guarded("tasks", lambda: self.tasks(sessions), ([], None))
        closers = guarded("cci closers", lambda: self.records(views.stamp(self.entry["started_at"]), kind=CLOSING_KINDS), [])
        closed = overview.curated(closers)
        squashed = guarded("trunk squashes", self.squashed, set())
        ledger_rows = guarded("ledger", lambda: self.ledger(moment, squashed, closed), {"asks": [], "prs": [], "landed": []})
        boards = guarded("boards", self.boards, [])
        compactions = guarded("compactions", lambda: self.compactions(sessions), [])
        platy_config = config.get("platy")
        builds = (guarded("builds", lambda: self.release_builds(platy_config, moment), None) or self.known_builds()) if platy_config else []
        sources: dict[str, list[dict]] = {
            "inbox": lines,
            "lanes": self.lanes(lines, tasks, moment),
            "tasks": tasks,
            "incidents": guarded("incidents", self.incidents, []),
            "boards": boards,
            "compactions": compactions,
            "watches": guarded("watches", lambda: self.watches(moment), []),
            "builds": builds,
            "manual": [item | {"section": section, "cite": f"manual:{section}:{number}"} for section, items in config.items() if section in MANUAL_SECTIONS for number, item in enumerate(manual_items(items), 1)],
            **ledger_rows,
            **guarded("notes", self.notes, {kind: [] for kind in NOTE_SOURCES}),
            **guarded("orca", self.orca, {"orca": [], "orca_attention": []}),
        }
        digest = guarded("cci", lambda: overview.uncurated(self.digest(), closed), None)
        actions = guarded("owner actions", self.actions, [])
        owner_rows = guarded("owner", lambda: self.owner(tasks, ledger_rows["asks"], lines, boards, config, moment), [])
        replies = guarded("owner replies", lambda: self.replies(actions), [])
        sources["owner"] = still_open(with_actions(owner_rows, actions, replies), closed)
        if platy_config and digest is None:
            errors["platy"] = "the cci digest is unreadable, so blockers are unknown"
        sources["platy"] = guarded("platy", lambda: self.platy(platy_config, builds, lines, cci_blockers(digest)), []) if platy_config and digest is not None else []
        sources["platy_targets"] = platy.target_rows(sources["platy"])
        subjects = guarded("commit subjects", lambda: self.subjects({build["commit"] for build in builds[: overview.RELEASE_LIMIT * 2]}), {})
        sources["releases"] = overview.release_rows(builds, subjects, self.entry["repo"], (platy_config or {}).get("slack"), overview.slack_links(lines, (platy_config or {}).get("pipeline", "release")), moment)
        sources["cci_lanes"] = guarded("cci lanes", lambda: self.cci("lanes"), [])
        incident_records = guarded("cci incidents", lambda: self.records(moment - INCIDENT_WINDOW, kind=list(overview.INCIDENT_KINDS)), [])
        open_incidents = {record["seq"] for record in (digest or {}).get("open_incidents") or []}
        resolved = {record["resolves"] for record in [*closers, *incident_records] if record.get("resolves")}
        sources["incident_groups"] = overview.incident_groups(incident_records, open_incidents, sources["incidents"], moment, closed, resolved)
        sources["holds"] = overview.standing_holds([*((digest or {}).get("open_holds") or []), *((digest or {}).get("untracked_holds") or [])], closers)
        census = guarded("cci census", lambda: self.records(moment - CENSUS_WINDOW, kind="state"), [])
        titles = {row["pr"]: row["title"] for row in [*ledger_rows["prs"], *ledger_rows["landed"]] if row.get("title")}
        sources["landings"] = overview.landings(guarded("cci landings", lambda: self.records(moment - LANDED_WINDOW, kind="landed"), []), titles, self.entry["repo"])
        latest = compactions[-1] if compactions else {}
        sources["drive"] = [
            {key: self.entry.get(key) for key in ("drive", "ledger", "repo", "checkout", "state_dir", "orca_run", "started_at")}
            | {
                "program": program_of(self.entry),
                "task_dir": task_dir,
                "sessions": ", ".join(sessions),
                "compactions": len(compactions),
                "last_compaction": latest.get("at"),
                "open_prs": len(ledger_rows["prs"]),
                "open_tasks": sum(1 for task in tasks if task.get("status") in ("in_progress", "pending")),
                "inbox_lines": len(lines),
                "at": iso(moment),
            }
        ]
        if host := guarded("tailnet", tailnet_host, None):
            sources = {name: [on_tailnet(row, host) for row in rows] for name, rows in sources.items()}
        loaders = Loaders(self.state_dir, self.entry["checkout"])
        rendered = []
        for spec in views.merge(default_views(), config.get("views") or []):
            try:
                data = views.render(spec, sources, loaders, moment)
                rendered.append({key: spec.get(key) for key in VIEW_KEYS} | {"data": data})
            except (views.SpecError, re.error, KeyError, ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as failure:
                rendered.append({key: spec.get(key) for key in VIEW_KEYS} | {"error": failure_text(failure)})
        state = {
            "generated_at": iso(moment),
            "builds_at": iso(self.builds.fetched_at) if self.builds else None,
            "drive": sources["drive"][0],
            "errors": errors,
            "overview": overview.overview(
                sources["releases"],
                {"total": len(sources["platy"]), "at_zero": sum(row["zero"] == "0/0" for row in sources["platy"])} if platy_config and "platy" not in errors else None,
                sources["platy"],
                len(ledger_rows["prs"]),
                sources["landings"],
                sources["cci_lanes"],
                sources["incident_groups"],
                sources["holds"],
                census,
                moment,
            ),
            "owner": sources["owner"],
            "releases": sources["releases"],
            "incidents": sources["incident_groups"],
            "replies": replies,
            "lanes": [lane for lane in sources["cci_lanes"] if moment - views.stamp(lane["at"]) <= LANE_WINDOW],
            "views": rendered,
            "sections": [str(section) for section in config.get("sections") or []],
        }
        return state, sources


@dataclass
class Loaders:
    state_dir: Path
    checkout: str

    def text(self, spec: dict) -> str:
        if ident := spec.get("ccn"):
            return run(["ccn", "-R", self.checkout, "show", str(ident)])
        path = views.newest(self.state_dir, spec["file"])
        if path is None:
            raise FileNotFoundError(f"nothing matches {spec['file']} under {self.state_dir}")
        return path.read_text(errors="replace")

    def table(self, spec: dict) -> list[dict]:
        rows = views.select_table(self.text(spec), spec.get("table"))
        return [{key.replace(" ", "_").replace("/", "_"): value for key, value in row.items()} for row in rows]


def default_views() -> list[dict]:
    return yamlish.loads(DEFAULT_VIEWS.read_text())["views"]


def page(action_token: str) -> bytes:
    return PAGE.read_text().replace("</body>", CHAT_WIDGET.read_text() + "</body>").replace("{{action_token}}", action_token).encode()


def tailnet_host() -> str | None:
    try:
        status = json.loads(run(["tailscale", "status", "--json"]))
    except FileNotFoundError:
        return None
    return status["Self"]["DNSName"].rstrip(".") if status.get("BackendState") == "Running" else None


def reachable(url: str, host: str) -> str:
    parts = urlsplit(url)
    if parts.hostname not in LOOPBACK:
        return url
    return parts._replace(netloc=f"{host}:{parts.port}" if parts.port else host).geturl()


def on_tailnet(row: dict, host: str) -> dict:
    return {key: reachable(value, host) if (key == "url" or key.endswith("_url")) and isinstance(value, str) else value for key, value in row.items()}


def share(port: int) -> str | None:
    target = f"127.0.0.1:{port}"
    try:
        if not (host := tailnet_host()):
            return None
        forwards = json.loads(run(["tailscale", "serve", "status", "--json"]) or "{}").get("TCP") or {}
        if (current := forwards.get(str(port), {}).get("TCPForward", target)) != target:
            print(f"not shared on the tailnet: port {port} already forwards to {current}", file=sys.stderr, flush=True)
            return None
        run(["tailscale", "serve", "--bg", "--yes", "--tcp", str(port), f"tcp://{target}"])
    except subprocess.CalledProcessError as failure:
        print(f"not shared on the tailnet: {(failure.stderr or '').strip() or failure}", file=sys.stderr, flush=True)
        return None
    return url_of(host, port)


def unshare(port: int) -> None:
    run(["tailscale", "serve", "--tcp", str(port), "off"])


def preferred_port(drive_id: str) -> int:
    return PORT_BASE + int(hashlib.sha1(drive_id.encode()).hexdigest()[:8], 16) % PORT_SPAN


def server_file(entry: dict) -> Path:
    return Path(entry["state_dir"]) / SERVER_FILE


class Dashboard(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], collector: Collector, interval: float):
        super().__init__(address, Handler)
        self.token = secrets.token_hex(16)
        self.chat_token = secrets.token_hex(16)
        self.action_token = secrets.token_hex(16)
        self.acting = threading.Lock()
        self.authorities = {f"127.0.0.1:{self.server_address[1]}", f"localhost:{self.server_address[1]}"}
        self.collector = collector
        self.interval = interval
        self.state: dict = {"generated_at": None, "drive": {"drive": collector.entry["drive"]}, "views": [], "errors": {}}
        self.sources: dict[str, list[dict]] = {}
        self.stopping = threading.Event()

    def poll(self) -> None:
        while not self.stopping.is_set():
            self.state, self.sources = self.collector.snapshot()
            self.stopping.wait(self.interval)


class Handler(BaseHTTPRequestHandler):
    server: Dashboard

    def log_message(self, format: str, *args: object) -> None:
        return

    def send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def text(self, status: int, body: str) -> None:
        self.send(status, body.encode(), "text/plain; charset=utf-8")

    @property
    def origin(self) -> str:
        return f"http://{self.headers['Host']}"

    def sources(self) -> chat.Sources:
        collector = self.server.collector
        return chat.Sources(
            state=self.server.state,
            state_dir=collector.state_dir,
            rows=lambda: self.server.sources,
            ccn=chat.run_ccn(collector.entry["checkout"]),
        )

    def foreign(self) -> bool:
        if self.headers.get("Host") in self.server.authorities:
            return False
        self.text(421, "misdirected request\n")
        return True

    def json(self, payload) -> None:
        self.send(200, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        if self.foreign():
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        entry = self.server.collector.entry
        if url.path == "/":
            self.send(200, page(self.server.action_token), "text/html; charset=utf-8")
        elif url.path == "/state.json":
            self.json(self.server.state)
        elif url.path == "/healthz":
            self.json({"drive": entry["drive"], "script": str(Path(__file__).resolve()), "pid": os.getpid()})
        elif url.path.startswith("/ccn/") and CCN_ID.match(ident := url.path.removeprefix("/ccn/")):
            try:
                self.text(200, run(["ccn", "-R", entry["checkout"], "show", ident]))
            except subprocess.CalledProcessError as failure:
                self.text(404, failure.stderr)
        elif url.path.startswith("/inbox/"):
            self.inbox_excerpt(unquote_url(url.path.removeprefix("/inbox/")), query)
        elif url.path == "/ai.json":
            if chat.key():
                self.send(200, json.dumps(chat.site_config(self.server.chat_token)).encode(), "application/json")
            else:
                self.text(404, f"{chat.KEY_ENV} is not set in the dashboard's environment\n")
        elif url.path.startswith("/sources/") and url.path.endswith(".json"):
            name = url.path.removeprefix("/sources/").removesuffix(".json")
            if name in self.server.sources:
                self.json(self.server.sources[name])
            else:
                self.text(404, f"no source {name}; sources: {', '.join(sorted(self.server.sources))}\n")
        elif url.path.startswith("/ask/"):
            self.ask(url.path.removeprefix("/ask/"), {name: values[0] for name, values in parse_qs(url.query).items()})
        else:
            self.text(404, "not found")

    def ask(self, verb: str, query: dict[str, str]) -> None:
        sources = self.sources()
        try:
            if verb == "digest":
                self.text(200, chat.digest(sources.state))
            elif verb == "search":
                self.send(200, json.dumps(chat.search(sources, query.get("query", ""))).encode(), "application/json")
            elif verb == "read":
                self.text(200, chat.read(sources, query.get("ref", "")))
            elif verb == "view":
                self.send(200, chat.view(sources.state, query.get("id")).encode(), "application/json")
            else:
                self.text(404, "not found")
        except (LookupError, ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as failure:
            detail = getattr(failure, "stderr", "") or str(failure)
            self.text(404, f"{detail}".strip() + "\n")

    def inbox_excerpt(self, name: str, query: dict[str, list[str]]) -> None:
        inbox = Path(self.server.collector.entry["state_dir"]) / "inbox"
        if name not in {str(path.relative_to(inbox)) for path in inbox_files(inbox)}:
            self.text(404, "not found")
            return
        requested = query.get("line", [""])[0]
        self.text(200, chat.excerpt(inbox, name, int(requested) if requested.isdigit() else None, CONTEXT_LINES))

    def relay(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {self.server.chat_token}" or self.headers.get("Origin", self.origin) != self.origin:
            self.text(403, "forbidden\n")
            return
        request = chat.relay(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            upstream = urllib.request.urlopen(request, timeout=chat.UPSTREAM_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as failure:
            self.send_response(failure.code)
            for name in ("Content-Type", "Retry-After"):
                if value := failure.headers.get(name):
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(failure.read())
            return
        with upstream:
            self.send_response(upstream.status)
            self.send_header("Content-Type", upstream.headers.get("Content-Type", "text/event-stream"))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for chunk in chat.stream(upstream):
                self.wfile.write(chunk)
                self.wfile.flush()

    def owner_action(self) -> None:
        if not secrets.compare_digest(self.headers.get(ACTION_HEADER, ""), self.server.action_token) or self.headers.get("Origin", self.origin) != self.origin:
            self.text(403, "forbidden\n")
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        row = next((row for row in self.server.sources.get("owner") or [] if row["cite"] == body.get("cite")), None)
        if row is None or body.get("action") not in OWNER_ACTIONS:
            self.text(404, "no such owner item or action\n")
            return
        try:
            with self.server.acting:
                record = self.server.collector.act(row, body["action"], " ".join(str(body.get("text") or "").split()))
        except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as failure:
            self.text(422 if isinstance(failure, ValueError) else 502, failure_text(failure) + "\n")
            return
        self.json(record)

    def do_POST(self) -> None:
        if self.foreign():
            return
        if urlparse(self.path).path == "/ai/chat/completions":
            self.relay()
            return
        if urlparse(self.path).path == "/owner/act":
            self.owner_action()
            return
        if urlparse(self.path).path != "/shutdown":
            self.text(404, "not found")
            return
        if not secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), self.server.token):
            self.text(403, "forbidden\n")
            return
        self.text(200, "stopping\n")
        self.server.stopping.set()
        threading.Thread(target=self.server.shutdown, daemon=True).start()



def bind(host: str, port: int, collector: Collector, interval: float) -> Dashboard:
    try:
        return Dashboard((host, port), collector, interval)
    except OSError:
        return Dashboard((host, 0), collector, interval)


def url_of(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


def cmd_serve(args: argparse.Namespace) -> int:
    entry = resolve(args.drive, args.session)
    server = bind(args.host, args.port or preferred_port(entry["drive"]), Collector(entry), args.interval)
    host, port = server.server_address[:2]
    record = {"drive": entry["drive"], "pid": os.getpid(), "host": host, "port": port, "url": url_of(host, port), "script": str(Path(__file__).resolve()), "token": server.token, "started_at": iso(now())}
    if tailnet_url := share(port):
        record["tailnet_url"] = tailnet_url
        server.authorities.add(urlparse(tailnet_url).netloc)
    path = server_file(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(json.dumps(record, indent=2) + "\n")
    staged.replace(path)
    threading.Thread(target=server.poll, daemon=True).start()
    print(public_url(record), flush=True)
    server.serve_forever()
    if tailnet_url:
        unshare(port)
    return 0


def public_url(record: dict) -> str:
    return record.get("tailnet_url", record["url"])


def health(record: dict) -> dict | None:
    try:
        with urllib.request.urlopen(f"{record['url']}healthz", timeout=HEALTH_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def running(entry: dict) -> dict | None:
    path = server_file(entry)
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    alive = health(record)
    return record | {"alive": alive} if alive and alive["drive"] == entry["drive"] else None


def retire(record: dict) -> None:
    request = urllib.request.Request(f"{record['url']}shutdown", method="POST", data=b"", headers={TOKEN_HEADER: record["token"]})
    with urllib.request.urlopen(request, timeout=HEALTH_TIMEOUT_SECONDS):
        pass
    deadline = time.monotonic() + START_WAIT_SECONDS
    while health(record) and time.monotonic() < deadline:
        time.sleep(0.2)


def cmd_start(args: argparse.Namespace) -> int:
    entry = resolve(args.drive, args.session)
    lock = Path(entry["state_dir"]) / START_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        return start(entry, args.host)


def start(entry: dict, host: str) -> int:
    script = str(Path(__file__).resolve())
    if record := running(entry):
        if record["alive"]["script"] == script:
            print(public_url(record))
            return 0
        retire(record)
    log = Path(entry["state_dir"]) / SERVER_LOG
    previous = server_file(entry).read_text() if server_file(entry).exists() else ""
    with log.open("a") as sink:
        subprocess.Popen(
            [sys.executable, script, "serve", "--drive", entry["drive"], "--host", host],
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=sink,
            start_new_session=True,
            cwd=entry["state_dir"],
        )
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if server_file(entry).exists() and server_file(entry).read_text() != previous and (record := running(entry)):
            print(public_url(record))
            return 0
        time.sleep(0.2)
    raise SystemExit(f"the dashboard did not come up within {START_WAIT_SECONDS:.0f}s; see {log}")


def cmd_url(args: argparse.Namespace) -> int:
    if not (record := running(resolve(args.drive, args.session))):
        return 1
    print(public_url(record))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    print(json.dumps(Collector(resolve(args.drive, args.session)).snapshot()[0], indent=2))
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    if not (record := running(resolve(args.drive, args.session))):
        raise SystemExit("no dashboard is serving this drive; start it with lr-dashboard.py start")
    sources = {}
    for name in ("owner", "incident_groups", "holds", "prs"):
        with urllib.request.urlopen(f"{record['url']}sources/{name}.json", timeout=COMMAND_TIMEOUT_SECONDS) as response:
            sources[name] = json.load(response)
    print("\n".join(open_items(sources)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lr-dashboard.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("serve", cmd_serve), ("start", cmd_start), ("url", cmd_url), ("snapshot", cmd_snapshot), ("open", cmd_open)):
        sub = subparsers.add_parser(name)
        sub.add_argument("--drive")
        sub.add_argument("--session")
        if name in ("serve", "start"):
            sub.add_argument("--host", default="127.0.0.1")
        if name == "serve":
            sub.add_argument("--port", type=int)
            sub.add_argument("--interval", type=float, default=INTERVAL_SECONDS)
        sub.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
