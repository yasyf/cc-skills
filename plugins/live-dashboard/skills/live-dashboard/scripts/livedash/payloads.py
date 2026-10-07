from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import ddshared

TONES = ("ok", "warn", "bad", "muted")
COL_KINDS = ("text", "num", "age", "due", "link", "badge", "delta", "bar")
LINE_STYLES = ("line", "area", "bar")
GATE_STATUSES = ("open", "pass", "fail", "blocked", "waived", "not-created", "not-run")
MAX_BYTES = 256 * 1024
SCHEMAS = Path(__file__).resolve().parents[2] / "reference" / "components"
ROW_LISTS = ("rows", "entries")


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
    """One number: `gauge` (0 to 1) draws a ring, `trend` is a short delta such as "▲ 4 in 24h" toned by `trend_tone`,
    `foot` is a line under the tile, and `link` opens a page or, as `#card-<id>`, jumps to a card."""

    label: str
    value: str | int | float | None
    unit: str | None = None
    tone: str | None = None
    hint: str | None = None
    link: str | None = None
    trend: str | None = None
    trend_tone: str | None = None
    gauge: float | None = None
    foot: str | None = None

    def __post_init__(self) -> None:
        tone_of(self.tone)
        tone_of(self.trend_tone)
        if self.gauge is not None and not 0 <= self.gauge <= 1:
            raise PayloadError(f"tile {self.label} gauge must be between 0 and 1, not {self.gauge}")


@dataclass(frozen=True)
class Tiles:
    tiles: list[Tile]
    note: str | None = None

    kind = "tiles"

    def json(self) -> dict:
        return dropped({"kind": self.kind, "note": self.note}) | {"tiles": [dropped(asdict(tile)) | {"key": tile.label, "value": tile.value} for tile in self.tiles]}

    @classmethod
    def example(cls) -> Tiles:
        return cls(tiles=[Tile("Stacks at 0/0", 230, "/296", "warn", gauge=230 / 296, trend="▲ 4 in 24h", trend_tone="ok", foot="66 drift"), Tile("p95 lookup", 182, "ms", "ok")])


@dataclass(frozen=True)
class Line:
    """`[at, value]` points, or `[at, value, {"tone", "link", "label"}]` to tone, link or name one point; `style` draws a
    line, a filled area, or bars."""

    label: str
    points: list[list]
    style: str = "line"

    def __post_init__(self) -> None:
        if self.style not in LINE_STYLES:
            raise PayloadError(f"line {self.label} has style {self.style!r}; use one of {', '.join(LINE_STYLES)}")
        for point in self.points:
            if len(point) == 3:
                tone_of(point[2].get("tone"))


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
class Span:
    label: str
    start: str
    end: str | None = None
    tone: str | None = None

    def __post_init__(self) -> None:
        tone_of(self.tone)


@dataclass(frozen=True)
class Track:
    label: str
    spans: list[Span]
    link: str | None = None
    tone: str | None = None
    note: str | None = None
    key: str | None = None
    cite: str | None = None

    def __post_init__(self) -> None:
        tone_of(self.tone)


@dataclass(frozen=True)
class Timeline:
    """A Gantt: one track per item, each a row of labelled spans from `start` to `end`; a span with no `end` is still
    running and reaches now."""

    tracks: list[Track]
    note: str | None = None

    kind = "timeline"

    def json(self) -> dict:
        return dropped({"kind": self.kind, "note": self.note}) | {"tracks": [dropped(asdict(track) | {"spans": [dropped(asdict(span)) for span in track.spans]}) for track in self.tracks]}

    @classmethod
    def example(cls) -> Timeline:
        return cls([Track("#2960 Release infra", [Span("Plan", "2026-10-07T08:36:49Z", "2026-10-07T08:39:28Z", "ok"), Span("Deploy", "2026-10-07T08:43:33Z", None, "warn")], "https://buildkite.com/o/release/builds/2960", note="12 of 31 steps")])


@dataclass(frozen=True)
class Heat:
    tone: str | None = None
    title: str | None = None
    link: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        tone_of(self.tone)


@dataclass(frozen=True)
class Heatmap:
    """A dense grid of toned squares: row labels against column labels, `None` where a row has no such column; `groups`
    names each row's group, `legend` labels each tone, and a square's `text` is drawn inside it."""

    rows: list[str]
    cols: list[str]
    cells: list[list[Heat | None]]
    groups: list[str] | None = None
    legend: dict[str, str] = field(default_factory=dict)
    note: str | None = None

    kind = "heatmap"

    def __post_init__(self) -> None:
        if len(self.cells) != len(self.rows) or any(len(row) != len(self.cols) for row in self.cells):
            raise PayloadError(f"a heatmap needs one row of {len(self.cols)} cells per row label, {len(self.rows)} rows")
        if self.groups is not None and len(self.groups) != len(self.rows):
            raise PayloadError("a heatmap's groups must name one group per row")
        for tone in self.legend:
            tone_of(tone)

    def json(self) -> dict:
        return dropped({"kind": self.kind, "groups": self.groups, "legend": self.legend, "note": self.note}) | {"rows": self.rows, "cols": self.cols, "cells": [[dropped(asdict(cell)) if cell else None for cell in row] for row in self.cells]}

    @classmethod
    def example(cls) -> Heatmap:
        return cls(["api", "network"], ["plat", "tnt-a"], [[Heat("ok", "api/plat: 0/0"), Heat("warn", "api/tnt-a: drift")], [Heat("ok"), None]], ["platform", "infra"], {"ok": "0/0", "warn": "drift"})


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


def unique(cards: list[dict]) -> list[dict]:
    seen: dict[str, str] = {}
    trimmed: dict[str, dict] = {}
    for card in sorted(cards, key=lambda card: not card["pinned"]):
        payload = card["payload"] or {}
        name = next((name for name in ROW_LISTS if name in payload), None)
        if name is None or not all(isinstance(row, dict) for row in payload[name]):
            continue
        kept, moved = [], {}
        for row in payload[name]:
            cite = row.get("cite")
            if cite in seen:
                moved[seen[cite]] = moved.get(seen[cite], 0) + 1
                continue
            kept.append(row)
            if cite:
                seen[cite] = card["title"]
        if moved:
            said = "; ".join(f"{count} more under {title}" for title, count in moved.items())
            trimmed[card["id"]] = payload | {name: kept, "note": f"{payload['note']} {said}" if payload.get("note") else said}
    return [card | {"payload": trimmed[card["id"]]} if card["id"] in trimmed else card for card in cards]


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
        return "\n".join(f"- {tile['label']}: {tile['value']}{' ' + tile['unit'] if tile.get('unit') else ''}" + "".join(f" ({tile[name]})" for name in ("hint", "trend", "foot") if tile.get(name)) for tile in payload["tiles"])
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
    if kind == "timeline":
        return "\n".join(f"- {track['label']}: " + ", ".join(f"{span['label']} {span['start']}–{span.get('end') or 'now'}" for span in track["spans"]) + (f" ({track['note']})" if track.get("note") else "") for track in payload["tracks"])
    if kind == "heatmap":
        legend = [f"- {label}" for label in payload.get("legend", {}).values()]
        return "\n".join(legend + [f"- {label}: " + ", ".join(cell.get("title") or f"{col} {cell.get('tone')}" for col, cell in zip(payload["cols"], row, strict=True) if cell) for label, row in zip(payload["rows"], payload["cells"], strict=True)])
    if kind == "markdown":
        return payload["text"]
    return "(an SVG drawing)"
