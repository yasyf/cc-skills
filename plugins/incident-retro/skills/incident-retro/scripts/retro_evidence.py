#!/usr/bin/env python3
"""Evidence snapshots for the incident-retro skill.

  retro.py evidence fetch <dir> [--notebook ID|URL]… [--monitor ID|URL]… [--from-ssm …] [--dry-run]
  retro.py evidence slack check <dir>
  retro.py evidence slack new --permalink URL --channel-name NAME [--thread]

fetch snapshots Datadog notebooks and monitors into evidence/datadog/ as
ir.notebook/1 and ir.monitor/1 files and registers them in retro.json.
slack check validates the ir.slack/1 files the authoring agent wrote from
its Slack tooling; slack new prints the skeleton for one. The page never
calls Datadog or Slack: it renders these files. Stdlib only.
"""
import argparse, datetime, json, math, os, re, subprocess, sys, urllib.error, urllib.request
from pathlib import Path

SLACK_PERMALINK = re.compile(r"https://([a-z0-9-]+)\.slack\.com/archives/([A-Z0-9]+)/p(\d{16})(?:\?thread_ts=(\d+\.\d+)(?:&cid=[A-Z0-9]+)?)?")
SLACK_TS = re.compile(r"^\d+\.\d+$")
SLACK_CHANNEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SLACK_MESSAGE_KEYS = ("ts", "user_id", "user_name", "datetime", "text", "reactions", "files")
SLACK_MAX_MESSAGES = 50
NOTEBOOK_URL = re.compile(r"^https://app\.([a-z0-9.-]+)/notebook/(\d+)")
MONITOR_URL = re.compile(r"^https://app\.([a-z0-9.-]+)/monitors/(\d+)")
LIVE_SPAN = re.compile(r"^(\d+)(mo|m|h|d|w)$")
LIVE_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800, "mo": 2592000}
SCALAR_TYPES = ("toplist", "query_table")
MAX_POINTS = 1500
MESSAGE_CHARS = 500
EVENTS_PAGE = 500
EVENT_PAD = datetime.timedelta(hours=1)
EVENT_FALLBACK = datetime.timedelta(days=7)
LOG_FIELDS = ("timestamp", "status", "service", "host", "message")
MONITOR_ROLE_ADDED = "added"


def parse_permalink(url: str) -> dict:
    m = SLACK_PERMALINK.match(url)
    if not m:
        raise SystemExit(f"{url} is not a Slack message permalink")
    workspace, channel_id, raw, thread_ts = m.groups()
    return {"workspace": workspace, "channel_id": channel_id, "ts": f"{raw[:10]}.{raw[10:]}", "thread_ts": thread_ts}


