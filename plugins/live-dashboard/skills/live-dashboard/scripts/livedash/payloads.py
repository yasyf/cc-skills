from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import ddshared

TONES = ("ok", "warn", "bad", "muted")
COL_KINDS = ("text", "num", "age", "due", "link", "badge", "delta", "bar")
GATE_STATUSES = ("open", "pass", "fail", "blocked", "waived", "not-created", "not-run")
MAX_BYTES = 256 * 1024
SCHEMAS = Path(__file__).resolve().parents[2] / "reference" / "components"


class PayloadError(ValueError):
    pass


def tone_of(value: str | None) -> str | None:
    if value is not None and value not in TONES:
        raise PayloadError(f"tone must be one of {', '.join(TONES)}, not {value!r}")
    return value


def dropped(data: dict) -> dict:
    return {key: value for key, value in data.items() if value is not None and value != [] and value != {}}


@dataclass(frozen=True)
class Col:
    key: str
    label: str = ""
    kind: str = "text"
    phone: bool = True

    def __post_init__(self) -> None:
        if self.kind not in COL_KINDS:
            raise PayloadError(f"column {self.key} has kind {self.kind!r}; use one of {', '.join(COL_KINDS)}")


@dataclass(frozen=True)
class Table:
    """Rows under typed columns; a `link` column reads its URL from `<key>_url`, and each row may carry `key`, `cite` and `tone`."""

    cols: list[Col]
    rows: list[dict]
    group_by: str | None = None
    footer: dict | None = None
    note: str | None = None

    kind = "table"

    def __post_init__(self) -> None:
        for row in self.rows:
            tone_of(row.get("tone"))

    def json(self) -> dict:
        return dropped({"kind": self.kind, "group_by": self.group_by, "footer": self.footer, "note": self.note}) | {"cols": [asdict(col) for col in self.cols], "rows": self.rows}

    @classmethod
    def example(cls) -> Table:
        return cls(
            cols=[Col("pr", "PR", "link"), Col("title", "Title"), Col("ci", "CI", "badge"), Col("opened", "Opened", "age")],
            rows=[{"key": "101", "pr": "#101", "pr_url": "https://github.com/o/r/pull/101", "title": "Add the parser", "ci": "green", "opened": "2026-10-06T09:00:00Z", "tone": "ok"}],
        )


@dataclass(frozen=True)
class Cell:
    text: str
    tone: str | None = None
    title: str | None = None
    link: str | None = None

    def __post_init__(self) -> None:
        tone_of(self.tone)


def matrix_schema() -> dict:
    schema = copy.deepcopy(json.loads((SCHEMAS / "dd.matrix.json").read_text()))
    for axis in ("rows", "cols"):
        schema["properties"][axis].pop("maxItems")
    cell = schema["properties"]["cells"]["items"]["items"]
    cell["properties"]["title"] = {"type": "string", "minLength": 1}
    cell["properties"]["link"] = {"type": "string", "minLength": 1}
    cell["properties"]["tone"]["enum"] = list(TONES)
    return schema


@dataclass(frozen=True)
class Matrix:
    """A dd.matrix grid: row labels against column labels, one Cell each; a cell's `title` is its hover detail and its
    `link` the page it opens."""

    rows: list[str]
    cols: list[str]
    cells: list[list[Cell]]
    title: str | None = None
    pick: str | None = None

    kind = "matrix"

    def __post_init__(self) -> None:
        if len(self.cells) != len(self.rows) or any(len(row) != len(self.cols) for row in self.cells):
            raise PayloadError(f"a matrix needs one row of {len(self.cols)} cells per row label, {len(self.rows)} rows")

    def json(self) -> dict:
        return dropped({"kind": "dd.matrix", "title": self.title, "pick": self.pick}) | {
            "rows": [{"label": label} for label in self.rows],
            "cols": [{"label": label} for label in self.cols],
            "cells": [[dropped(asdict(cell)) for cell in row] for row in self.cells],
        }

    def schema_errors(self) -> list[str]:
        return ddshared.schema_errors(self.json(), matrix_schema(), "matrix")

    @classmethod
    def example(cls) -> Matrix:
        return cls(
            rows=["#101", "#102"],
            cols=["Ruling A", "Ruling B"],
            cells=[[Cell("meets", "ok"), Cell("deviates", "warn", "Uses the old flag name")], [Cell("meets", "ok"), Cell("meets", "ok")]],
        )


@dataclass(frozen=True)
class Gate:
    id: str
    title: str
    status: str
    blocker: str | None = None
    owner: str | None = None
    closes: str | None = None
    link: str | None = None

    def __post_init__(self) -> None:
        if self.status not in GATE_STATUSES:
            raise PayloadError(f"gate {self.id} has status {self.status!r}; use one of {', '.join(GATE_STATUSES)}")


