from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
SLACK = timedelta(hours=2)
FOLLOW = timedelta(hours=16)
HEADER_CHARS = 80
NEW_YEAR = timedelta(days=180)
SEEN_FILE = "seen.json"
NOT_VERBS = frozenset({"PT", "PDT", "PST", "PM", "AM", "UTC", "PR", "PRS", "CI", "API", "AWS", "SHA", "URL", "IAM", "DNS", "ID", "OK", "UI", "DB", "SQL", "JSON", "HTTP", "GH", "MCP", "LGTM"})
PR_REF = re.compile(r"(?:https://github\.com/(?P<url_repo>[\w.-]+/[\w.-]+)/pull/(?P<url_pr>\d+))|(?:(?<![\w/])(?P<repo>[\w.-]+/[\w.-]+))?#(?P<pr>\d{2,6})\b")
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


def inbox_files(directory: Path) -> list[Path]:
    return sorted([*directory.glob("*.md"), *directory.glob("*.md.archive/*.md")])


@dataclass
class Inbox:
    inbox_dir: Path
    seen_file: Path
    cache: dict[str, tuple[float, int, list[Line]]] = field(default_factory=dict)
    seen: dict[str, list[tuple[int, datetime]]] | None = None

    def lines(self) -> list[Line]:
        found: list[Line] = []
        for path in inbox_files(self.inbox_dir):
            name = str(path.relative_to(self.inbox_dir))
            stat = path.stat()
            cached = self.cache.get(name)
            if not cached or cached[:2] != (stat.st_mtime, stat.st_size):
                text = path.read_text(errors="replace")
                parsed = parse_inbox(name, text, self.anchors(name, len(text.splitlines()), epoch(stat.st_mtime)))
                self.cache[name] = cached = (stat.st_mtime, stat.st_size, parsed)
            found += cached[2]
        return sorted(found, key=lambda line: (line.at or datetime.min.replace(tzinfo=timezone.utc), line.number), reverse=True)

    def anchors(self, name: str, count: int, modified: datetime) -> list[tuple[int, datetime]]:
        if self.seen is None:
            stored = json.loads(self.seen_file.read_text()) if self.seen_file.exists() else {}
            self.seen = {file: [(lines, datetime.fromisoformat(at)) for lines, at in marks] for file, marks in stored.items()}
        marks = self.seen.setdefault(name, [])
        if marks and marks[-1][0] > count:
            marks.clear()
        if not marks or marks[-1][0] < count:
            marks.append((count, modified))
            self.seen_file.parent.mkdir(parents=True, exist_ok=True)
            self.seen_file.write_text(json.dumps({file: [(lines, iso(at)) for lines, at in seen] for file, seen in self.seen.items()}))
        return [*marks, (count, modified)]
