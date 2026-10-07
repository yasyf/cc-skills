from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from livedash import Col, Context, Entry, Feed, Table, component, view
from livedash.components import ledger as ledgers
from livedash.components import orca
from livedash.components import bus
from livedash.components.bus import records
from livedash.components.release import overview

OPEN_KINDS = ("open_blockers", "open_defects", "open_holds", "untracked_holds")
SUPERSEDING = ["ready", "head"]
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
STOOD_DOWN = re.compile(r"\bSTAND-?DOWN\b|\bstood down\b|\bhandoff\b", re.IGNORECASE)
UNEXPIRING = frozenset({"hold"})
SETTLED_PR = frozenset({"landed", "closed-without-squash", "closed"})
INCIDENT_TONE = {"incident": "bad", "evidence": "warn", "mechanism": "warn", "fix-live": "ok", "recovered": "ok", "not-ours": "muted", "duplicate": "muted", "done": "ok"}


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


def settled_pr(record: dict, states: dict[str, str]) -> bool:
    pr = topic_pr(record)
    return pr is not None and states.get(pr) in SETTLED_PR


@component("open-records", "Open cci records", question="Which cci blockers, defects and holds are still open, and which went stale?", reads=["cci digest", "cci records", "ccn ledger row list"], every="5m", timeout="60s", actions={"close": close_record})
def open_records(ctx: Context, *, ledger: str | None = None, stale_only: bool = False, within: str = "24h") -> Table:
    """The cci digest's open blockers, defects and holds since the drive started, minus what settled: a cite a `resolved:`
    done record closed, a record a later one answers by `re` or `resolves`, a record about one PR the ledger shows landed
    or closed, and a blocker or defect older than `within`; holds never expire. A record is stale when it names a PR whose
    ledger head no longer matches the SHA it cites, or when a later READY or HEAD record exists for that PR. Action
    `close` posts `done --topic resolved:<cite>`."""
    since = ctx.facts.get("started_at")
    digest = ctx.cci("digest", since_time=since)
    closed = overview.curated(records(ctx, since, kind="done"))
    later = records(ctx, since, kind=SUPERSEDING)
    ledger_rows = {key: fields for key, fields in ledgers.rows(ctx, ledger).items() if key.isdigit()} if ledger else {}
    heads = {key: ledgers.current_head(fields) for key, fields in ledger_rows.items()}
    states = {key: fields.get("state", "open") for key, fields in ledger_rows.items()}
    candidates = [record for name in OPEN_KINDS for record in digest.get(name) or [] if f"cci:{record['seq']}" not in closed]
    live = [record for record in candidates if record["kind"] in UNEXPIRING or not bus.expired(record, ctx.now, within)]
    replied = bus.answered(ctx, live)
    out = []
    for record in live:
        if record["seq"] in replied or settled_pr(record, states):
            continue
        cite = f"cci:{record['seq']}"
        stale = staleness(record, heads, later)
        if stale_only and not stale:
            continue
        out.append({"key": cite, "cite": cite, "kind": record["kind"], "lane": record["lane"], "to": ", ".join(record.get("to") or []), "text": record["text"], "at": record["at"], "stale": stale, "tone": "muted" if stale else "warn"})
    out.sort(key=lambda row: (row["stale"] is None, row["at"]))
    aged = len(candidates) - len(live)
    note = f"Not listed: {aged} blockers and defects older than {within} with no answer." if aged else None
    return Table([Col("kind", "Kind", "badge"), Col("lane", "Lane"), Col("to", "To"), Col("text", "Record"), Col("stale", "Stale because"), Col("at", "Posted", "age")], out, note=note or (None if out else "No open records."))


def worktrees(ctx: Context) -> set[str]:
    listing = ctx.run(["git", "-C", str(ctx.facts["checkout"]), "worktree", "list", "--porcelain"])
    return {Path(line.removeprefix("worktree ")).name for line in listing.splitlines() if line.startswith("worktree ")}


def orca_attention(ctx: Context, run: str) -> list[dict]:
    names = {task["id"]: task.get("display_name") or task.get("task_title") for task in ctx.json(["orca", "orchestration", "task-list", "--run", run, "--json"])["result"]["tasks"]}
    out = []
    for worker in orca.workers(ctx, run):
        projection = worker.get("projection") or {}
        attention = projection.get("attention") or {}
        if worker["dispatchStatus"] in orca.SETTLED or not attention.get("requiresAction") or (projection.get("liveness") or {}).get("verdict") != orca.LIVE:
            continue
        name = names.get(worker["taskId"]) or worker["taskId"]
        out.append({"key": worker["dispatchId"], "cite": f"orca:{worker['taskId']}", "lane": name, "group": "needs attention", "kind": "orca", "text": ", ".join(attention.get("categories") or []), "at": None, "tone": "bad"})
    return out


@component("cci-lanes", "Lanes", question="Which lanes are working now, and which need attention?", reads=["cci lanes", "git worktree list", "orca orchestration worker-list", "orca orchestration task-list"], every="1m", timeout="60s")
def lanes(ctx: Context, *, idle_hours: float = 1.0) -> Table:
    """Lanes whose latest cci record is within `idle_hours`, stood-down lanes still holding a worktree of the drive's
    checkout, and live Orca workers asking for attention, named by their task. Idle and stood-down lanes are counted in
    the note, not listed; settled Orca dispatches never show."""
    latest = ctx.cci("lanes")
    trees = worktrees(ctx) if ctx.facts.get("checkout") else set()
    out = []
    quiet = {"idle": 0, "stood down": 0}
    for record in latest:
        if record["lane"] in ("owner", "root", "main"):
            continue
        down = record["kind"] in ("handoff", "withdraw") or bool(STOOD_DOWN.search(record["text"]))
        if down and record["lane"] not in trees:
            quiet["stood down"] += 1
            continue
        if not down and ctx.now - view.stamp(record["at"]) > timedelta(hours=idle_hours):
            quiet["idle"] += 1
            continue
        group = "holds a worktree" if down else "active"
        out.append({"key": record["lane"], "cite": f"cci:{record['seq']}", "lane": record["lane"], "group": group, "kind": record["kind"], "text": record["text"], "at": record["at"], "tone": {"active": "ok", "holds a worktree": "bad"}[group]})
    if run := ctx.facts.get("orca_run"):
        out += orca_attention(ctx, run)
    order = {"needs attention": 0, "active": 1, "holds a worktree": 2}
    out.sort(key=lambda row: row["at"] or "", reverse=True)
    out.sort(key=lambda row: order[row["group"]])
    note = f"Not listed: {quiet['idle']} idle over {idle_hours:g}h, {quiet['stood down']} stood down."
    return Table([Col("lane", "Lane"), Col("kind", "Last", "badge"), Col("text", "Latest record"), Col("at", "When", "age")], out, group_by="group", note=note)


@component("incident-feed", "Incident records", question="Which incidents opened or moved in the last few days?", reads=["cci records"], every="1m")
def incident_feed(ctx: Context, *, hours: int = 72) -> Feed:
    """Incident, evidence, mechanism, fix-live, recovered, not-ours, duplicate and done records from the last `hours`."""
    found = records(ctx, view.iso(ctx.now - timedelta(hours=hours)), kind=list(overview.INCIDENT_KINDS))
    entries = [Entry(record["at"], record["lane"], record["text"], (record.get("refs") or {}).get("url"), INCIDENT_TONE.get(record["kind"]), key=f"cci:{record['seq']}", cite=f"cci:{record['seq']}") for record in reversed(found)]
    return Feed(entries, note=None if entries else f"No incident records in {hours}h.")
