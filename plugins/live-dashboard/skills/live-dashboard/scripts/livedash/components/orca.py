from __future__ import annotations

from livedash import Col, Context, Table, component

SETTLED = frozenset({"completed", "failed"})
LIVE = "live"


def utc(stamp: str) -> str:
    return stamp.replace(" ", "T") + "Z"


def workers(ctx: Context, run: str) -> list[dict]:
    found: list[dict] = []
    cursor: list[str] = []
    while True:
        page = ctx.json(["orca", "orchestration", "worker-list", "--run", run, "--json", "--limit", "100", *cursor])["result"]
        found += page["workers"]
        if not page["page"].get("hasMore"):
            return found
        cursor = ["--cursor", page["page"]["nextCursor"]]


@component("orca", "Orca workers", question="Which Orca workers are running now, and which need attention?", reads=["orca orchestration task-list", "orca orchestration worker-list"], every="2m", timeout="60s")
def orca(ctx: Context, *, orca_run: str | None = None) -> Table:
    """The drive's Orca run: each task whose current worker reports itself live, named by the task, with what it asks for.
    A dispatched worker with no live status and a task never dispatched are counted in the note, not listed."""
    if not orca_run:
        return Table([Col("name", "Worker")], [], note="This drive has no Orca run.")
    tasks = ctx.json(["orca", "orchestration", "task-list", "--run", orca_run, "--json"])["result"]["tasks"]
    current = {worker["taskId"]: worker for worker in workers(ctx, orca_run) if worker["dispatchStatus"] not in SETTLED}
    rows = []
    silent = 0
    for task in tasks:
        if task["status"] in SETTLED:
            continue
        projection = (current.get(task["id"]) or {}).get("projection") or {}
        if (projection.get("liveness") or {}).get("verdict") != LIVE:
            silent += 1
            continue
        attention = projection.get("attention") or {}
        categories = ", ".join(attention.get("categories") or []) if attention.get("requiresAction") else None
        rows.append({"key": task["id"], "cite": f"orca:{task['id']}", "name": task.get("display_name") or task.get("task_title"), "status": task["status"], "attention": categories, "at": utc(task["created_at"]), "tone": "warn" if categories else "ok"})
    rows.sort(key=lambda row: row["attention"] is None)
    note = f"Not listed: {silent} open tasks without a live worker." if silent else None
    return Table([Col("name", "Worker"), Col("status", "Status", "badge"), Col("attention", "Needs", "badge"), Col("at", "Started", "age")], rows, note=note or (None if rows else "No Orca worker is running."))