@dataclass(frozen=True)
class Checklist:
    """Gates with a status each, open through pass, fail, blocked, waived, not-created or not-run."""

    items: list[Gate]
    note: str | None = None

    kind = "checklist"

    def json(self) -> dict:
        return dropped({"kind": self.kind, "note": self.note}) | {"items": [dropped(asdict(gate) | {"key": gate.id}) for gate in self.items]}

    @classmethod
    def example(cls) -> Checklist:
        return cls(items=[Gate("V6", "Latency bench on staging", "not-run", blocker="waits on the bench PR", owner="bench lane", closes="#101"), Gate("Q6", "Floor picked", "pass")])


@dataclass(frozen=True)
class Tile:
    label: str
    value: str | int | float | None
    unit: str | None = None
    tone: str | None = None
    hint: str | None = None
    link: str | None = None

    def __post_init__(self) -> None:
        tone_of(self.tone)


@dataclass(frozen=True)
class Tiles:
    tiles: list[Tile]
    note: str | None = None

    kind = "tiles"

    def json(self) -> dict:
        return dropped({"kind": self.kind, "note": self.note}) | {"tiles": [dropped(asdict(tile)) | {"key": tile.label, "value": tile.value} for tile in self.tiles]}

    @classmethod
    def example(cls) -> Tiles:
        return cls(tiles=[Tile("Open PRs", 13, tone="warn", hint="all held for review"), Tile("p95 lookup", 182, "ms", "ok")])


@dataclass(frozen=True)
class Line:
    label: str
    points: list[list]


@dataclass(frozen=True)
class Series:
    """Lines of `[at, value]` points, where `at` is an ISO time or a number, with optional labelled threshold rules."""

    lines: list[Line]
    unit: str | None = None
    thresholds: dict[str, float] = field(default_factory=dict)
    note: str | None = None

    kind = "series"

    def json(self) -> dict:
        return dropped({"kind": self.kind, "unit": self.unit, "thresholds": self.thresholds, "note": self.note}) | {"lines": [asdict(line) for line in self.lines]}

    @classmethod
    def example(cls) -> Series:
        return cls(lines=[Line("p95", [["2026-10-06T09:00:00Z", 140], ["2026-10-06T10:00:00Z", 182]])], unit="ms", thresholds={"budget": 250})


def nearest_rank(ordered: list[float], percent: float) -> float:
    return ordered[max(math.ceil(percent / 100 * len(ordered)) - 1, 0)]


@dataclass(frozen=True)
class Dist:
    label: str
    n: int
    p50: float | None
    p95: float | None
    p99: float | None
    max: float | None
    unit: str = "ms"
    budget: float | None = None
    failures: int | None = None

    @classmethod
    def of(cls, label: str, samples: list[float], unit: str = "ms", budget: float | None = None, failures: int | None = None) -> Dist:
        """Nearest-rank percentiles of the samples; an empty sample list reports n=0 and no values."""
        ordered = sorted(samples)
        if not ordered:
            return cls(label, 0, None, None, None, None, unit, budget, failures)
        return cls(label, len(ordered), nearest_rank(ordered, 50), nearest_rank(ordered, 95), nearest_rank(ordered, 99), ordered[-1], unit, budget, failures)


@dataclass(frozen=True)
class Percentiles:
    dists: list[Dist]
    history: Series | None = None
    note: str | None = None

    kind = "percentiles"

    def json(self) -> dict:
        return dropped({"kind": self.kind, "history": self.history.json() if self.history else None, "note": self.note}) | {"dists": [asdict(dist) | {"key": dist.label} for dist in self.dists]}

    @classmethod
    def example(cls) -> Percentiles:
        return cls(dists=[Dist.of("cold", [120.0, 140.0, 182.0, 240.0], budget=250), Dist.of("cached", [8.0, 9.5, 12.0], budget=50)])


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    tone: str | None = None
    link: str | None = None

    def __post_init__(self) -> None:
        tone_of(self.tone)


@dataclass(frozen=True)
class Graph:
    """Nodes and directed `[from, to]` edges, laid out in layers from the roots; `highlight` rings nodes."""

    nodes: list[Node]
    edges: list[list[str]]
    highlight: list[str] = field(default_factory=list)

    kind = "graph"

    def __post_init__(self) -> None:
        known = {node.id for node in self.nodes}
        for edge in self.edges:
            if len(edge) != 2 or not set(edge) <= known:
                raise PayloadError(f"edge {edge} must join two of the graph's node ids")

    def json(self) -> dict:
        return dropped({"kind": self.kind, "highlight": self.highlight}) | {"nodes": [dropped(asdict(node)) | {"key": node.id} for node in self.nodes], "edges": [list(edge) for edge in self.edges]}

    @classmethod
    def example(cls) -> Graph:
        return cls(nodes=[Node("dev", "dev", "muted"), Node("101", "#101", "ok"), Node("102", "#102", "warn")], edges=[["dev", "101"], ["101", "102"]], highlight=["101"])


@dataclass(frozen=True)
class Entry:
    at: str
    actor: str
    text: str
    link: str | None = None
    tone: str | None = None
    latency_ms: float | None = None
    key: str | None = None
    cite: str | None = None

    def __post_init__(self) -> None:
        tone_of(self.tone)


