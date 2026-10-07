from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from livedash import Col, Context, Entry, Feed, Table, component, view
from livedash.components import ledger as ledgers
from livedash.components.release import overview

PAGE = 500
OPEN_KINDS = ("open_blockers", "open_defects", "open_holds", "untracked_holds")
SUPERSEDING = ["ready", "head"]
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
STOOD_DOWN = re.compile(r"\bSTAND-?DOWN\b|\bstood down\b|\bhandoff\b", re.IGNORECASE)
IDLE = timedelta(hours=1)
INCIDENT_TONE = {"incident": "bad", "evidence": "warn", "mechanism": "warn", "fix-live": "ok", "recovered": "ok", "not-ours": "muted", "duplicate": "muted", "done": "ok"}


def records(ctx: Context, since: str, **query) -> list[dict]:
    found: list[dict] = []
    while True:
        page = ctx.cci("records", since_time=since, limit=PAGE, **({"since": found[-1]["seq"]} if found else {}), **query)
        found += page
        if len(page) < PAGE:
            return found


def topic_pr(record: dict) -> str | None:
    if (topic := record.get("topic") or "").isdigit():
        return topic
    prs = (record.get("refs") or {}).get("prs") or []
    return str(prs[0]) if prs else None


def staleness(record: dict, heads: dict[str, str], later: list[dict]) -> str | None:
    pr = topic_pr(record)
    if pr is None:
        return None
    if (head := heads.get(pr)) and (cited := SHA.findall(record["text"])) and not any(head.startswith(sha) for sha in cited):
        return f"#{pr} moved to {head[:10]}"
    if newer := next((other for other in later if other["seq"] > record["seq"] and topic_pr(other) == pr), None):
        return f"{newer['kind']} #{newer['seq']} came after"
    return None


def close_record(ctx: Context, row: dict, text: str) -> str:
    reason = text or row.get("stale") or "closed from the dashboard"
    record = ctx.json(["cci", "post", "--drive", ctx.facts["cci_drive"], "--lane", "owner", "--kind", "done", "--topic", f"resolved:{row['cite']}", "--text", f"closed {row['cite']}: {reason}"[:400], "--json"])
    return f"cci #{record['seq']} closes {row['cite']}"


@component("open-records", "Open cci records", every="5m", timeout="60s", actions={"close": close_record})
def open_records(ctx: Context, *, ledger: str | None = None, stale_only: bool = False) -> Table:
    """The cci digest's open blockers, defects and holds since the drive started, minus cites a `resolved:` done record
    closed. A record is stale when it names a PR whose ledger head no longer matches the SHA it cites, or when a later
    READY or HEAD record exists for that PR. Action `close` posts `done --topic resolved:<cite>`."""
    since = ctx.facts.get("started_at")
    digest = ctx.cci("digest", since_time=since)
    closers = records(ctx, since, kind="done")
    closed = overview.curated(closers)
    later = records(ctx, since, kind=SUPERSEDING)
    heads = {key: ledgers.current_head(fields) for key, fields in ledgers.rows(ctx, ledger).items() if key.isdigit()} if ledger else {}
    out = []
    for name in OPEN_KINDS:
        for record in digest.get(name) or []:
            cite = f"cci:{record['seq']}"
            if cite in closed:
                continue
            stale = staleness(record, heads, later)
            if stale_only and not stale:
                continue
            out.append({"key": cite, "cite": cite, "kind": record["kind"], "lane": record["lane"], "to": ", ".join(record.get("to") or []), "text": record["text"], "at": record["at"], "stale": stale, "tone": "muted" if stale else "warn"})
    out.sort(key=lambda row: (row["stale"] is None, row["at"]))
    return Table([Col("kind", "Kind", "badge"), Col("lane", "Lane"), Col("to", "To"), Col("text", "Record"), Col("stale", "Stale because"), Col("at", "Posted", "age")], out, note=None if out else "No open records.")


def worktrees(ctx: Context) -> set[str]:
    listing = ctx.run(["git", "-C", str(ctx.facts["checkout"]), "worktree", "list", "--porcelain"])
    return {Path(line.removeprefix("worktree ")).name for line in listing.splitlines() if line.startswith("worktree ")}


@component("cci-lanes", "Lanes", every="1m", timeout="60s")
def lanes(ctx: Context, *, idle_hours: float = 1.0) -> Table:
    """Each lane's latest cci record, grouped active, idle past `idle_hours`, stood down, and stood down while still holding
    a worktree of the drive's checkout; Orca workers needing attention join when the drive has an Orca run."""
    latest = ctx.cci("lanes")
    trees = worktrees(ctx) if ctx.facts.get("checkout") else set()
    out = []
    for record in latest:
        if record["lane"] in ("owner", "root", "main"):
            continue
        age = ctx.now - view.stamp(record["at"])
        down = record["kind"] in ("handoff", "withdraw") or bool(STOOD_DOWN.search(record["text"]))
        group = ("holds a worktree" if record["lane"] in trees else "stood down") if down else "active" if age <= timedelta(hours=idle_hours) else f"idle over {idle_hours:g}h"
        out.append({"key": record["lane"], "cite": f"cci:{record['seq']}", "lane": record["lane"], "group": group, "kind": record["kind"], "text": record["text"], "at": record["at"], "tone": {"active": "ok", "holds a worktree": "bad"}.get(group, "muted")})
    if run := ctx.facts.get("orca_run"):
        for worker in ctx.json(["orca", "orchestration", "worker-list", "--run", run, "--json", "--limit", "100"])["result"]["workers"]:
            attention = (worker.get("projection") or {}).get("attention") or {}
            if attention.get("requiresAction"):
                out.append({"key": worker["dispatchId"], "cite": f"orca:{worker['dispatchId']}", "lane": worker["dispatchId"], "group": "needs attention", "kind": "orca", "text": ", ".join(attention.get("categories") or []), "at": None, "tone": "bad"})
    order = {"needs attention": 0, "active": 1, "holds a worktree": 3, "stood down": 4}
    out.sort(key=lambda row: row["at"] or "", reverse=True)
    out.sort(key=lambda row: order.get(row["group"], 2))
    return Table([Col("lane", "Lane"), Col("kind", "Last", "badge"), Col("text", "Latest record"), Col("at", "When", "age")], out, group_by="group", note=None if out else "No lane has posted.")


@component("incident-feed", "Incidents", every="1m")
def incident_feed(ctx: Context, *, hours: int = 72) -> Feed:
    """Incident, evidence, mechanism, fix-live, recovered, not-ours, duplicate and done records from the last `hours`."""
    found = records(ctx, view.iso(ctx.now - timedelta(hours=hours)), kind=list(overview.INCIDENT_KINDS))
    entries = [Entry(record["at"], record["lane"], record["text"], (record.get("refs") or {}).get("url"), INCIDENT_TONE.get(record["kind"]), key=f"cci:{record['seq']}", cite=f"cci:{record['seq']}") for record in reversed(found)]
    return Feed(entries, note=None if entries else f"No incident records in {hours}h.")
