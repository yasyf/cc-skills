from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from captain_hook import (
    Allow,
    BaseHookEvent,
    Event,
    FromSubagent,
    HookResult,
    Input,
    WorkflowState,
    on,
    workflow_state,
)
from captain_hook.tasks import Tasks

from .compaction_handoff import CompactionState
from .lane_rotation import DriveActive
from .session_tree import acquire

ARCHIVE_AGE_SECONDS = 2 * 60 * 60
STOP_INTERVAL_SECONDS = 30 * 60
ARCHIVE = ".archive.jsonl"
HIGH_WATER_MARK = ".highwatermark"
LIST_LOCK = ".lock.lock"


@workflow_state("long_running_task_archive")
class ArchiveState(WorkflowState):
    swept_at: float = 0.0


def list_dir(evt: BaseHookEvent) -> Path | None:
    root = Tasks.resolve_root()
    name = Tasks.list_id(evt.session_id, evt.transcript_path)
    return next((path for path in (root / name, root / f"session-{name[:8]}") if path.is_dir()), None)


def raise_high_water_mark(directory: Path, ids: list[int]) -> None:
    mark = directory / HIGH_WATER_MARK
    current = int(mark.read_text().strip() or 0) if mark.exists() else 0
    if ids and max(ids) > current:
        mark.write_text(str(max(ids)))


def archive_task(directory: Path, path: Path, now: float, archived_at: str) -> bool:
    lock = path.with_name(f"{path.name}.lock")
    if not acquire(lock):
        return False
    try:
        task = json.loads(path.read_text())
        if task.get("status") != "completed" or now - path.stat().st_mtime < ARCHIVE_AGE_SECONDS:
            return False
        with (directory / ARCHIVE).open("a") as archive:
            archive.write(json.dumps(task | {"archivedAt": archived_at}) + "\n")
            archive.flush()
            os.fsync(archive.fileno())
        path.unlink()
        return True
    finally:
        lock.rmdir()


def archive_completed(directory: Path, now: float) -> list[str]:
    lock = directory / LIST_LOCK
    if not acquire(lock):
        return []
    try:
        files = sorted(directory.glob("*.json"), key=lambda path: int(path.stem) if path.stem.isdigit() else 0)
        aged = [path for path in files if now - path.stat().st_mtime >= ARCHIVE_AGE_SECONDS]
        if not aged:
            return []
        raise_high_water_mark(directory, [int(path.stem) for path in files if path.stem.isdigit()])
        archived_at = datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        return [path.stem for path in aged if archive_task(directory, path, now, archived_at)]
    finally:
        lock.rmdir()


@on(
    Event.SessionStart | Event.PreCompact | Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(source="compact", state=[CompactionState(active=True)]): Allow(),
        Input(state=[CompactionState(active=True)]): Allow(),
        Input(source="resume"): Allow(),
    },
)
def archive_completed_tasks(evt: BaseHookEvent) -> HookResult | None:
    now = time.time()
    with ArchiveState.mutate(evt) as state:
        if evt.event == Event.Stop and now - state.swept_at < STOP_INTERVAL_SECONDS:
            return None
        state.swept_at = now
    if directory := list_dir(evt):
        archive_completed(directory, now)
    return None
