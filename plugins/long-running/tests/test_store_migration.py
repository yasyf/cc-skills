from __future__ import annotations

import fcntl
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from long_running import migration
from long_running.model import Incident, StaleGeneration, stamp

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


@pytest.fixture
def root(tmp_path):
    directory = tmp_path / "incidents"
    directory.mkdir(mode=0o700)
    yield directory
    directory.chmod(0o700)


def legacy(root, name="one"):
    incident = Incident(name, "owner", 3, stamp(NOW), {"nested": {"items": [1]}})
    incident.accept("effect", "send", "destination", "grant", NOW)
    incident.start("effect", NOW)
    (root / f"{name}.json").write_text(json.dumps(incident.dump(), indent=2) + "\n")
    (root / f".{name}.lock").touch()
    return incident


def publish_legacy(root, incident):
    root.mkdir(parents=True, exist_ok=True)
    with (root / f".{incident.incident_id}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / f"{incident.incident_id}.json"
        staged = path.with_name(f".{path.name}.{os.getpid()}")
        staged.write_text(json.dumps(incident.dump(), indent=2) + "\n")
        staged.replace(path)


def import_snapshot(directory, incident):
    path = directory / f"{incident.incident_id}.imported"
    value = incident.dump()
    if path.exists():
        assert json.loads(path.read_text()) == value
    else:
        path.write_text(json.dumps(value))


def test_discovery_requires_explicit_setup_without_mutating(root):
    mode = root.stat().st_mode
    with pytest.raises(migration.InitializationRequired):
        migration.data_root(root)
    assert root.stat().st_mode == mode
    assert list(root.iterdir()) == []
    legacy(root)
    with pytest.raises(migration.MigrationRequired):
        migration.data_root(root)
    assert root.stat().st_mode == mode
    assert not (root / migration.NAMESPACE).exists()


def test_initialize_is_explicit_private_idempotent_and_excludes_unknown_ids(root):
    manifest = migration.initialize(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o500
    assert manifest["fence"]["original_mode"] == 0o700
    assert manifest["fence"]["uid"] == os.geteuid()
    assert manifest["fence"]["root"] == str(root.resolve())
    assert migration.initialize(root) == manifest
    directory = migration.data_root(root)
    (directory / "new-briefs").mkdir()
    (directory / "new.runner").touch()
    with pytest.raises(PermissionError):
        publish_legacy(root, Incident("unknown", "old", 0, stamp(NOW), {}))
    assert not (root / "unknown.json").exists()


def test_initializer_does_not_adopt_an_uncommitted_unknown_database(root):
    directory = root / migration.NAMESPACE
    directory.mkdir()
    (directory / "unknown.sqlite3").write_bytes(b"uncommitted")
    with pytest.raises(ValueError, match="without a cutover manifest"):
        migration.initialize(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert not (directory / "READY.json").exists()


def test_racing_legacy_creator_cannot_make_fresh_sqlite_authority(root, monkeypatch):
    fence = migration._legacy_fence

    @contextmanager
    def race(root, directory, resume):
        legacy(root)
        with fence(root, directory, resume) as result:
            yield result

    monkeypatch.setattr(migration, "_legacy_fence", race)
    with pytest.raises(migration.MigrationRequired, match="appeared"):
        migration.initialize(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert not (root / migration.NAMESPACE / "READY.json").exists()


def test_migration_preserves_effect_state_briefs_locks_and_blocks_old_publication(root):
    incident = legacy(root)
    old_lock = (root / ".one.lock").stat().st_ino
    briefs = root / "one"
    briefs.mkdir()
    (briefs / "prior.md").write_text("existing")
    staged = root / ".one.json.preopened"
    with staged.open("w") as descriptor:
        manifest = migration.migrate(root, import_snapshot)
        descriptor.write("late staged bytes")
        descriptor.flush()
        with pytest.raises(PermissionError):
            staged.replace(root / "one.json")
    assert (root / ".one.lock").stat().st_ino == old_lock
    assert json.loads((root / "one.json").read_text()) == incident.dump()
    directory = migration.data_root(root)
    imported = json.loads((directory / "one.imported").read_text())
    assert imported == incident.dump()
    assert imported["actions"]["effect"]["status"] == "started"
    assert imported["owner_generation"] == 3
    assert imported["input_revision"] == 1
    assert manifest["source"].keys() == {"one"}
    (briefs / "after.md").write_text("still writable")
    assert (briefs / "prior.md").read_text() == "existing"
    incident.facts["late"] = True
    with pytest.raises(PermissionError):
        publish_legacy(root, incident)
    with pytest.raises(PermissionError):
        publish_legacy(root, Incident("unknown", "old", 0, stamp(NOW), {}))
    assert "late" not in json.loads((root / "one.json").read_text())["facts"]


def test_busy_supported_writer_prevents_import_and_restores_original_mode(root):
    legacy(root)
    with (root / ".one.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            migration.migrate(root, import_snapshot)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert not (root / migration.NAMESPACE / "READY.json").exists()
    assert not (root / migration.NAMESPACE / "FENCE.json").exists()


def test_interrupted_import_stays_fenced_until_explicit_idempotent_resume(root):
    legacy(root, "one")
    legacy(root, "two")

    def interrupted(directory, incident):
        import_snapshot(directory, incident)
        raise RuntimeError("interrupted after a completed database")

    with pytest.raises(RuntimeError, match="interrupted"):
        migration.migrate(root, interrupted)
    assert stat.S_IMODE(root.stat().st_mode) == 0o500
    with pytest.raises(migration.ResumeRequired):
        migration.data_root(root)
    with pytest.raises(migration.ResumeRequired):
        migration.migrate(root, import_snapshot)
    with pytest.raises(PermissionError):
        publish_legacy(root, Incident("unknown", "old", 0, stamp(NOW), {}))
    manifest = migration.migrate(root, import_snapshot, resume=True)
    assert manifest["source"].keys() == {"one", "two"}
    assert not (migration.data_root(root) / "PENDING.json").exists()
    assert len(list(migration.data_root(root).glob("*.imported"))) == 2


def test_out_of_contract_inplace_writer_is_detected_before_resume_commit(root):
    legacy(root)

    def interrupted(directory, incident):
        raise RuntimeError("interrupt")

    with pytest.raises(RuntimeError):
        migration.migrate(root, interrupted)
    record = json.loads((root / "one.json").read_text())
    record["facts"]["inplace"] = True
    (root / "one.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="snapshots changed"):
        migration.migrate(root, import_snapshot, resume=True)
    assert not (root / migration.NAMESPACE / "READY.json").exists()
    assert stat.S_IMODE(root.stat().st_mode) == 0o500


def test_acl_roots_are_refused_before_permission_changes(root, monkeypatch):
    monkeypatch.setattr(migration.sys, "platform", "darwin")
    monkeypatch.setattr(migration.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="drwx------ root\n 0: user:someone allow add_file\n"))
    with pytest.raises(ValueError, match="ACL"):
        migration.initialize(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert not (root / migration.NAMESPACE).exists()


def test_ineffective_permission_fence_never_exposes_ready(root, monkeypatch):
    legacy(root)
    monkeypatch.setattr(migration, "_fenced", lambda path: False)
    with pytest.raises(PermissionError):
        migration.migrate(root, import_snapshot)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert not (root / migration.NAMESPACE / "READY.json").exists()


def test_postpublication_error_never_reopens_legacy_authority(root, monkeypatch):
    legacy(root)
    sync = migration._sync

    def fail_after_ready(directory):
        if (directory / "READY.json").is_file():
            raise OSError("postpublication sync failed")
        sync(directory)

    monkeypatch.setattr(migration, "_sync", fail_after_ready)
    with pytest.raises(OSError, match="postpublication"):
        migration.migrate(root, import_snapshot)
    assert stat.S_IMODE(root.stat().st_mode) == 0o500
    assert migration.data_root(root) == root / migration.NAMESPACE
    with pytest.raises(PermissionError):
        publish_legacy(root, Incident("unknown", "old", 0, stamp(NOW), {}))


def test_real_database_import_preserves_generation_and_does_not_retry_started_effect(root):
    from long_running import sqlite_store

    original = legacy(root)
    migration.migrate(root, sqlite_store.import_database)
    store = sqlite_store.Store(root)
    assert store.load("one").dump() == original.dump()
    with pytest.raises(StaleGeneration):
        with store.transaction("one", generation=2):
            pytest.fail("stale generation was admitted")
    with store.transaction("one", generation=3) as incident:
        incident.lose("effect")
        assert incident.accept("effect", "send", "ignored", None, NOW).status == "unverifiable"
        with pytest.raises(ValueError, match="not accepted"):
            incident.start("effect", NOW)
    assert store.find_action("one", "effect").status == "unverifiable"
