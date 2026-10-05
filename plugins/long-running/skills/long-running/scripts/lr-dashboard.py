#!/usr/bin/env python3
"""
    lr-dashboard.py serve    [--drive ID] [--host HOST] [--port N] [--interval S]
    lr-dashboard.py start    [--drive ID] [--session ID] [--host HOST]
    lr-dashboard.py url      [--drive ID] [--session ID]
    lr-dashboard.py snapshot [--drive ID] [--session ID]
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
from urllib.parse import parse_qs, urlparse
from urllib.parse import unquote as unquote_url
from zoneinfo import ZoneInfo

import drive
import ledger
from lrdash import chat

PACIFIC = ZoneInfo("America/Los_Angeles")
PAGE = Path(__file__).with_name("lr-dashboard.html")
CHAT_WIDGET = Path(__file__).resolve().parents[1] / "templates" / "lr-dashboard-chat.html"
SERVER_FILE = Path("dashboard") / "server.json"
SERVER_LOG = Path("dashboard") / "server.log"
START_LOCK = Path("dashboard") / "start.lock"
SEEN_FILE = Path("dashboard") / "seen.json"
TOKEN_HEADER = "X-Dashboard-Token"
MANUAL_FILE = "dashboard.yaml"
PORT_BASE = 8700
PORT_SPAN = 300
INTERVAL_SECONDS = 15.0
COMMAND_TIMEOUT_SECONDS = 30
START_WAIT_SECONDS = 10.0
HEALTH_TIMEOUT_SECONDS = 2.0
FEED_LIMIT = 300
LANE_WINDOW = timedelta(hours=48)
LANDED_LIMIT = 40
SLACK = timedelta(hours=2)
FOLLOW = timedelta(hours=16)
HEADER_CHARS = 80
NEW_YEAR = timedelta(days=180)
CONTEXT_LINES = 25
COMPLETED_TASKS = 40
DOC_LIMIT = 20
ORCA_DONE_LIMIT = 20
COMPACT_BOUNDARY = b'"subtype":"compact_boundary"'
CCN_ID = re.compile(r"^[0-9a-f]{7,40}$")

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
    re.compile(rf"^(?P<when>\d{{1,2}}:\d{{2}})\s+(?P<verb>{VERB})\b"),
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
INCIDENT_VERBS = frozenset({"INCIDENT", "ALERT", "PAGE", "MECHANISM", "FIX-LIVE", "RESOLVED", "MITIGATED"})
RELEASE_VERBS = frozenset({"OPENED", "UPDATED", "READY", "LANDED", "RELEASE", "RELEASED", "MERGED", "ENQUEUED", "BUILD", "DEPLOYED", "INSTALLED", "SERVING"})
RULING_VERBS = frozenset({"GO", "HOLD", "LIFT", "DECIDE", "RULING", "ASK", "OWNER", "NOTE", "DESIGN"})
CENSUS_VERBS = frozenset({"STATE", "CENSUS", "MATRIX", "STATUS"})
TABLE_GAP = re.compile(r"\s{2,}")
URL_FIELD = re.compile(r"^https?://\S+$")


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


def parse_manual(text: str) -> dict[str, list[dict]]:
    sections: dict[str, list[dict]] = {}
    current: list[dict] | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0 and stripped.endswith(":"):
            current = sections.setdefault(stripped[:-1].strip(), [])
        elif stripped.startswith("- ") and current is not None:
            item = stripped[2:].strip()
            key, sep, value = item.partition(":")
            if sep and re.fullmatch(r"\w+", key) and not URL_FIELD.match(item):
                current.append({key: unquote(value.strip())})
            else:
                current.append({"text": unquote(item)})
        elif current:
            key, _, value = stripped.partition(":")
            current[-1][key.strip()] = unquote(value.strip())
    return sections


def unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"" else value


def parse_boards(text: str) -> list[dict]:
    rows = [line for line in text.splitlines() if line.strip()]
    header = next((index for index, line in enumerate(rows) if line.startswith("SUBJECT")), None)
    if header is None:
        return []
    boards = []
    for line in rows[header + 1 :]:
        cells = TABLE_GAP.split(line.strip())
        if len(cells) < 6:
            continue
        subject, slug, session, status, events, url = cells[:6]
        boards.append({"subject": subject, "slug": slug, "session": None if session == "-" else session, "status": status, "events": int(events) if events.isdigit() else events, "url": url})
    return boards


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


def run(argv: list[str], cwd: str | None = None) -> str:
    return subprocess.run(argv, capture_output=True, text=True, check=True, cwd=cwd, timeout=COMMAND_TIMEOUT_SECONDS).stdout


def resolve(drive_id: str | None, session: str | None) -> dict:
    entry = drive.find(drive_id, session) if drive_id or session else drive.find(drive.current_drive(), None)
    if not entry:
        raise SystemExit(f"no drive matches drive={drive_id or '-'} session={session or '-'}; register one with drive.py start")
    return entry


def program_of(entry: dict) -> str:
    return Path(entry["state_dir"]).name


@dataclass
class Collector:
    entry: dict
    inbox_cache: dict[str, tuple[float, int, list[Line]]] = field(default_factory=dict)
    seen: dict[str, list[tuple[int, datetime]]] | None = None
    compaction_cache: dict[str, tuple[int, list[dict]]] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)
    task_rows: list[dict] = field(default_factory=list)
    ledger_rows: dict[str, dict] = field(default_factory=dict)

    @property
    def state_dir(self) -> Path:
        return Path(self.entry["state_dir"])

    @property
    def inbox_dir(self) -> Path:
        return self.state_dir / "inbox"

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

    def lanes(self, lines: list[Line], tasks: list[dict], moment: datetime) -> list[dict]:
        latest: dict[str, Line] = {}
        for line in lines:
            if line.lane and line.lane not in latest:
                latest[line.lane] = line
        owners: dict[str, list[dict]] = {}
        for task in tasks:
            if task.get("owner") and task.get("status") in ("in_progress", "pending"):
                owners.setdefault(task["owner"], []).append(task)
        names = {lane for lane, line in latest.items() if line.at and moment - line.at <= LANE_WINDOW} | set(owners)
        rows = []
        for name in names:
            line = latest.get(name)
            rows.append(
                {
                    "lane": name,
                    "last": line.row() if line else None,
                    "idle_minutes": int((moment - line.at).total_seconds() // 60) if line and line.at else None,
                    "tasks": [{"id": task["id"], "subject": task.get("subject"), "status": task.get("status")} for task in owners.get(name, [])],
                }
            )
        return sorted(rows, key=lambda row: (row["last"] or {}).get("at") or "", reverse=True)

    def incidents(self, lines: list[Line]) -> dict:
        directory = self.state_dir / "incidents"
        folders = []
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_dir():
                    files = sorted((child for child in path.iterdir() if child.is_file()), key=lambda child: child.stat().st_mtime, reverse=True)
                    modified = max([path.stat().st_mtime, *(child.stat().st_mtime for child in files)])
                    folders.append({"slug": path.name, "path": str(path), "updated_at": iso(epoch(modified)), "files": [child.name for child in files[:8]]})
        folders.sort(key=lambda folder: folder["updated_at"], reverse=True)
        flagged = [line.row() for line in lines if line.verb in INCIDENT_VERBS or "orca-desk: alert" in line.text]
        return {"lines": flagged[:FEED_LIMIT], "folders": folders[:60]}

    def ledger(self, moment: datetime) -> dict:
        self.ledger_rows = {}
        rows = {row["key"]: row["fields"] for row in json.loads(run(["ccn", "-R", self.entry["checkout"], "ledger", "show", self.entry["ledger"], "--json"]))["rows"]}
        self.ledger_rows = rows
        prs = {key: fields for key, fields in rows.items() if key.isdigit()}
        live = ledger.live_since(rows)
        asks = [
            {"key": key, "state": ledger.ask_state(fields, prs, moment, live) or "new", **{name: fields.get(name) for name in ("lane", "text", "accept", "asked_at", "prs", "answer")}}
            for key, fields in rows.items()
            if key.startswith(ledger.ASK_PREFIX)
        ]
        asks.sort(key=lambda ask: ask.get("asked_at") or "", reverse=True)
        open_rows = []
        landed = []
        for pr, fields in prs.items():
            summary = {
                "pr": pr,
                "url": f"https://github.com/{self.entry['repo']}/pull/{pr}",
                "lane": fields.get("lane"),
                "title": fields.get("title"),
                "head": ledger.current_head(fields)[:12],
                "test_state": fields.get("test_state"),
                "mergeable_state": fields.get("mergeable_state"),
                "hold": fields.get("hold_reason") if ledger.is_held(fields) else None,
                "hold_until": fields.get("hold_until") if ledger.is_held(fields) else None,
                "waiting": ledger.waiting_reason(fields) if fields.get("reported_head") or fields.get("registered") else "",
                "queued": ledger.carries_label(fields),
                "rules_blocked": ledger.rules_blocked(rows, pr),
                "landed_at": fields.get("landed_at"),
            }
            if ledger.is_open(fields):
                open_rows.append(summary)
            elif fields.get("state") == ledger.LANDED and fields.get("landed_at"):
                landed.append(summary)
        open_rows.sort(key=lambda row: int(row["pr"]), reverse=True)
        landed.sort(key=lambda row: row["landed_at"] or "", reverse=True)
        return {
            "asks": asks,
            "open": open_rows,
            "landed": landed[:LANDED_LIMIT],
            "live_at": live or None,
            "counts": {
                "open": len(open_rows),
                "held": sum(1 for row in open_rows if row["hold"]),
                "queued": sum(1 for row in open_rows if row["queued"]),
                "rules_blocked": sum(1 for row in open_rows if row["rules_blocked"]),
                "landed": len(landed),
            },
        }

    def tasks(self, sessions: list[str]) -> tuple[list[dict], str | None]:
        for session in reversed(sessions):
            if directory := task_list_dir(session, transcript_of(self.entry, session)):
                return read_tasks(directory), str(directory)
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
        return sorted(found, key=lambda item: item["at"] or "", reverse=True)

    def boards(self) -> list[dict]:
        other_roots = {session for entry in drive.drives() if entry["drive"] != self.entry["drive"] for session in entry["sessions"]}
        boards = [board for board in parse_boards(run(["cc-present", "sessions"])) if board["session"] not in other_roots]
        return sorted(boards, key=lambda board: board["status"] != "open")

    def notes(self) -> dict:
        program = program_of(self.entry)
        logs = sorted(self.ccn("log", "list", "--json"), key=lambda log: log.get("updated_at") or "", reverse=True)
        answers = self.ccn("answer", "list", "--json", "--limit", str(DOC_LIMIT))
        return {
            "plans": sorted(self.ccn("plan", "list", "--json"), key=lambda plan: plan.get("updated_at") or "", reverse=True),
            "progress": self.ccn("doc", "list", "--label", f"progress:{program}", "--json"),
            "handoffs": self.ccn("doc", "list", "--label", "handoff", "--json", "--limit", str(DOC_LIMIT)),
            "docs": self.ccn("doc", "list", "--label", program, "--json", "--limit", str(DOC_LIMIT)),
            "logs": logs[:DOC_LIMIT],
            "investigations": self.ccn("investigation", "list", "--json"),
            "answers": answers,
        }

    def orca(self) -> dict | None:
        if not (run_id := self.entry.get("orca_run")):
            return None
        tasks = json.loads(run(["orca", "orchestration", "task-list", "--run", run_id, "--json"]))["result"]["tasks"]
        workers = self.orca_workers(run_id)
        names = {task["id"]: task.get("display_name") or task.get("task_title") for task in tasks}
        live = {task["id"] for task in tasks if task["status"] not in ("completed", "failed")}

        def task_row(task: dict) -> dict:
            return {"id": task["id"], "name": task.get("display_name") or task.get("task_title"), "status": task["status"], "created_at": task.get("created_at"), "completed_at": task.get("completed_at")}

        active = [task_row(task) for task in tasks if task["status"] not in ("completed", "failed")]
        done = sorted((task for task in tasks if task["status"] in ("completed", "failed")), key=lambda task: task.get("completed_at") or "", reverse=True)
        attention = [
            {
                "dispatch": worker["dispatchId"],
                "name": names.get(worker["taskId"]),
                "categories": (worker["projection"].get("attention") or {}).get("categories", []),
                "activity": (worker["projection"].get("stage") or {}).get("activity"),
            }
            for worker in workers
            if worker["taskId"] in live and (worker.get("projection", {}).get("attention") or {}).get("requiresAction")
        ]
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        return {"run": run_id, "counts": counts, "active": active, "recent": [task_row(task) for task in done[:ORCA_DONE_LIMIT]], "attention": attention}

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
            row = {"name": path.name, "path": str(path), "updated_at": iso(epoch(stat.st_mtime)), "age_seconds": int(moment.timestamp() - stat.st_mtime)}
            if path.suffix == ".json":
                payload = json.loads(path.read_text())
                row["pending"] = len(payload.get("pending", [])) if isinstance(payload, dict) else None
            rows.append(row)
        return rows

    def manual(self) -> dict:
        path = self.state_dir / MANUAL_FILE
        return {"path": str(path), "sections": parse_manual(path.read_text()) if path.exists() else {}}

    def snapshot(self) -> dict:
        moment = now()
        errors: dict[str, str] = {}
        try:
            self.entry = drive.find(self.entry["drive"], None) or self.entry
        except (OSError, ValueError) as failure:
            errors["registry"] = str(failure)[:300]
        sessions = self.entry["sessions"]

        def guarded(name: str, read, default):
            try:
                return read()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as failure:
                detail = getattr(failure, "stderr", "") or str(failure)
                detail = detail.decode(errors="replace") if isinstance(detail, bytes) else detail
                errors[name] = (detail.strip().splitlines() or [type(failure).__name__])[-1][:300]
                return default

        lines = guarded("inbox", self.inbox, [])
        tasks, task_dir = guarded("tasks", lambda: self.tasks(sessions), ([], None))
        self.lines, self.task_rows = lines, tasks
        open_tasks = sorted((task for task in tasks if task.get("status") in ("in_progress", "pending")), key=lambda task: (task.get("status") != "in_progress", -int(task["id"]) if str(task["id"]).isdigit() else 0))
        completed = sorted((task for task in tasks if task.get("status") == "completed"), key=lambda task: task.get("updated_at") or "", reverse=True)
        compactions = guarded("compactions", lambda: self.compactions(sessions), [])
        return {
            "generated_at": iso(moment),
            "drive": {key: self.entry.get(key) for key in ("drive", "ledger", "repo", "checkout", "state_dir", "orca_run", "sessions", "started_at")} | {"program": program_of(self.entry), "task_dir": task_dir},
            "errors": errors,
            "lanes": self.lanes(lines, tasks, moment),
            "incidents": guarded("incidents", lambda: self.incidents(lines), {"lines": [], "folders": []}),
            "rulings": [line.row() for line in lines if line.verb in RULING_VERBS][:FEED_LIMIT],
            "releases": [line.row() for line in lines if line.verb in RELEASE_VERBS][:FEED_LIMIT],
            "census": [line.row() for line in lines if line.verb in CENSUS_VERBS][:FEED_LIMIT],
            "feed": [line.row() for line in lines[:FEED_LIMIT]],
            "ledger": guarded("ledger", lambda: self.ledger(moment), None),
            "tasks": {"open": open_tasks, "completed": completed[:COMPLETED_TASKS], "total": len(tasks)},
            "boards": guarded("boards", self.boards, []),
            "notes": guarded("notes", self.notes, None),
            "compactions": {"count": len(compactions), "last_at": compactions[0]["at"] if compactions else None, "recent": compactions[:20]},
            "orca": guarded("orca", self.orca, None),
            "watches": guarded("watches", lambda: self.watches(moment), []),
            "manual": guarded("manual", self.manual, {"sections": {}}),
        }


def page() -> bytes:
    return PAGE.read_text().replace("</body>", CHAT_WIDGET.read_text() + "</body>").encode()


def tailnet_host() -> str | None:
    try:
        status = json.loads(run(["tailscale", "status", "--json"]))
    except FileNotFoundError:
        return None
    return status["Self"]["DNSName"].rstrip(".") if status.get("BackendState") == "Running" else None


def share(port: int) -> str | None:
    if not (host := tailnet_host()):
        return None
    run(["tailscale", "serve", "--bg", "--yes", "--tcp", str(port), f"tcp://127.0.0.1:{port}"])
    return url_of(host, port)


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
        self.collector = collector
        self.interval = interval
        self.state: dict = {"generated_at": None, "drive": {"drive": collector.entry["drive"]}}
        self.stopping = threading.Event()

    def poll(self) -> None:
        while not self.stopping.is_set():
            self.state = self.collector.snapshot()
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
            inbox=lambda: collector.lines,
            tasks=lambda: collector.task_rows,
            ledger_rows=lambda: collector.ledger_rows,
            ccn=chat.run_ccn(collector.entry["checkout"]),
        )

    def do_GET(self) -> None:
        url = urlparse(self.path)
        entry = self.server.collector.entry
        if url.path == "/":
            self.send(200, page(), "text/html; charset=utf-8")
        elif url.path == "/state.json":
            self.send(200, json.dumps(self.server.state).encode(), "application/json")
        elif url.path == "/healthz":
            self.send(200, json.dumps({"drive": entry["drive"], "script": str(Path(__file__).resolve()), "pid": os.getpid()}).encode(), "application/json")
        elif url.path.startswith("/ccn/") and CCN_ID.match(ident := url.path.removeprefix("/ccn/")):
            try:
                self.text(200, run(["ccn", "-R", entry["checkout"], "show", ident]))
            except subprocess.CalledProcessError as failure:
                self.text(404, failure.stderr)
        elif url.path.startswith("/inbox/"):
            self.inbox_excerpt(unquote_url(url.path.removeprefix("/inbox/")), parse_qs(url.query))
        elif url.path == "/ai.json":
            if chat.key():
                self.send(200, json.dumps(chat.site_config(self.server.chat_token)).encode(), "application/json")
            else:
                self.text(404, f"{chat.KEY_ENV} is not set in the dashboard's environment\n")
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
            elif verb == "state":
                self.send(200, chat.section(sources.state, query.get("section")).encode(), "application/json")
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

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/ai/chat/completions":
            self.relay()
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
    path = server_file(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(json.dumps(record, indent=2) + "\n")
    staged.replace(path)
    threading.Thread(target=server.poll, daemon=True).start()
    print(record["url"], flush=True)
    server.serve_forever()
    return 0


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
            print(record["url"])
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
            print(record["url"])
            return 0
        time.sleep(0.2)
    raise SystemExit(f"the dashboard did not come up within {START_WAIT_SECONDS:.0f}s; see {log}")


def cmd_url(args: argparse.Namespace) -> int:
    if not (record := running(resolve(args.drive, args.session))):
        return 1
    print(record.get("tailnet_url", record["url"]))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    print(json.dumps(Collector(resolve(args.drive, args.session)).snapshot(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lr-dashboard.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("serve", cmd_serve), ("start", cmd_start), ("url", cmd_url), ("snapshot", cmd_snapshot)):
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
