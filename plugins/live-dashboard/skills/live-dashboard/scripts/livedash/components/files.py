from __future__ import annotations

import csv
import io
import json
import re
from collections import deque
from pathlib import Path

from livedash import Col, Context, Entry, Feed, Table, Tile, Tiles, component, view

LEADING_TIME = re.compile(r"^\[?(?P<at>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)")
COLUMN = re.compile(r"^(?P<key>[\w.-]+)(?::(?P<kind>\w+))?$")


def read_rows(path: Path, rows_key: str | None = None) -> list[dict]:
    text = path.read_text()
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if path.suffix in (".csv", ".tsv"):
        return list(csv.DictReader(io.StringIO(text), delimiter="\t" if path.suffix == ".tsv" else ","))
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if rows_key is None:
        raise ValueError(f"{path.name} holds a JSON object; name the list of rows inside it with rows_key")
    return data[rows_key]


def number(value) -> float | None:
    parsed = view.number(value)
    return float(parsed) if parsed is not None else None


@component("log-tail", "Log tail", every="15s")
def log_tail(ctx: Context, *, path: Path, lines: int = 40, highlight: str | None = None) -> Feed:
    """The last `lines` lines of a log file, newest first; a leading ISO time dates a line, and `highlight` marks matches bad."""
    if not path.exists():
        return Feed([], note=f"{path} does not exist yet.")
    with path.open(errors="replace") as handle:
        tail = deque(handle, maxlen=lines)
    pattern = re.compile(highlight) if highlight else None
    entries = []
    for number_, line in enumerate(reversed(tail)):
        text = line.rstrip("\n")
        stamp = LEADING_TIME.match(text)
        entries.append(Entry(stamp["at"] if stamp else "", path.name, text[:400], tone="bad" if pattern and pattern.search(text) else None, key=f"{path.name}:{number_}"))
    return Feed(entries)


@component("kv-file", "Numbers from a file", every="1m")
def kv_file(ctx: Context, *, path: Path, keys: list[str] = [], units: dict[str, str] = {}) -> Tiles:
    """One tile per top-level key of a JSON object file, or per `key: value` line; `keys` picks and orders them."""
    if not path.exists():
        return Tiles([], note=f"{path} does not exist yet.")
    text = path.read_text()
    data = json.loads(text) if path.suffix == ".json" else dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
    data = {str(key).strip(): value.strip() if isinstance(value, str) else value for key, value in data.items()}
    return Tiles([Tile(key, data.get(key), units.get(key)) for key in (keys or list(data))])


def columns_of(specs: list[str], rows: list[dict]) -> list[Col]:
    if not specs:
        return [Col(key, key) for key in (rows[0] if rows else {}) if key not in ("url", "key", "cite", "tone")]
    out = []
    for spec in specs:
        if not (match := COLUMN.match(spec)):
            raise ValueError(f"column {spec!r} must look like key or key:kind")
        out.append(Col(match["key"], match["key"].replace("_", " "), match["kind"] or "text"))
    return out


@component("view", "View", every="15s")
def table_view(
    ctx: Context,
    *,
    source: str | None = None,
    file: str | None = None,
    ccn: str | None = None,
    table: str | None = None,
    rows_key: str | None = None,
    where: dict[str, str] = {},
    since: str | None = None,
    match: str | None = None,
    match_field: str = "text",
    sort: str | None = None,
    latest_by: str | None = None,
    limit: int = 200,
    columns: list[str] = [],
) -> Table:
    """The declarative engine: rows from another card's Table (`source`, a card id), the newest file matching `file`
    (markdown tables, JSON, JSONL, CSV or TSV), or a cc-notes record (`ccn`); then `where` regexes (a leading ! negates),
    `since` (30m, 48h, 7d), `match` named groups, `sort` (-field for descending), `latest_by`, and `limit`. `columns`
    takes `key` or `key:kind`."""
    if source:
        found = ctx.latest(source)
        if found is None:
            raise LookupError(f"card {source} has no payload yet")
        rows = list(found.rows)
    elif file:
        matches = ctx.glob(file)
        if not matches:
            return Table(columns_of(columns, []) or [Col("file", "File")], [], note=f"Nothing matches {file} yet.")
        newest = matches[0]
        rows = view.select_table(newest.read_text(errors="replace"), table) if newest.suffix == ".md" else read_rows(newest, rows_key)
    elif ccn:
        rows = view.select_table(ctx.ccn("show", ccn), table)
    else:
        raise ValueError("a view needs source, file or ccn")
    spec = {"where": where, "since": since, "match": match, "match_field": match_field, "latest_by": latest_by}
    if sort:
        spec["sort"] = sort
    kept = view.pipeline([{key.replace(" ", "_").replace("/", "_"): value for key, value in row.items()} for row in rows], spec, ctx.now)
    return Table(columns_of(columns, kept), kept[:limit], note=f"showing {limit} of {len(kept)}" if len(kept) > limit else None)
