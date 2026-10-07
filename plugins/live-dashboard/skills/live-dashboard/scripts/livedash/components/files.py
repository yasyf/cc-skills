from __future__ import annotations

import csv
import io
import json
import re
from collections import deque
from pathlib import Path

from livedash import Cell, Col, Context, Matrix, Table, Tile, Tiles, component, view

LEADING_TIME = re.compile(r"^\[?(?P<at>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)")
COLUMN = re.compile(r"^(?P<key>[\w.-]+)(?::(?P<kind>\w+))?$")
CELL_TONES = {"pass": "ok", "passed": "ok", "fail": "bad", "failed": "bad", "rerun-pending": "warn", "pending": "warn", "running": "warn", "untested": "muted", "skipped": "muted"}
MATCH_COLUMNS = [Col("pattern", "Pattern"), Col("count", "Matches", "num"), Col("last", "Last match", "age")]


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


@component("log-matches", "Log matches", question="How often do the watched patterns show up in the log, and when last?", reads=["log file"], every="15s")
def log_matches(ctx: Context, *, path: Path, patterns: dict[str, str], lines: int = 2000) -> Table:
    """For each named regex in `patterns`, how many of the last `lines` lines of a log file match and when the newest match
    was logged, read from a leading ISO time. The line text never reaches the board."""
    if not path.exists():
        return Table(MATCH_COLUMNS, [], note=f"{path} does not exist yet.")
    with path.open(errors="replace") as handle:
        tail = list(deque(handle, maxlen=lines))
    rows = []
    for name, regex in patterns.items():
        pattern = re.compile(regex)
        hits = [line for line in tail if pattern.search(line)]
        stamp = LEADING_TIME.match(hits[-1]) if hits else None
        rows.append({"key": name, "cite": f"log:{path.name}:{name}", "pattern": name, "count": len(hits), "last": stamp["at"] if stamp else None, "tone": "bad" if hits else "ok"})
    return Table(MATCH_COLUMNS, rows, note=f"last {len(tail)} lines of {path.name}")


@component("kv-file", "Numbers from a file", question="What are the latest numbers in this file?", reads=["JSON or key: value file"], every="1m")
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


@component("view", "View", question="Which rows of this table, file or record match the filter?", reads=["another card", "files", "ccn"], every="15s")
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


@component("matrix-file", "Matrix from a file", question="Which cells pass, fail, or have not run yet?", reads=["result file"], every="1m")
def matrix_file(
    ctx: Context,
    *,
    file: str,
    table: str | None = None,
    rows_key: str | None = None,
    row: str = "row",
    col: str = "col",
    value: str = "status",
    link: str = "link",
    detail: list[str] = ["detail"],
    rows: list[str] = [],
    cols: list[str] = [],
    tones: dict[str, str] = {},
    missing: str = "untested",
) -> Matrix:
    """The newest file matching `file` (JSON, JSONL, CSV, TSV, or a markdown table picked by `table`) as a grid: each result
    row lands in the cell its `row` and `col` fields name, showing its `value`, opening its `link` field, and hovering its
    `detail` fields; a later row for the same cell wins. `rows` and `cols` fix the order and show cells with no result as
    `missing`. A value tones by `tones`, else pass is ok, fail bad, pending or rerun-pending warn, untested muted."""
    found = ctx.glob(file)
    results = (view.select_table(found[0].read_text(errors="replace"), table) if found[0].suffix == ".md" else read_rows(found[0], rows_key)) if found else []
    if not results and not (rows and cols):
        raise LookupError(f"nothing matches {file} yet; name rows and cols to show every cell as {missing}")
    cells = {(str(result[row]), str(result[col])): result for result in results}
    row_labels = rows or list(dict.fromkeys(name for name, _ in cells))
    col_labels = cols or list(dict.fromkeys(name for _, name in cells))
    toning = CELL_TONES | {key.lower(): tone for key, tone in tones.items()}

    def cell(name: str, column: str) -> Cell:
        result = cells.get((name, column))
        if result is None:
            return Cell(missing, toning.get(missing.lower()))
        shown = str(result.get(value) or missing)
        hover = "; ".join(str(result[field]) for field in detail if result.get(field))
        return Cell(shown, toning.get(shown.lower()), hover or None, result.get(link) or None)

    return Matrix(row_labels, col_labels, [[cell(name, column) for column in col_labels] for name in row_labels])
