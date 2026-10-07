from __future__ import annotations

from datetime import datetime, timezone

from livedash import Checklist, Context, Dist, Gate, Line, Percentiles, Series, component, view
from livedash.components.files import number, read_rows

FALSE = frozenset({"", "0", "false", "False", "no", "none", "None"})
MONITOR_STATUS = {"OK": "pass", "Alert": "fail", "Warn": "blocked", "No Data": "not-run", "Skipped": "waived", "Ignored": "waived"}
UNGROUPED = "all"


def mtime(path) -> str:
    return view.iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))


def failed(row: dict, field: str | None) -> bool:
    if not field:
        return False
    return (str(row.get(field.removeprefix("!"))) not in FALSE) != field.startswith("!")


def pup(ctx: Context, argv: list[str]):
    return ctx.json(["pup", *argv, "--no-agent"])


def samples_from_files(ctx: Context, pattern: str, value: str, group: str | None, failure: str | None, rows_key: str | None):
    samples: dict[str, list[float]] = {}
    failures: dict[str, int] = {}
    history: dict[str, list[list]] = {}
    for path in sorted(ctx.glob(pattern), key=lambda path: path.stat().st_mtime):
        run: dict[str, list[float]] = {}
        for row in read_rows(path, rows_key):
            name = str(row.get(group)) if group else UNGROUPED
            if failed(row, failure):
                failures[name] = failures.get(name, 0) + 1
            elif (sample := number(row.get(value))) is not None:
                run.setdefault(name, []).append(sample)
        for name, found in run.items():
            samples.setdefault(name, []).extend(found)
            history.setdefault(name, []).append([mtime(path), Dist.of(name, found).p95])
    return samples, failures, history


def samples_from_deltas(ctx: Context, pattern: str, origin: str, stages: list[str], rows_key: str | None):
    samples: dict[str, list[float]] = {stage: [] for stage in stages}
    history: dict[str, list[list]] = {stage: [] for stage in stages}
    for path in sorted(ctx.glob(pattern), key=lambda path: path.stat().st_mtime):
        entries = read_rows(path, rows_key or "entries")
        start = next((entry for entry in entries if entry.get("id") == origin), None)
        if start is None:
            continue
        for stage in stages:
            hit = next((entry for entry in entries if entry.get("id") == stage or str(entry.get("id", "")).startswith(stage + ":")), None)
            if hit is not None:
                delta = (view.stamp(hit["ts"]) - view.stamp(start["ts"])).total_seconds() * 1000
                samples[stage].append(delta)
                history[stage].append([start["ts"], delta])
    return samples, {}, history


def samples_from_query(ctx: Context, query: str, window: str):
    data = pup(ctx, ["metrics", "query", "--query", query, "--from", window])
    if data.get("status") == "error":
        raise ValueError(f"Datadog rejected the query: {data.get('error')}")
    samples: dict[str, list[float]] = {}
    history: dict[str, list[list]] = {}
    for series in data.get("series") or []:
        name = series.get("scope") or series["metric"]
        points = [[view.iso(datetime.fromtimestamp(at / 1000, timezone.utc)), value] for at, value in series["pointlist"] if value is not None]
        samples[name] = [value for _, value in points]
        history[name] = points
    return samples, {}, history


@component("latency-percentiles", "Latency percentiles", every="5m", timeout="60s")
def latency_percentiles(
    ctx: Context,
    *,
    files: str | None = None,
    value: str = "wall_ms",
    group: str | None = None,
    failure: str | None = None,
    rows_key: str | None = None,
    deltas_from: str | None = None,
    deltas_to: list[str] = [],
    query: str | None = None,
    window: str = "1h",
    unit: str = "ms",
    budget_ms: float | None = None,
    budgets: dict[str, str] = {},
    groups: list[str] = [],
) -> Percentiles:
    """p50, p95, p99 and max per group, with a p95 history. Three sources: `files` rows (json, jsonl, csv, tsv) with a
    `value` sample, a `group` field and a `failure` flag counted apart (`!success` counts a false `success`); `deltas_from` plus `deltas_to`, the time from one
    timeline entry to each later stage across every file matching `files`; or a Datadog `query` through pup. Every named
    `groups` entry shows as not run until it has samples, and `budgets` overrides `budget_ms` per group."""
    if query:
        samples, failures, history = samples_from_query(ctx, query, window)
    elif files and deltas_from:
        samples, failures, history = samples_from_deltas(ctx, files, deltas_from, deltas_to, rows_key)
    elif files:
        samples, failures, history = samples_from_files(ctx, files, value, group, failure, rows_key)
    else:
        raise ValueError("latency-percentiles needs files, files with deltas_from, or query")
    names = [*groups, *sorted((set(samples) | set(failures)) - set(groups))]
    dists = [Dist.of(name, samples.get(name, []), unit, float(budgets[name]) if name in budgets else budget_ms, failures.get(name)) for name in names]
    lines = [Line(name, points) for name, points in history.items() if points]
    thresholds = {"budget": budget_ms} if budget_ms is not None else {}
    empty = not any(dist.n for dist in dists)
    note = ("Not run yet: nothing matches " + (files or query or "")) if empty else None
    return Percentiles(dists, Series(lines, unit, thresholds) if lines else None, note=note)


@component("timeseries", "Time series", every="1m", timeout="60s")
def timeseries(ctx: Context, *, query: str | None = None, window: str = "1h", files: str | None = None, x: str = "at", y: list[str] = [], rows_key: str | None = None, unit: str | None = None, thresholds: dict[str, str] = {}) -> Series:
    """Lines from a Datadog `query` through pup, one per series, or from the `y` fields of every row in the files matching
    `files`, plotted against `x`; `thresholds` draws labeled rules."""
    rules = {label: float(level) for label, level in thresholds.items()}
    if query:
        _, _, history = samples_from_query(ctx, query, window)
        return Series([Line(name, points) for name, points in history.items()], unit, rules, note=None if history else "No points in the window.")
    if not files:
        raise ValueError("timeseries needs query or files")
    lines: dict[str, list[list]] = {field: [] for field in y}
    for path in sorted(ctx.glob(files), key=lambda path: path.stat().st_mtime):
        for row in read_rows(path, rows_key):
            for field in y:
                if (sample := number(row.get(field))) is not None:
                    lines[field].append([row.get(x), sample])
    return Series([Line(field, sorted(points, key=lambda point: str(point[0]))) for field, points in lines.items()], unit, rules, note=None if any(lines.values()) else f"Nothing matches {files} yet.")


@component("datadog-monitors", "Datadog monitors", every="1m", timeout="60s")
def datadog_monitors(ctx: Context, *, query: str, expect: list[str] = []) -> Checklist:
    """Monitors matching a Datadog monitor search `query` through pup, as gates: OK passes, Alert fails, Warn blocks, No Data
    has not run. Each name in `expect` that matches no monitor shows as not created."""
    monitors = pup(ctx, ["monitors", "search", "--query", query, "--per-page", "100"]).get("monitors") or []
    gates = [Gate(str(monitor["id"]), monitor["name"], MONITOR_STATUS.get(monitor.get("status"), "open"), link=f"https://app.datadoghq.com/monitors/{monitor['id']}") for monitor in monitors]
    names = {monitor["name"] for monitor in monitors}
    gates += [Gate(name, name, "not-created", blocker=f"no monitor matches {query}") for name in expect if not any(name in found for found in names)]
    return Checklist(gates, note=None if gates else f"No monitor matches {query} yet.")
