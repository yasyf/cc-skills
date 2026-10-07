from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from livedash.components import tasks
from livedash.context import failure_text

MODIFIED = datetime(2026, 10, 5, 5, 20, tzinfo=UTC)


def write_task(directory: Path, task: dict, mtime: float) -> None:
    path = directory / f"{task['id']}.json"
    path.write_text(json.dumps(task))
    os.utime(path, (mtime, mtime))


def test_read_tasks_merges_live_files_and_the_archive(tmp_path):
    write_task(tmp_path, {"id": "641", "subject": "ship", "status": "in_progress", "owner": "dash"}, MODIFIED.timestamp())
    (tmp_path / ".archive.ndjson").write_text(json.dumps({"id": "6", "subject": "old", "status": "completed", "archivedAt": "2026-10-05T04:51:14Z"}) + "\n")
    found = sorted(tasks.read_tasks(tmp_path), key=lambda task: int(task["id"]))
    assert found == [
        {"id": "6", "subject": "old", "status": "completed", "archivedAt": "2026-10-05T04:51:14Z", "archived": True, "updated_at": "2026-10-05T04:51:14Z"},
        {"id": "641", "subject": "ship", "status": "in_progress", "owner": "dash", "updated_at": "2026-10-05T05:20:00Z"},
    ]


def test_task_list_dir_follows_the_newest_teammate_team(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTAIN_HOOK_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)
    transcript = tmp_path / "project" / "900424b6-7393.jsonl"
    subagents = transcript.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-a.meta.json").write_text(json.dumps({"teamName": "session-1111aaaa"}))
    (subagents / "agent-b.meta.json").write_text(json.dumps({"teamName": "session-67c0e5da"}))
    os.utime(subagents / "agent-a.meta.json", (1, 1))
    (tmp_path / "tasks" / "session-67c0e5da").mkdir(parents=True)
    (tmp_path / "tasks" / "session-900424b6").mkdir(parents=True)
    assert tasks.task_list_dir("900424b6-7393", transcript) == tmp_path / "tasks" / "session-67c0e5da"


def test_an_explicit_task_list_id_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTAIN_HOOK_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "shared-list")
    (tmp_path / "tasks" / "shared-list").mkdir(parents=True)
    (tmp_path / "tasks" / "session-900424b6").mkdir(parents=True)
    assert tasks.task_list_dir("900424b6-7393", tmp_path / "p" / "900424b6-7393.jsonl") == tmp_path / "tasks" / "shared-list"


def test_project_slug_matches_the_claude_projects_directory():
    assert tasks.project_slug("/Users/me/.orca/workspaces/repo/repo/sole") == "-Users-me--orca-workspaces-repo-repo-sole"


def test_failure_text_names_the_exit_or_timeout():
    warning = "Warning: an API token in the environment overrides the stored credential."
    assert failure_text(subprocess.CalledProcessError(1, ["bk"], stderr=warning)) == f"exit 1: {warning}"
    assert failure_text(subprocess.TimeoutExpired(["bk"], 30, stderr=warning.encode())) == f"timed out after 30s: {warning}"
