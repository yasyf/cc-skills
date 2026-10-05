from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from cc_transcript import Session
from captain_hook.events import SessionStartEvent, StopEvent
from captain_hook.testing.helpers import build_context

from fire import fire
from hooks import session_tree, task_archive
from hooks.compaction_handoff import CompactionState

SESSION = "0123456789abcdef"
NOW = time.time()
OLD = NOW - task_archive.ARCHIVE_AGE_SECONDS - 60


def write_task(directory: Path, task_id: str, status: str, at: float) -> Path:
    path = directory / f"{task_id}.json"
    path.write_text(json.dumps({"id": task_id, "subject": f"task {task_id}", "status": status, "blocks": [], "blockedBy": []}))
    os.utime(path, (at, at))
    return path


def archived(directory: Path) -> list[dict]:
    archive = directory / task_archive.ARCHIVE
    return [json.loads(line) for line in archive.read_text().splitlines()] if archive.exists() else []


@pytest.fixture
def tasks(tmp_path: Path) -> Path:
    directory = tmp_path / "tasks" / f"session-{SESSION[:8]}"
    directory.mkdir(parents=True)
    (directory / ".lock").touch()
    (directory / ".highwatermark").write_text("3")
    return directory


def test_only_completed_tasks_older_than_two_hours_leave_the_list(tasks: Path) -> None:
    write_task(tasks, "4", "completed", OLD)
    write_task(tasks, "5", "completed", NOW)
    write_task(tasks, "6", "in_progress", OLD)
    write_task(tasks, "7", "pending", NOW)

    assert task_archive.archive_completed(tasks, NOW) == ["4"]

    assert sorted(path.stem for path in tasks.glob("*.json")) == ["5", "6", "7"]
    [record] = archived(tasks)
    assert record["id"] == "4"
    assert record["status"] == "completed"
    assert record["archivedAt"].endswith("Z")
    assert (tasks / ".highwatermark").read_text() == "7"
    assert not (tasks / task_archive.LIST_LOCK).exists()


def test_archive_appends_and_never_lowers_the_high_water_mark(tasks: Path) -> None:
    (tasks / ".highwatermark").write_text("40")
    write_task(tasks, "4", "completed", OLD)
    task_archive.archive_completed(tasks, NOW)
    write_task(tasks, "9", "completed", OLD)
    task_archive.archive_completed(tasks, NOW)

    assert [record["id"] for record in archived(tasks)] == ["4", "9"]
    assert (tasks / ".highwatermark").read_text() == "40"


def test_held_list_lock_skips_the_sweep(tasks: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_tree, "LOCK_RETRIES", 0)
    write_task(tasks, "4", "completed", OLD)
    (tasks / task_archive.LIST_LOCK).mkdir()

    assert task_archive.archive_completed(tasks, NOW) == []
    assert (tasks / "4.json").exists()


def test_task_held_by_claude_code_stays_in_the_list(tasks: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_tree, "LOCK_RETRIES", 0)
    write_task(tasks, "4", "completed", OLD)
    write_task(tasks, "5", "completed", OLD)
    (tasks / "4.json.lock").mkdir()

    assert task_archive.archive_completed(tasks, NOW) == ["5"]
    assert (tasks / "4.json").exists()


def event(tmp_path: Path, cls, **raw):
    transcript = tmp_path / "projects" / "p" / f"{SESSION}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.touch()
    payload = {"session_id": SESSION, "transcript_path": str(transcript), "cwd": str(tmp_path)} | raw
    ctx = build_context(transcript=Session.from_path(transcript), session_dir=tmp_path / "state")
    return cls(_raw=payload, ctx=ctx)


def test_drive_sweeps_on_session_start_and_at_most_every_half_hour_on_stop(
    tasks: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAPTAIN_HOOK_TASKS_DIR", str(tmp_path / "tasks"))
    CompactionState(active=True).save(event(tmp_path, StopEvent))
    write_task(tasks, "4", "completed", OLD)

    fire(task_archive, event(tmp_path, SessionStartEvent, source="compact"))
    assert not (tasks / "4.json").exists()

    write_task(tasks, "5", "completed", OLD)
    fire(task_archive, event(tmp_path, StopEvent))
    assert (tasks / "5.json").exists()

    with task_archive.ArchiveState.mutate(event(tmp_path, StopEvent)) as state:
        state.swept_at -= task_archive.STOP_INTERVAL_SECONDS
    fire(task_archive, event(tmp_path, StopEvent))
    assert [record["id"] for record in archived(tasks)] == ["4", "5"]


def test_inactive_drive_keeps_every_task(tasks: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPTAIN_HOOK_TASKS_DIR", str(tmp_path / "tasks"))
    write_task(tasks, "4", "completed", OLD)

    fire(task_archive, event(tmp_path, SessionStartEvent, source="resume"))

    assert (tasks / "4.json").exists()
