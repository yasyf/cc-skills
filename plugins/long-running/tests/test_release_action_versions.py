import json
import sqlite3
from dataclasses import asdict

import pytest

from long_running import actions


STAMP = "2026-11-01T00:00:00Z"


def record(key, kind="release"):
    return actions.Action("sample", key, kind, "{}", None, 0, 0, STAMP)


def release_store(tmp_path):
    store = actions.Store(tmp_path / "store")
    store.create(actions.Incident("sample", "owner", 0, STAMP, {}))
    with store.transaction("sample") as state:
        state.actions.update({key: record(key, kind) for key, kind in (("alpha", "release"), ("beta", "release"), ("ordinary", "ordinary"))})
    return store


def version_rows(store):
    with store._read("sample") as connection:
        expected = list(connection.execute("SELECT action_key, ordinal FROM action WHERE kind = 'release' ORDER BY ordinal"))
        actual = list(connection.execute("SELECT action_key, ordinal FROM release_action_version ORDER BY ordinal"))
    assert actual == expected
    return actual


def test_version_spans_closed_connections_and_ignores_unrelated_changes(tmp_path):
    store = release_store(tmp_path)
    selected, version = store.release_action_changes("sample", None, ())
    assert [action.action_id for _, action in selected] == ["alpha", "beta"]
    with actions.Store(store.root).transaction("sample") as state:
        state.facts["unrelated"] = 1
        state.action("ordinary").target = "changed"
    selected, same = store.release_action_changes("sample", version, ())
    assert selected == [] and same == version
    selected, same = actions.Store(store.root).release_action_changes("sample", version, ())
    assert selected == [] and same == version
    version_rows(store)


def test_old_ordinal_mutation_decodes_only_that_changed_release(tmp_path):
    store = release_store(tmp_path)
    initial, version = store.release_action_changes("sample", None, ())
    ordinal = next(ordinal for ordinal, action in initial if action.action_id == "alpha")
    with actions.Store(store.root).transaction("sample") as state:
        state.action("alpha").target = "changed"
    selected, after = store.release_action_changes("sample", version, ())
    assert [(position, action.action_id, action.target) for position, action in selected] == [(ordinal, "alpha", "changed")]
    assert after.epoch == version.epoch and after.revision > version.revision
    version_rows(store)


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("change", ["replace-key", "replace-ordinal", "replace-two", "replace-nonrelease", "update-key", "update-ordinal", "update-two", "kind-from-release", "kind-to-release", "delete"])
def test_direct_mutations_keep_exact_current_row_versions(tmp_path, recursive, change):
    store = release_store(tmp_path)
    initial, version = store.release_action_changes("sample", None, ())
    positions = {action.action_id: ordinal for ordinal, action in initial}
    active = tuple(ordinal for ordinal, _ in initial)
    fresh = max(active) + 10
    with store.transaction("sample") as state:
        connection = state._connection
        connection.execute(f"PRAGMA recursive_triggers = {recursive}")
        if change.startswith("replace"):
            key = "alpha" if change in ("replace-key", "replace-nonrelease") else "new" if change == "replace-ordinal" else "beta"
            ordinal = fresh if change == "replace-key" else positions["alpha"]
            kind = "ordinary" if change == "replace-nonrelease" else "release"
            action = record(key, kind)
            connection.execute("INSERT OR REPLACE INTO action(ordinal, action_key, kind, status, record) VALUES (?, ?, ?, ?, ?)", (ordinal, key, kind, action.status, json.dumps(asdict(action))))
        elif change == "update-key":
            action = record("renamed")
            connection.execute("UPDATE action SET action_key = ?, record = ? WHERE action_key = ?", ("renamed", json.dumps(asdict(action)), "alpha"))
        elif change == "update-ordinal":
            connection.execute("UPDATE action SET ordinal = ? WHERE action_key = ?", (fresh, "alpha"))
        elif change == "update-two":
            action = record("beta")
            connection.execute("UPDATE OR REPLACE action SET action_key = ?, ordinal = ?, kind = ?, status = ?, record = ? WHERE action_key = ?", ("beta", positions["alpha"], "release", action.status, json.dumps(asdict(action)), "ordinary"))
        elif change.startswith("kind-"):
            key = "alpha" if change == "kind-from-release" else "ordinary"
            kind = "ordinary" if change == "kind-from-release" else "release"
            connection.execute("UPDATE action SET kind = ?, record = ? WHERE action_key = ?", (kind, json.dumps(asdict(record(key, kind))), key))
        else:
            connection.execute("DELETE FROM action WHERE action_key = ?", ("alpha",))
    selected, after = store.release_action_changes("sample", version, active)
    assert [asdict(action) for _, action in selected] == [asdict(action) for action in store.actions("sample", kind="release")]
    assert after.revision > version.revision and after.epoch == version.epoch
    version_rows(store)


def test_identical_update_and_rollback_do_not_change_proof(tmp_path):
    store = release_store(tmp_path)
    _, version = store.release_action_changes("sample", None, ())
    with store.transaction("sample") as state:
        state._connection.execute("UPDATE action SET action_key = action_key, ordinal = ordinal, kind = kind, status = status, record = record WHERE action_key = ?", ("alpha",))
    selected, after = store.release_action_changes("sample", version, ())
    assert selected == [] and after == version
    marker = RuntimeError("writer rollback")
    with pytest.raises(RuntimeError) as caught:
        with store.transaction("sample") as state:
            changed = asdict(record("alpha")) | {"target": "rolled back"}
            state._connection.execute("UPDATE action SET record = ? WHERE action_key = ?", (json.dumps(changed), "alpha"))
            state._connection.execute("DELETE FROM action WHERE action_key = ?", ("beta",))
            assert state._connection.execute("SELECT revision FROM release_action_clock WHERE singleton = 1").fetchone()[0] > version.revision
            raise marker
    assert caught.value is marker
    selected, after = store.release_action_changes("sample", version, ())
    assert selected == [] and after == version
    version_rows(store)


