from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from livedash import Col, Context, Table, component, view


def project_slug(checkout: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", checkout)


def transcript_of(checkout: str, session: str) -> Path:
    projects = Path.home() / ".claude" / "projects"
    expected = projects / project_slug(checkout) / f"{session}.jsonl"
    return expected if expected.exists() else next(projects.glob(f"*/{session}.jsonl"), expected)


def task_list_dir(session: str, transcript: Path) -> Path | None:
    root = Path(os.environ.get("CAPTAIN_HOOK_TASKS_DIR") or Path.home() / ".claude" / "tasks")
    if explicit := os.environ.get("CLAUDE_CODE_TASK_LIST_ID"):
        return root / explicit if (root / explicit).is_dir() else None
    metas = sorted((transcript.with_suffix("") / "subagents").glob("agent-*.meta.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    team = next((name for meta in metas if (name := json.loads(meta.read_text()).get("teamName"))), None)
    names = [re.sub(r"[^\w.-]", "-", team)] if team else []
    names += [session, f"session-{session[:8]}"]
    return next((root / name for name in names if (root / name).is_dir()), None)


def read_tasks(directory: Path) -> list[dict]:
    tasks = []
    for path in directory.glob("*.json"):
        task = json.loads(path.read_text())
        tasks.append(task | {"updated_at": view.iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))})
    archive = directory / ".archive.ndjson"
    if archive.exists():
        archived = [json.loads(line) for line in archive.read_text().splitlines() if line.strip()]
        tasks += [task | {"archived": True, "updated_at": task.get("archivedAt")} for task in archived]
    return tasks


def session_tasks(checkout: str, sessions: list[str]) -> tuple[list[dict], Path | None]:
    for session in reversed(sessions):
        if directory := task_list_dir(session, transcript_of(checkout, session)):
            return read_tasks(directory), directory
    return [], None


@component("tasks", "Root tasks", question="What is the root session working on right now?", reads=["the root session's task list"], every="1m")
def tasks(ctx: Context, *, checkout: str, sessions: list[str], statuses: list[str] = ["in_progress"], within: str = "24h") -> Table:
    """The root session's task list (the newest of `sessions` that has one), narrowed to `statuses` and to tasks updated
    within `within`; every other open task is counted in the note by status."""
    found, directory = session_tasks(checkout, sessions)
    floor = ctx.now - view.window(within)
    live = [task for task in found if not task.get("archived") and task.get("status") not in ("completed", "deleted")]
    current = [task for task in live if task.get("status") in statuses and (view.stamp(task.get("updated_at")) or floor) > floor]
    rows = [
        {"key": task["id"], "cite": f"task:{task['id']}", "id": task["id"], "subject": task.get("subject", ""), "status": task.get("status"), "owner": task.get("owner"), "updated": task.get("updated_at"), "tone": "warn" if task.get("status") == "in_progress" else None}
        for task in sorted(current, key=lambda task: task.get("updated_at") or "", reverse=True)
    ]
    others = Counter(task.get("status") if task.get("status") not in statuses else f"{task.get('status')} untouched for {within}" for task in live if task not in current)
    if directory is None:
        note = f"No task list for sessions {', '.join(sessions)}."
    else:
        note = "Not listed: " + ", ".join(f"{count} {status.replace('_', ' ')}" for status, count in sorted(others.items())) + "." if others else None
    return Table([Col("id", "#"), Col("subject", "Task"), Col("status", "Status", "badge"), Col("owner", "Owner"), Col("updated", "Updated", "age")], rows, note=note)