def parse_ts(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise SystemExit(f"{s} has no UTC offset")
    return dt


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def stamp(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms(dt: datetime.datetime) -> int:
    return int(dt.timestamp() * 1000)


def resolve_window(time, fetched_at: datetime.datetime):
    if time is None:
        return None
    if "live_span" in time:
        m = LIVE_SPAN.match(time["live_span"])
        if not m:
            raise SystemExit(f"cannot resolve live span {time['live_span']!r}; set the notebook to an absolute window in Datadog")
        span = datetime.timedelta(seconds=int(m.group(1)) * LIVE_UNITS[m.group(2)])
        return {"start": stamp(fetched_at - span), "end": stamp(fetched_at), "live": True}
    return {"start": parse_ts(time["start"]).isoformat(), "end": parse_ts(time["end"]).isoformat(), "live": bool(time.get("live"))}


def forbidden_terms(flag, start: Path):
    if flag:
        return re.compile(flag, re.IGNORECASE)
    env = os.environ.get("FORBIDDEN_TERMS")
    if env:
        return re.compile(env, re.IGNORECASE)
    start = start.resolve()
    for parent in (start, *start.parents):
        names = parent / ".customer-names"
        if names.exists():
            terms = [l.strip() for l in names.read_text().splitlines() if l.strip() and not l.startswith("#")]
            return re.compile("|".join(terms), re.IGNORECASE)
    return None


def load_retro(root: Path) -> dict:
    path = root / "retro.json"
    if not path.exists():
        raise SystemExit(f"{path} not found; run scaffold first")
    return json.loads(path.read_text())


def save_retro(root: Path, retro: dict):
    (root / "retro.json").write_text(json.dumps(retro, indent=2, ensure_ascii=False) + "\n")


class Datadog:
    def __init__(self, site: str, api_key: str, app_key: str):
        self.site, self.api_key, self.app_key = site, api_key, app_key

    def request(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"https://api.{self.site}{path}", data=data, method=method, headers={
            "DD-API-KEY": self.api_key, "DD-APPLICATION-KEY": self.app_key, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise SystemExit(f"datadog {method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:400]}")

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, body: dict):
        return self.request("POST", path, body)


def ssm_parameter(name: str, args) -> str:
    cmd = ["aws", "ssm", "get-parameter", "--with-decryption", "--name", name, "--query", "Parameter.Value", "--output", "text"]
    if args.aws_region:
        cmd += ["--region", args.aws_region]
    if args.aws_profile:
        cmd += ["--profile", args.aws_profile]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(p.stderr.strip() or f"aws ssm get-parameter {name} failed")
    return p.stdout.strip()


def datadog_keys(args):
    if args.from_ssm:
        if not (args.ssm_api_key_path and args.ssm_app_key_path):
            raise SystemExit("--from-ssm needs --ssm-api-key-path and --ssm-app-key-path")
        return ssm_parameter(args.ssm_api_key_path, args), ssm_parameter(args.ssm_app_key_path, args)
    api, app = os.environ.get("DD_API_KEY"), os.environ.get("DD_APP_KEY")
    if not (api and app):
        raise SystemExit("set DD_API_KEY and DD_APP_KEY, or pass --from-ssm with --ssm-api-key-path and --ssm-app-key-path")
    return api, app


def resource_id(value: str, pattern: re.Pattern, noun: str) -> int:
    if value.isdigit():
        return int(value)
    m = pattern.match(value)
    if not m:
        raise SystemExit(f"{value} is neither a {noun} id nor an app.<site>/{noun} URL")
    return int(m.group(2))


def normalise_request(r: dict, scalar: bool) -> dict:
    if "q" in r:
        queries = [{"data_source": "metrics", "name": "query1", "query": r["q"]}]
        formulas = [{"formula": "query1", "alias": None}]
    else:
        queries = r["queries"]
        formulas = [{"formula": f["formula"], "alias": f.get("alias"), **({"limit": f["limit"]} if "limit" in f else {})}
                    for f in (r.get("formulas") or [{"formula": q["name"]} for q in queries])]
    if scalar:
        queries = [{**q, "aggregator": q.get("aggregator", "avg")} for q in queries]
    return {"queries": queries, "formulas": formulas, "displayType": r.get("display_type", "line")}


def cell_skeleton(index: int, cell: dict, fetched_at: datetime.datetime) -> dict:
    at = cell["attributes"]
    d = at["definition"]
    kind = d["type"]
    out = {"index": index, "type": kind}
    if kind == "markdown":
        out["text"] = d["text"]
        return out
    out.update(title=d.get("title"), graphSize=at.get("graph_size"), time=resolve_window(at.get("time"), fetched_at))
    if kind == "timeseries" or kind in SCALAR_TYPES:
        out["requests"] = [normalise_request(r, kind in SCALAR_TYPES) for r in d["requests"]]
    elif kind == "log_stream":
        out.update(query=d.get("query", ""), indexes=d.get("indexes") or [], columns=d.get("columns") or [])
    else:
        out.update(definition=d, status="unrendered", reason=f"{kind} cells have no snapshot query; open in Datadog")
    return out


def unit_name(unit):
    if not unit or not unit[0]:
        return None
    return unit[0].get("short_name") or unit[0].get("name")


def series_label(formula: dict, tags: list, formulas: int) -> str:
    head = formula["alias"] or (formula["formula"] if formulas > 1 or not tags else None)
    return " ".join(p for p in (head, ",".join(tags)) if p) or formula["formula"]


def query_timeseries(dd: Datadog, request: dict, start, end, interval):
    attrs = {"from": ms(start), "to": ms(end), "queries": request["queries"], "formulas": [{"formula": f["formula"]} for f in request["formulas"]]}
    if interval:
        attrs["interval"] = interval * 1000
    a = dd.post("/api/v2/query/timeseries", {"data": {"type": "timeseries_request", "attributes": attrs}})["data"]["attributes"]
    if len(a["times"]) > MAX_POINTS and not interval:
        return query_timeseries(dd, request, start, end, math.ceil((end - start).total_seconds() / MAX_POINTS))
    return a


def fetch_timeseries(dd: Datadog, cell: dict, window: dict, interval) -> dict:
    start, end = parse_ts(window["start"]), parse_ts(window["end"])
    times, series = None, []
    for ri, request in enumerate(cell["requests"]):
        a = query_timeseries(dd, request, start, end, interval)
        if times is None:
            times = a["times"]
            if len(times) > 1 and not interval:
                interval = (times[1] - times[0]) // 1000
        elif a["times"] != times:
            raise SystemExit(f"cell {cell['index']}: request {ri} returned a different time axis than request 0; pass --interval")
        for s, v in zip(a["series"], a["values"], strict=True):
            formula = request["formulas"][s["query_index"]]
            series.append({"request": ri, "formula": s["query_index"], "label": series_label(formula, s["group_tags"], len(request["formulas"])),
                           "tags": s["group_tags"], "unit": unit_name(s["unit"]), "v": v})
    return {"interval": interval, "t": [t // 1000 for t in times], "series": series}


def fetch_scalar(dd: Datadog, cell: dict, window: dict) -> dict:
    start, end = parse_ts(window["start"]), parse_ts(window["end"])
    rows = []
    for ri, request in enumerate(cell["requests"]):
        attrs = {"from": ms(start), "to": ms(end), "queries": request["queries"],
                 "formulas": [{k: v for k, v in f.items() if k in ("formula", "limit")} for f in request["formulas"]]}
        columns = dd.post("/api/v2/query/scalar", {"data": {"type": "scalar_request", "attributes": attrs}})["data"]["attributes"]["columns"]
        groups = [c for c in columns if c["type"] == "group"]
        numbers = [c for c in columns if c["type"] == "number"]
        for fi, col in enumerate(numbers):
            formula = request["formulas"][fi]
            for i, value in enumerate(col["values"]):
                tags = [f"{g['name']}:{x}" for g in groups for x in g["values"][i]]
                rows.append({"request": ri, "formula": fi, "label": series_label(formula, tags, len(numbers)), "tags": tags,
                             "unit": unit_name((col.get("meta") or {}).get("unit")), "value": value})
    return {"rows": rows}


def column_value(a: dict, column: str):
    node = a.get("attributes") or {}
    for part in column.lstrip("@").split("."):
        if not isinstance(node, dict) or part not in node:
            values = [t.split(":", 1)[1] for t in a.get("tags") or [] if t.startswith(f"{column}:")]
            return values[0] if len(values) == 1 else values or None
        node = node[part]
    return node


def fetch_logs(dd: Datadog, cell: dict, window: dict, limit: int) -> dict:
    flt = {"query": cell["query"], "from": window["start"], "to": window["end"]}
    if cell["indexes"]:
        flt["indexes"] = cell["indexes"]
    res = dd.post("/api/v2/logs/events/search", {"filter": flt, "page": {"limit": limit}, "sort": "timestamp"})
    extra = [c for c in cell["columns"] if c not in LOG_FIELDS]
    logs = []
    for e in res.get("data", []):
        a = e["attributes"]
        picked = {c: column_value(a, c) for c in extra}
        logs.append({"ts": a["timestamp"], "status": a.get("status"), "service": a.get("service"), "host": a.get("host"),
                     "message": (a.get("message") or "")[:MESSAGE_CHARS], "attrs": {k: v for k, v in picked.items() if v is not None}})
    return {"logs": logs, "truncated": bool((res.get("meta") or {}).get("page", {}).get("after")), "limit": limit}


def cell_summary(cell: dict) -> str:
    head = f"cell {cell['index']} {cell['type']}"
    if cell["type"] == "markdown":
        return f"{head}: {len(cell['text'])} chars"
    if cell.get("status") == "unrendered":
        return f"{head}: unrendered"
    data = cell["data"]
    if cell["type"] == "timeseries":
        return f"{head}: {len(data['series'])} series, {len(data['t'])} points, interval {data['interval']}s"
    if cell["type"] == "log_stream":
        return f"{head}: {len(data['logs'])} logs" + (" (truncated)" if data["truncated"] else "")
    return f"{head}: {len(data['rows'])} rows"


def cell_plan(cell: dict, window: dict, interval) -> str:
    head = f"cell {cell['index']} {cell['type']}"
    if cell["type"] == "markdown":
        return f"{head}: {len(cell['text'])} chars"
    if cell.get("status") == "unrendered":
        return f"{head}: unrendered ({cell['reason']})"
    span = f"window {window['start']} → {window['end']}"
    if cell["type"] == "log_stream":
        return f"{head}: {span}\n    {cell['query']}"
    queries = "\n".join(f"    {q['query'] if 'query' in q else json.dumps(q)}" for r in cell["requests"] for q in r["queries"])
    step = f", interval {interval}s" if interval and cell["type"] == "timeseries" else (", interval auto" if cell["type"] == "timeseries" else "")
    return f"{head}: {sum(len(r['queries']) for r in cell['requests'])} queries, {span}{step}\n{queries}"


def fetch_notebook(dd: Datadog, nid: int, args, fetched_at: datetime.datetime, dry_run: bool) -> dict:
    nb = dd.get(f"/api/v1/notebooks/{nid}")["data"]["attributes"]
    window = resolve_window(nb["time"], fetched_at)
    snap = {"schema": "ir.notebook/1", "id": nid, "url": f"https://app.{dd.site}/notebook/{nid}", "site": dd.site, "fetchedAt": stamp(fetched_at),
            "name": nb["name"], "author": nb["author"]["name"], "modified": nb["modified"], "type": nb["metadata"]["type"], "time": window,
            "cells": [], "warnings": []}
    if window["live"]:
        snap["warnings"].append("the notebook uses a live span; its window was resolved at fetch time and may not cover the incident")
    print(f"notebook {nid}: {nb['name']} ({len(nb['cells'])} cells, {window['start']} → {window['end']})")
    for index, raw in enumerate(nb["cells"]):
        cell = cell_skeleton(index, raw, fetched_at)
        cw = cell.get("time") or window
        if dry_run:
            print("  " + cell_plan(cell, cw, args.interval))
        elif cell["type"] == "timeseries":
            cell["data"], cell["status"] = fetch_timeseries(dd, cell, cw, args.interval), "rendered"
        elif cell["type"] in SCALAR_TYPES:
            cell["data"], cell["status"] = fetch_scalar(dd, cell, cw), "rendered"
        elif cell["type"] == "log_stream":
            cell["data"], cell["status"] = fetch_logs(dd, cell, cw, args.logs_limit), "rendered"
        if not dry_run:
            print("  " + cell_summary(cell))
        snap["cells"].append(cell)
    return snap


def event_window(retro: dict):
    ts = retro.get("timestamps") or {}
    onset, close = ts.get("onset"), ts.get("allClear") or ts.get("resolved")
    if onset and close:
        return parse_ts(onset) - EVENT_PAD, parse_ts(close) + EVENT_PAD, None
    now = utc_now()
    return now - EVENT_FALLBACK, now + EVENT_FALLBACK, "retro.json has no onset and resolved timestamps; monitor events cover ±7 days from now"


def fetch_events(dd: Datadog, mid: int, start, end) -> list:
    events, cursor = [], None
    while True:
        page = {"limit": EVENTS_PAGE, **({"cursor": cursor} if cursor else {})}
        res = dd.post("/api/v2/events/search", {"filter": {"query": f"source:alert @monitor_id:{mid}", "from": start.isoformat(), "to": end.isoformat()},
                                                "page": page, "sort": "timestamp"})
        for e in res.get("data", []):
            a = e["attributes"]
            at = a["attributes"]
            events.append({"ts": a["timestamp"], "transition": at["monitor"]["transition"]["destination_state"].lower(),
                           "group": ", ".join(at.get("monitor_groups") or []), "title": at["title"],
                           "url": f"https://app.{dd.site}/event/event?id={at['evt']['id']}"})
        cursor = (res.get("meta") or {}).get("page", {}).get("after")
        if not cursor:
            return events


def fetch_monitor(dd: Datadog, mid: int, retro: dict, fetched_at: datetime.datetime, dry_run: bool) -> dict:
    start, end, warning = event_window(retro)
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
    if dry_run:
        print(f"monitor {mid}: GET /api/v1/monitor/{mid}; events source:alert @monitor_id:{mid}, window {start.isoformat()} → {end.isoformat()}")
        return {}
    m = dd.get(f"/api/v1/monitor/{mid}")
    o = m["options"]
    th = o.get("thresholds") or {}
    events = fetch_events(dd, mid, start, end)
    print(f"monitor {mid}: {m['name']} ({m['type']}, {m['overall_state']}), {len(events)} events {start.isoformat()} → {end.isoformat()}")
    return {"schema": "ir.monitor/1", "id": mid, "url": f"https://app.{dd.site}/monitors/{mid}", "site": dd.site, "fetchedAt": stamp(fetched_at),
            "name": m["name"], "type": m["type"], "query": m["query"], "message": m["message"], "tags": m["tags"], "created": m["created"],
            "modified": m["modified"], "overallState": m["overall_state"],
            "thresholds": {"critical": th.get("critical"), "warning": th.get("warning"), "criticalRecovery": th.get("critical_recovery"),
                           "warningRecovery": th.get("warning_recovery")},
            "options": {"evaluationDelay": o.get("evaluation_delay"), "notifyNoData": o.get("notify_no_data"), "noDataTimeframe": o.get("no_data_timeframe"),
                        "renotifyInterval": o.get("renotify_interval"), "requireFullWindow": o.get("require_full_window")},
            "events": events}


def first_transition(events: list, state: str, after=None):
    for e in events:
        if e["transition"] == state and (after is None or e["ts"] > after):
            return e["ts"]
    return None


def detection_entry(snap: dict, retro: dict, file: str) -> dict:
    fired = first_transition(snap["events"], "alert")
    onset = (retro.get("timestamps") or {}).get("onset")
    if onset and parse_ts(snap["created"]) > parse_ts(onset):
        role = MONITOR_ROLE_ADDED
    else:
        role = "caught" if fired else "missed"
    return {"id": snap["id"], "file": file, "role": role, "fired": fired, "recovered": first_transition(snap["events"], "ok", fired) if fired else None}


def register(entries: list, entry: dict):
    for i, e in enumerate(entries):
        if e.get("id") == entry["id"]:
            entries[i] = {**e, **entry}
            return
    entries.append(entry)


def screen(snap: dict, pattern, label: str, allow: bool):
    if pattern is None:
        print(f"WARNING: no forbidden-terms source configured; {label} was not screened", file=sys.stderr)
        return
    hits = sorted({m.group(0) for m in pattern.finditer(json.dumps(snap, ensure_ascii=False))})
    if hits and not allow:
        raise SystemExit(f"{label}: forbidden terms in the payload ({', '.join(hits)}); nothing written. Pass --allow-terms to write anyway")
    if hits:
        print(f"WARNING: {label} carries forbidden terms ({', '.join(hits)}); written because of --allow-terms", file=sys.stderr)


def write_snapshot(root: Path, rel: str, snap: dict):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False) + "\n")
    print(f"wrote {rel}")


def fetch(args) -> int:
    root = Path(args.dir)
    if not (args.notebook or args.monitor):
        raise SystemExit("pass at least one --notebook or --monitor")
    retro = load_retro(root)
    pattern = forbidden_terms(args.forbidden_terms, root)
    api_key, app_key = datadog_keys(args)
    dd = Datadog(args.site, api_key, app_key)
    fetched_at = utc_now()
    for value in args.notebook or []:
        nid = resource_id(value, NOTEBOOK_URL, "notebook")
        snap = fetch_notebook(dd, nid, args, fetched_at, args.dry_run)
        if args.dry_run:
            continue
        rel = f"evidence/datadog/notebook-{nid}.json"
        screen(snap, pattern, f"notebook {nid}", args.allow_terms)
        write_snapshot(root, rel, snap)
        if not args.no_register:
            register(retro["evidence"].setdefault("notebooks", []), {"id": nid, "url": snap["url"], "file": rel})
    for value in args.monitor or []:
        mid = resource_id(value, MONITOR_URL, "monitor")
        snap = fetch_monitor(dd, mid, retro, fetched_at, args.dry_run)
        if args.dry_run:
            continue
        rel = f"evidence/datadog/monitor-{mid}.json"
        screen(snap, pattern, f"monitor {mid}", args.allow_terms)
        write_snapshot(root, rel, snap)
        if not args.no_register:
            register(retro["evidence"].setdefault("monitors", []), {"id": mid, "url": snap["url"], "file": rel})
            detection = retro.setdefault("detection", {}).setdefault("monitors", [])
            if not any(m.get("id") == mid for m in detection):
                detection.append(detection_entry(snap, retro, rel))
            else:
                register(detection, {"id": mid, "file": rel})
    if not (args.dry_run or args.no_register):
        save_retro(root, retro)
        print("registered in retro.json")
    return 0


def slack_file_issues(path: Path, data) -> list:
    issues = []
    if not isinstance(data, dict):
        return [f"{path}: not a JSON object"]
    if data.get("schema") != "ir.slack/1":
        issues.append(f"{path}: schema must be ir.slack/1")
    link = SLACK_PERMALINK.match(str(data.get("permalink", "")))
    if not link:
        issues.append(f"{path}: permalink is not a Slack message permalink")
    else:
        p = parse_permalink(data["permalink"])
        if data.get("channel_id") != p["channel_id"]:
            issues.append(f"{path}: channel_id {data.get('channel_id')!r} does not match the permalink's {p['channel_id']}")
        if data.get("ts") != p["ts"]:
            issues.append(f"{path}: ts {data.get('ts')!r} does not match the permalink's {p['ts']}")
        if p["thread_ts"] and data.get("thread_ts") != p["thread_ts"]:
            issues.append(f"{path}: thread_ts {data.get('thread_ts')!r} does not match the permalink's {p['thread_ts']}")
    name = data.get("channel_name")
    if not (isinstance(name, str) and SLACK_CHANNEL_NAME.match(name)):
        issues.append(f"{path}: channel_name must be a Slack channel name without '#'")
    elif isinstance(data.get("ts"), str) and path.name != f"{name}-{data['ts']}.json":
        issues.append(f"{path}: file name must be {name}-{data['ts']}.json")
    thread_ts = data.get("thread_ts")
    if thread_ts is not None and not (isinstance(thread_ts, str) and SLACK_TS.match(thread_ts)):
        issues.append(f"{path}: thread_ts must be null or a Slack ts")
    for key in ("fetchedAt",):
        try:
            parse_ts(str(data.get(key)))
        except (ValueError, SystemExit):
            issues.append(f"{path}: {key} must be an ISO 8601 timestamp with offset")
    messages = data.get("messages")
    if not (isinstance(messages, list) and messages):
        issues.append(f"{path}: messages must be a non-empty list")
        messages = []
    if len(messages) > SLACK_MAX_MESSAGES:
        issues.append(f"{path}: {len(messages)} messages; cap at {SLACK_MAX_MESSAGES} and set truncated: true")
    for i, m in enumerate(messages):
        where = f"{path}: messages[{i}]"
        if not isinstance(m, dict):
            issues.append(f"{where} is not an object")
            continue
        for key in SLACK_MESSAGE_KEYS:
            if key not in m:
                issues.append(f"{where} lacks {key}")
        if "ts" in m and not (isinstance(m["ts"], str) and SLACK_TS.match(m["ts"])):
            issues.append(f"{where}.ts must be a Slack ts")
        if not isinstance(m.get("user_id"), str):
            issues.append(f"{where}.user_id must be a string")
        if not (isinstance(m.get("user_name"), str) and m["user_name"].strip()):
            issues.append(f"{where}.user_name must be a non-empty string")
        try:
            parse_ts(str(m.get("datetime")))
        except (ValueError, SystemExit):
            issues.append(f"{where}.datetime must be an ISO 8601 timestamp with offset")
        if not isinstance(m.get("text"), str):
            issues.append(f"{where}.text must be a string")
        reactions = m.get("reactions")
        if not (isinstance(reactions, list) and all(isinstance(r, dict) and isinstance(r.get("name"), str) and isinstance(r.get("count"), int) for r in reactions)):
            issues.append(f"{where}.reactions must be a list of {{name, count}}")
        files = m.get("files")
        if not (isinstance(files, list) and all(isinstance(f, str) for f in files)):
            issues.append(f"{where}.files must be a list of strings")
    if messages and isinstance(messages[0], dict) and messages[0].get("ts") not in (data.get("ts"), thread_ts):
        issues.append(f"{path}: the first message must be the permalinked message or the thread root")
    if not isinstance(data.get("redactions"), list):
        issues.append(f"{path}: redactions must be a list")
    if "truncated" in data and not isinstance(data["truncated"], bool):
        issues.append(f"{path}: truncated must be a boolean")
    return issues


def read_json(path: Path):
    try:
        return json.loads(path.read_text()), None
    except json.JSONDecodeError as e:
        return None, f"{path}: invalid JSON ({e})"


def slack_snapshots(root: Path, retro: dict):
    paths = {root / e["file"] for e in retro.get("evidence", {}).get("slack", []) if isinstance(e.get("file"), str)}
    paths |= set((root / "evidence" / "slack").glob("*.json"))
    return sorted(p for p in paths if p.exists())


def slack_check(args) -> int:
    root = Path(args.dir)
    retro = load_retro(root)
    issues, count = [], 0
    for path in slack_snapshots(root, retro):
        data, err = read_json(path)
        issues += [err] if err else slack_file_issues(path.relative_to(root), data)
        count += 1
    for e in retro.get("evidence", {}).get("slack", []):
        if not (root / e.get("file", "")).exists():
            issues.append(f"retro.json evidence.slack: {e.get('file')!r} does not exist")
    for m in issues:
        print(f"ERROR: {m}")
    print(f"{count} slack snapshot(s), {len(issues)} issue(s)")
    return 1 if issues else 0


def slack_new(args) -> int:
    p = parse_permalink(args.permalink)
    thread_ts = p["thread_ts"] or (p["ts"] if args.thread else None)
    if not SLACK_CHANNEL_NAME.match(args.channel_name):
        raise SystemExit(f"{args.channel_name!r} is not a Slack channel name; drop the '#'")
    skeleton = {"schema": "ir.slack/1", "permalink": args.permalink, "channel_id": p["channel_id"], "channel_name": args.channel_name,
                "ts": p["ts"], "thread_ts": thread_ts, "fetchedAt": stamp(utc_now()),
                "messages": [{"ts": thread_ts if args.thread else p["ts"], "user_id": "", "user_name": "", "datetime": "", "text": "", "reactions": [], "files": []}],
                "redactions": []}
    print(json.dumps(skeleton, indent=2, ensure_ascii=False))
    print(f"write it to evidence/slack/{args.channel_name}-{p['ts']}.json", file=sys.stderr)
    return 0


def strings_in(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from strings_in(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings_in(v)


def slack_coverage(root: Path, retro: dict) -> set:
    covered = set()
    for path in slack_snapshots(root, retro):
        data, err = read_json(path)
        if err or not isinstance(data, dict):
            continue
        channel = data.get("channel_id")
        for ts in (data.get("ts"), data.get("thread_ts"), *(m.get("ts") for m in data.get("messages", []) if isinstance(m, dict))):
            if ts:
                covered.add((channel, ts))
    return covered


def evidence_check(root: Path, rep, retro: dict) -> None:
    ev = retro.get("evidence") or {}
    ts = retro.get("timestamps") or {}
    for kind in ("notebooks", "monitors"):
        for e in ev.get(kind, []):
            file = e.get("file")
            if not (isinstance(file, str) and (root / file).exists()):
                rep.err(f"evidence.{kind}: {file!r} does not exist")
                continue
            data, err = read_json(root / file)
            if err:
                rep.err(err)
                continue
            if data.get("schema") != f"ir.{kind[:-1]}/1":
                rep.err(f"{file}: schema must be ir.{kind[:-1]}/1")
            if data.get("id") != e.get("id"):
                rep.err(f"{file}: id {data.get('id')} does not match evidence.{kind} entry {e.get('id')}")
            if kind == "notebooks":
                window = data.get("time") or {}
                if window.get("live"):
                    rep.warn(f"{file}: the notebook uses a live span; set an absolute window in Datadog and re-fetch")
                if ts.get("onset") and ts.get("resolved") and window.get("start") and window.get("end"):
                    if parse_ts(window["start"]) > parse_ts(ts["onset"]) or parse_ts(window["end"]) < parse_ts(ts["resolved"]):
                        rep.warn(f"{file}: the notebook window {window['start']} → {window['end']} does not cover onset → resolved")
    for m in (retro.get("detection") or {}).get("monitors", []):
        file = m.get("file")
        if not (isinstance(file, str) and (root / file).exists()):
            rep.warn(f"detection.monitors {m.get('id')}: no snapshot file; run evidence fetch --monitor {m.get('id')}")
    for e in ev.get("slack", []):
        file = e.get("file")
        if not (isinstance(file, str) and (root / file).exists()):
            rep.err(f"evidence.slack: {file!r} does not exist")
            continue
        data, err = read_json(root / file)
        if err:
            rep.err(err)
            continue
        for issue in slack_file_issues(Path(file), data):
            rep.err(issue)
        if SLACK_PERMALINK.match(str(e.get("url", ""))) and isinstance(data, dict):
            p = parse_permalink(e["url"])
            if (p["channel_id"], p["ts"]) != (data.get("channel_id"), data.get("ts")):
                rep.err(f"evidence.slack: {e['url']} does not match the permalink in {file}")
    for img in ev.get("images", []):
        file = img.get("file")
        if not (isinstance(file, str) and (root / file).exists()):
            rep.err(f"evidence.images: {file!r} does not exist")
        if not (isinstance(img.get("alt"), str) and img["alt"].strip()):
            rep.err(f"evidence.images {file}: alt text is required")
    covered = slack_coverage(root, retro)
    cited = {**retro, "evidence": {k: v for k, v in ev.items() if k != "slack"}}
    for s in strings_in(cited):
        for m in SLACK_PERMALINK.finditer(s):
            p = parse_permalink(m.group(0))
            if (p["channel_id"], p["ts"]) not in covered:
                rep.warn(f"Slack permalink {m.group(0)} has no snapshot under evidence/slack/; write one with evidence slack new")


def add_evidence_parsers(sub) -> None:
    ev = sub.add_parser("evidence", help="fetch Datadog snapshots and validate Slack snapshots under evidence/")
    evs = ev.add_subparsers(dest="evidence_cmd", required=True)
    fe = evs.add_parser("fetch", help="snapshot Datadog notebooks and monitors into evidence/datadog/ and register them in retro.json")
    fe.add_argument("dir")
    fe.add_argument("--notebook", action="append", metavar="ID|URL", help="notebook id or app.<site>/notebook/<id> URL; repeatable")
    fe.add_argument("--monitor", action="append", metavar="ID|URL", help="monitor id or app.<site>/monitors/<id> URL; repeatable")
    fe.add_argument("--from-ssm", action="store_true", help="read the API and application keys from AWS SSM instead of DD_API_KEY / DD_APP_KEY")
    fe.add_argument("--ssm-api-key-path", metavar="P", help="SSM parameter holding the API key (required with --from-ssm)")
    fe.add_argument("--ssm-app-key-path", metavar="P", help="SSM parameter holding the application key (required with --from-ssm)")
    fe.add_argument("--aws-profile", metavar="NAME")
    fe.add_argument("--aws-region", metavar="R")
    fe.add_argument("--site", default="datadoghq.com", help="Datadog site (default datadoghq.com)")
    fe.add_argument("--logs-limit", type=int, default=25, help="log lines kept per log_stream cell (default 25)")
    fe.add_argument("--interval", type=int, metavar="SECONDS", help="timeseries rollup; default lets Datadog choose, capped at ~1500 points")
    fe.add_argument("--no-register", action="store_true", help="write the files without touching retro.json")
    fe.add_argument("--allow-terms", action="store_true", help="write a snapshot even when the forbidden-terms grep matches")
    fe.add_argument("--forbidden-terms", metavar="REGEX", help="terms to grep the payload for; default FORBIDDEN_TERMS, else the nearest .customer-names")
    fe.add_argument("--dry-run", action="store_true", help="print the per-cell plan without querying data or writing files")
    fe.set_defaults(func=fetch)
    sl = evs.add_parser("slack", help="validate or scaffold ir.slack/1 snapshots the authoring agent writes from its Slack tooling")
    sls = sl.add_subparsers(dest="slack_cmd", required=True)
    sc = sls.add_parser("check", help="validate every Slack snapshot under evidence/slack/ and registered in retro.json")
    sc.add_argument("dir")
    sc.set_defaults(func=slack_check)
    sn = sls.add_parser("new", help="print an ir.slack/1 skeleton for one permalink")
    sn.add_argument("--permalink", required=True, metavar="URL")
    sn.add_argument("--channel-name", required=True, metavar="NAME")
    sn.add_argument("--thread", action="store_true", help="the snapshot holds the whole thread, root first")
    sn.set_defaults(func=slack_new)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_evidence_parsers(sub)
    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