def test_multiple_mutations_coalesce_to_one_latest_row(tmp_path):
    store = release_store(tmp_path)
    _, version = store.release_action_changes("sample", None, ())
    with store.transaction("sample") as state:
        for value in ("first", "last"):
            row = asdict(record("alpha")) | {"target": value}
            state._connection.execute("UPDATE action SET record = ? WHERE action_key = ?", (json.dumps(row), "alpha"))
    selected, after = store.release_action_changes("sample", version, ())
    assert [(action.action_id, action.target) for _, action in selected] == [("alpha", "last")]
    assert after.revision > version.revision
    version_rows(store)


def test_epoch_change_and_unrelated_schema_change_force_cold_read(tmp_path):
    store = release_store(tmp_path)
    _, version = store.release_action_changes("sample", None, ())
    with store.transaction("sample") as state:
        state._connection.execute("UPDATE release_action_clock SET epoch = randomblob(16) WHERE singleton = 1")
    selected, after = store.release_action_changes("sample", version, ())
    assert len(selected) == 2 and after.epoch != version.epoch
    with store.transaction("sample") as state:
        state._connection.execute("CREATE INDEX unrelated_probe_index ON action(status, kind)")
    selected, changed = store.release_action_changes("sample", after, ())
    assert len(selected) == 2 and changed.schema_version != after.schema_version


@pytest.mark.parametrize("change", ["trigger", "index", "user-version", "counter"])
def test_unsupported_tracking_or_schema_refuses_and_closes(tmp_path, monkeypatch, change):
    store = release_store(tmp_path)
    _, version = store.release_action_changes("sample", None, ())
    with store.transaction("sample") as state:
        if change == "trigger":
            state._connection.execute("DROP TRIGGER release_action_insert")
        elif change == "index":
            state._connection.execute("DROP INDEX release_action_changed")
        elif change == "user-version":
            state._connection.execute("PRAGMA user_version = 3")
        else:
            state._connection.execute("UPDATE release_action_clock SET revision = ? WHERE singleton = 1", (version.revision - 1,))
    connections = []
    connect = store._connect
    def observed(path, writable=False):
        connection = connect(path, writable)
        connections.append(connection)
        return connection
    monkeypatch.setattr(store, "_connect", observed)
    with pytest.raises(ValueError):
        with store.reading():
            store.release_action_changes("sample", version, ())
    assert store._readers.connections is None
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


def test_replaced_inode_forces_cold_validation(tmp_path):
    store = release_store(tmp_path / "old")
    _, version = store.release_action_changes("sample", None, ())
    replacement = release_store(tmp_path / "new")
    replacement.path("sample").replace(store.path("sample"))
    selected, after = store.release_action_changes("sample", version, ())
    assert len(selected) == 2
    assert (after.device, after.inode) != (version.device, version.inode)
    assert after.epoch != version.epoch


def test_changed_older_row_and_active_overlap_decode_once_in_ordinal_order(tmp_path):
    store = release_store(tmp_path)
    initial, version = store.release_action_changes("sample", None, ())
    ordinals = tuple(ordinal for ordinal, _ in initial)
    with store.transaction("sample") as state:
        state.action("alpha").target = "new"
    selected, _ = store.release_action_changes("sample", version, ordinals)
    assert [ordinal for ordinal, _ in selected] == list(ordinals)
    assert [action.action_id for _, action in selected] == ["alpha", "beta"]


def test_empty_release_history_and_empty_active_set_remain_empty(tmp_path):
    store = actions.Store(tmp_path / "empty")
    store.create(actions.Incident("sample", "owner", 0, STAMP, {}))
    selected, version = store.release_action_changes("sample", None, ())
    assert selected == []
    with store.transaction("sample") as state:
        state.facts["only-fact"] = 1
    selected, same = store.release_action_changes("sample", version, ())
    assert selected == [] and same == version


@pytest.mark.parametrize("history", [32, 10_000])
@pytest.mark.parametrize("kind", ["release", "ordinary"])
def test_native_writer_maintenance_is_per_row_not_history(tmp_path, history, kind):
    store = actions.Store(tmp_path / "writer-cost")
    store.create(actions.Incident("sample", "owner", 0, STAMP, {}))
    with store.transaction("sample") as state:
        state.actions.update({f"row:{index}": record(f"row:{index}") for index in range(history)})
        state.actions["ordinary"] = record("ordinary", "ordinary")
    key = "row:0" if kind == "release" else "ordinary"
    vm = [0]
    with store.transaction("sample") as state:
        connection = state._connection
        before = connection.execute("SELECT revision FROM release_action_clock WHERE singleton = 1").fetchone()[0]
        payload = json.dumps(asdict(record(key, kind)) | {"target": "changed"})
        def progress():
            vm[0] += 1
            return 0
        connection.set_progress_handler(progress, 1)
        connection.execute("UPDATE action SET record = ? WHERE action_key = ?", (payload, key))
        connection.set_progress_handler(None, 0)
        after = connection.execute("SELECT revision FROM release_action_clock WHERE singleton = 1").fetchone()[0]
        mapped = connection.execute("SELECT COUNT(*) FROM release_action_version").fetchone()[0]
    print(json.dumps({"release_history": history, "changed_kind": kind, "native_update_VM": vm[0], "revision_advanced": after > before, "version_rows": mapped, "sqlite": sqlite3.sqlite_version}))
    assert vm[0] < 1000
    assert (after > before) == (kind == "release")
    assert mapped == history
