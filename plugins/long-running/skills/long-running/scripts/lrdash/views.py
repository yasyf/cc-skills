from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

TYPES = ("stat", "series", "table", "timeline", "matrix", "progress", "kv", "links", "markdown")
DEFAULT_LIMIT = 200
SPARK_POINTS = 48
DURATION = re.compile(r"^(\d+)([mhd])$")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
BUCKETS = {"hour": 3600, "day": 86400}
TABLE_ROW = re.compile(r"^\|(.*)\|\s*$")
TABLE_RULE = re.compile(r"^\|[\s:|-]+\|\s*$")


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


def shape(kind: str, rows: list[dict], spec: dict, moment: datetime) -> dict:
    limit = int(spec.get("limit", DEFAULT_LIMIT))
    if kind == "table":
        columns = [column if isinstance(column, dict) else {"field": column} for column in spec.get("columns") or []]
        if not columns and rows:
            columns = [{"field": field} for field in rows[0] if field not in ("url",)]
        return {"columns": columns, "rows": rows[:limit], "total": len(rows), "link": spec.get("link", "url")}
    if kind == "timeline":
        return {"rows": rows[:limit], "total": len(rows)}
    if kind == "stat":
        field = spec["value"]
        valued = [row for row in rows if number(row.get(field)) is not None]
        values = [number(row.get(field)) for row in valued]
        first = valued[0] if valued else {}
        of = spec.get("of")
        return {
            "value": values[0] if values else None,
            "of": number(first.get(of)) if isinstance(of, str) else of,
            "delta": values[0] - values[1] if len(values) > 1 else None,
            "spark": list(reversed(values[:SPARK_POINTS])),
            "at": first.get("at"),
            "detail": {key: first.get(key) for key in spec.get("detail", [])} if first else {},
            "unit": spec.get("unit", ""),
            "url": first.get(spec.get("link", "url")),
        }
    if kind == "series":
        return {"series": series(rows, spec)}
    if kind == "matrix":
        return matrix(rows, spec)
    if kind == "progress":
        excluded = [row for row in rows if any(matches(row.get(f), str(p)) for f, p in (spec.get("exclude") or {}).items())]
        counted = [row for row in rows if row not in excluded]
        done = [row for row in counted if all(matches(row.get(f), str(p)) for f, p in (spec.get("done") or {}).items())]
        return {"done": len(done), "total": len(counted), "excluded": len(excluded)}
    if kind == "kv":
        first = rows[0] if rows else {}
        items = []
        for item in spec.get("fields") or []:
            entry = item if isinstance(item, dict) else {"field": item}
            items.append({"label": entry.get("label", entry["field"]), "value": first.get(entry["field"]), "url": first.get(entry["link"]) if entry.get("link") else None})
        return {"items": items}
    if kind == "links":
        label, url, note = spec.get("label", "title"), spec.get("url", "url"), spec.get("note")
        return {"items": [{"label": row.get(label), "url": row.get(url), "note": row.get(note) if note else None, "at": row.get("at")} for row in rows[:limit]]}
    raise SpecError(f"unknown view type {kind!r}; use one of {', '.join(TYPES)}")


def series(rows: list[dict], spec: dict) -> list[dict]:
    x = spec.get("x", "at")
    if bucket := spec.get("bucket"):
        size = BUCKETS[bucket]
        group = spec.get("group")
        counts: dict[str, dict[int, int]] = {}
        for row in rows:
            if not (at := stamp(row.get(x))):
                continue
            slot = int(at.timestamp()) // size * size
            name = str(row.get(group)) if group else spec.get("title", "count")
            counts.setdefault(name, {})
            counts[name][slot] = counts[name].get(slot, 0) + 1
        return [{"name": name, "points": [[iso_epoch(slot), value] for slot, value in sorted(points.items())]} for name, points in sorted(counts.items())]
    out = []
    for field in spec.get("y") or []:
        points = [[row.get(x), number(row.get(field))] for row in rows if number(row.get(field)) is not None]
        out.append({"name": field, "points": sorted(points, key=lambda point: str(point[0]))})
    return out


def iso_epoch(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def matrix(rows: list[dict], spec: dict) -> dict:
    row_field, col_field, value_field = spec["rows"], spec["cols"], spec["value"]
    cells: dict[str, dict[str, dict]] = {}
    row_names: list[str] = []
    col_names: list[str] = []
    for row in rows:
        r, c = str(row.get(row_field)), str(row.get(col_field))
        if r in cells and c in cells[r]:
            continue
        if r not in cells:
            cells[r] = {}
            row_names.append(r)
        if c not in col_names:
            col_names.append(c)
        cells[r][c] = {"value": row.get(value_field), "title": row.get(spec.get("title_field", "text")), "url": row.get(spec.get("link", "url"))}
    order = spec.get("col_order")
    col_names = [c for c in order if c in col_names] + [c for c in col_names if c not in order] if order else sorted(col_names)
    return {"rows": sorted(row_names) if spec.get("sort_rows", True) else row_names, "cols": col_names, "cells": cells}


def render(spec: dict, sources: dict[str, list[dict]], loaders, moment: datetime) -> dict:
    kind = spec.get("type", "table")
    if kind not in TYPES:
        raise SpecError(f"unknown view type {kind!r}; use one of {', '.join(TYPES)}")
    if kind == "markdown":
        return {"text": loaders.text(spec) if spec.get("file") or spec.get("ccn") else str(spec.get("text", ""))}
    if "source" in spec:
        if spec["source"] not in sources:
            raise SpecError(f"unknown source {spec['source']!r}; known: {', '.join(sorted(sources))}")
        rows = sources[spec["source"]]
    elif spec.get("file") or spec.get("ccn"):
        rows = loaders.table(spec)
    else:
        raise SpecError("a view needs source, file, or ccn")
    return shape(kind, pipeline(rows, spec, moment), spec, moment)


def merge(defaults: list[dict], declared: list[dict]) -> list[dict]:
    by_id = {view["id"]: view for view in defaults}
    order = [view["id"] for view in defaults]
    for view in declared:
        if view.get("hide"):
            order = [ident for ident in order if ident != view["id"]]
            continue
        if view["id"] not in by_id:
            order.append(view["id"])
        by_id[view["id"]] = (by_id.get(view["id"], {}) | view) if view.get("extend") else view
    return [by_id[ident] for ident in order]
