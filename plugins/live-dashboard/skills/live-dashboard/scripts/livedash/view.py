from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_LIMIT = 200
DURATION = re.compile(r"^(\d+)([mhd])$")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
TABLE_ROW = re.compile(r"^\|(.*)\|\s*$")
TABLE_RULE = re.compile(r"^\|[\s:|-]+\|\s*$")
SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ROOT = "root"


class SpecError(ValueError):
    pass


def window(text: str) -> timedelta:
    if not (match := DURATION.match(str(text))):
        raise SpecError(f"since must look like 30m, 48h or 7d, not {text!r}")
    return timedelta(**{UNITS[match[2]]: int(match[1])})


def stamp(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def lane_name(lane: str | None, facts: dict) -> str | None:
    if lane and (SESSION_ID.match(lane) or lane in (facts.get("sessions") or [])):
        return ROOT
    return lane


def iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def iso_epoch(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def markdown_tables(text: str) -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = {}
    heading = ""
    header: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("#"):
            heading, header = line.lstrip("#").strip(), None
            continue
        if not (match := TABLE_ROW.match(line)):
            header = None
            continue
        if TABLE_RULE.match(line):
            continue
        cells = [cell.strip() for cell in match[1].split("|")]
        if header is None:
            header = [cell.lower() for cell in cells]
            tables.setdefault(heading, [])
            continue
        if len(cells) == len(header):
            tables[heading].append(dict(zip(header, cells, strict=True)))
    return tables


def select_table(text: str, heading: str | None) -> list[dict]:
    tables = markdown_tables(text)
    if heading is None:
        return [row for rows in tables.values() for row in rows]
    for name, rows in tables.items():
        if heading.lower() in name.lower():
            return rows
    return []


def newest(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime)
    return matches[-1] if matches else None


def matches(value, pattern: str) -> bool:
    negate = pattern.startswith("!")
    hit = re.search(pattern[1:] if negate else pattern, "" if value is None else str(value), re.IGNORECASE) is not None
    return hit != negate


def number(value):
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return None


def pipeline(rows: list[dict], spec: dict, moment: datetime) -> list[dict]:
    for field, pattern in (spec.get("where") or {}).items():
        rows = [row for row in rows if matches(row.get(field), str(pattern))]
    if since := spec.get("since"):
        floor = moment - window(since)
        rows = [row for row in rows if (at := stamp(row.get("at"))) and at >= floor]
    if pattern := spec.get("match"):
        compiled = re.compile(pattern)
        field = spec.get("match_field", "text")
        found = []
        for row in rows:
            if hit := compiled.search(str(row.get(field) or "")):
                found.append(row | {key: value for key, value in hit.groupdict().items() if value is not None})
        rows = found
    sort = spec.get("sort", "-at" if rows and "at" in rows[0] else None)
    if sort:
        key = sort.lstrip("-")
        present = sorted((row for row in rows if row.get(key) is not None), key=lambda row: sort_key(row.get(key)), reverse=sort.startswith("-"))
        rows = present + [row for row in rows if row.get(key) is None]
    if latest_by := spec.get("latest_by"):
        seen, kept = set(), []
        for row in rows:
            if (marker := row.get(latest_by)) not in seen:
                seen.add(marker)
                kept.append(row)
        rows = kept
    return rows


def sort_key(value):
    parsed = number(value)
    return (0, parsed, "") if parsed is not None else (1, 0, str(value or ""))
