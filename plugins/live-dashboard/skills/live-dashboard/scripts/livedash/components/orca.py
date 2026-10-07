from __future__ import annotations

from livedash import Col, Context, Table, component

SETTLED = frozenset({"completed", "failed"})


def workers(ctx: Context, run: str) -> list[dict]:
    found: list[dict] = []
    cursor: list[str] = []
    while True:
        page = ctx.json(["orca", "orchestration", "worker-list", "--run", run, "--json", "--limit", "100", *cursor])["result"]
        found += page["workers"]
        if not page["page"].get("hasMore"):
            return found
        cursor = ["--cursor", page["page"]["nextCursor"]]


@component("orca", "Orca tasks", every="2m", timeout="60s")
def orca(ctx: Context, *, orca_run: str | None = None, settled: bool = False) -> Table:
    """The drive's Orca run: each task with its status, and the workers whose projection asks for attention."""
    if not orca_run:
        return Table([Col("name", "Task")], [], note="This drive has no Orca run.")
    tasks = ctx.json(["orca", "orchestration", "task-list", "--run", orca_run, "--json"])["result"]["tasks"]
    attention = {worker["taskId"]: worker for worker in workers(ctx, orca_run) if ((worker.get("projection") or {}).get("attention") or {}).get("requiresAction")}
    rows = []
    for task in tasks:
        if task["status"] in SETTLED and not settled:
            continue
        worker = attention.get(task["id"])
        categories = ", ".join(((worker or {}).get("projection") or {}).get("attention", {}).get("categories", []))
        rows.append({"key": task["id"], "cite": f"orca:{task['id']}", "name": task.get("display_name") or task.get("task_title"), "status": task["status"], "attention": categories or None, "at": task.get("completed_at") or task.get("created_at"), "tone": "bad" if worker else None})
    rows.sort(key=lambda row: row["attention"] is None)
    return Table([Col("name", "Task"), Col("status", "Status", "badge"), Col("attention", "Needs", "badge"), Col("at", "Since", "age")], rows, note=None if rows else "No open Orca tasks.")