@dataclass(frozen=True)
class Feed:
    """Timestamped entries, newest first; `latency_ms` shows how long an entry took from its origin."""

    entries: list[Entry]
    note: str | None = None

    kind = "feed"

    def json(self) -> dict:
        return dropped({"kind": self.kind, "note": self.note}) | {"entries": [dropped(asdict(entry)) for entry in self.entries]}

    @classmethod
    def example(cls) -> Feed:
        return cls(entries=[Entry("2026-10-06T16:54:48Z", "Iris", "Posted the page card", tone="ok", latency_ms=42751)])


@dataclass(frozen=True)
class Kv:
    pairs: dict[str, str | int | float | None]

    kind = "kv"

    def json(self) -> dict:
        return {"kind": self.kind, "pairs": [[label, value] for label, value in self.pairs.items()]}

    @classmethod
    def example(cls) -> Kv:
        return cls(pairs={"Approve #101": "lands #101 and #102", "Unblocks": 2})


@dataclass(frozen=True)
class Markdown:
    text: str

    kind = "markdown"

    def json(self) -> dict:
        return {"kind": self.kind, "text": self.text}

    @classmethod
    def example(cls) -> Markdown:
        return cls("**Next gate:** the staging bench run.")


@dataclass(frozen=True)
class Svg:
    """An SVG drawing; the page keeps only allowlisted tags and attributes, so no script or external reference survives."""

    svg: str

    kind = "svg"

    def json(self) -> dict:
        return {"kind": self.kind, "svg": self.svg}

    @classmethod
    def example(cls) -> Svg:
        return cls('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 20"><rect width="80" height="20" fill="#1baf7a"/></svg>')


def problems(payload) -> list[str]:
    found = payload.schema_errors() if isinstance(payload, Matrix) else []
    size = len(json.dumps(payload.json(), default=str).encode())
    if size > MAX_BYTES:
        found.append(f"payload is {size // 1024}KB, over the {MAX_BYTES // 1024}KB cap")
    return found


def cell_text(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown(kind: str, payload: dict) -> str:
    note = payload.get("note")
    return "\n\n".join(part for part in (body(kind, payload), f"_{note}_" if note else "") if part)


def body(kind: str, payload: dict) -> str:
    if kind == "table":
        cols = payload["cols"] + ([{"key": "cite", "label": "Cite"}] if any(row.get("cite") for row in payload["rows"]) else [])
        lines = ["| " + " | ".join(col["label"] or col["key"] for col in cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(cell_text(row.get(col["key"])) for col in cols) + " |" for row in payload["rows"]]
        if footer := payload.get("footer"):
            lines.append("| " + " | ".join(cell_text(footer.get(col["key"])) for col in cols) + " |")
        return "\n".join(lines)
    if kind == "matrix":
        lines = ["| | " + " | ".join(col["label"] for col in payload["cols"]) + " |", "|---|" + "---|" * len(payload["cols"])]
        for row, cells in zip(payload["rows"], payload["cells"], strict=True):
            lines.append(f"| {cell_text(row['label'])} | " + " | ".join(cell_text((f"[{cell['text']}]({cell['link']})" if cell.get("link") else cell["text"]) + (f" ({cell['title']})" if cell.get("title") else "")) for cell in cells) + " |")
        return "\n".join(lines)
    if kind == "checklist":
        return "\n".join(f"- [{'x' if item['status'] in ('pass', 'waived') else ' '}] {item['id']} {item['title']}: {item['status']}" + "".join(f"; {name} {item[name]}" for name in ("blocker", "owner", "closes") if item.get(name)) for item in payload["items"])
    if kind == "tiles":
        return "\n".join(f"- {tile['label']}: {tile['value']}{' ' + tile['unit'] if tile.get('unit') else ''}{' (' + tile['hint'] + ')' if tile.get('hint') else ''}" for tile in payload["tiles"])
    if kind == "series":
        return "\n".join(f"- {line['label']}: " + ", ".join(f"{at} {value}" for at, value in line["points"][-12:]) for line in payload["lines"])
    if kind == "percentiles":
        lines = ["| | n | p50 | p95 | p99 | max | budget |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {dist['label']} | {dist['n']} | " + " | ".join(cell_text(dist.get(name)) for name in ("p50", "p95", "p99", "max", "budget")) + " |" for dist in payload["dists"]]
        return "\n".join(lines)
    if kind == "graph":
        labels = {node["id"]: node["label"] for node in payload["nodes"]}
        return "\n".join(f"- {labels[a]} → {labels[b]}" for a, b in payload["edges"]) or "\n".join(f"- {label}" for label in labels.values())
    if kind == "feed":
        return "\n".join(f"- {entry['at']} {entry['actor']}: {entry['text']}" + (f" ({entry['latency_ms'] / 1000:.1f}s)" if entry.get("latency_ms") is not None else "") + (f" [{entry['cite']}]" if entry.get("cite") else "") for entry in payload["entries"])
    if kind == "kv":
        return "\n".join(f"- {label}: {value}" for label, value in payload["pairs"])
    if kind == "markdown":
        return payload["text"]
    return "(an SVG drawing)"
