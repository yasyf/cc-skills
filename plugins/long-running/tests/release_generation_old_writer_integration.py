import json
from contextlib import closing
from dataclasses import asdict

import pytest

from blocker_index_old_writer_integration import old_actions
from landing_release_history_characterization import actions, config, dispatched, release_history, runner_module, shell
from long_running.sqlite_store import _RELEASE_ACTION_DDL


def provision(store, container):
    with store.transaction(container) as state:
        for statement in _RELEASE_ACTION_DDL:
            state._connection.execute(statement)


def test_immutable801_writer_updates_current_release_versions(shell, config, tmp_path, monkeypatch):
    landing = release_history(shell, config, tmp_path)
    calls = dispatched(monkeypatch, landing)
    old = old_actions()
    writer = old.Store(landing.book.store.root)
    try:
        with landing.book.store.reading():
            landing.releases({})
        calls.clear()
        with writer.transaction(runner_module.LANDING) as state:
            state.actions["release:history:0"].verification_receipt.pop("live")
        with landing.book.store.reading():
            landing.releases({})
        assert calls[0] == ("publish", "release:history:0")
        with writer.transaction(runner_module.LANDING) as state:
            state._connection.execute("DELETE FROM action WHERE action_key = ?", ("release:active:accepted",))
        calls.clear()
        with landing.book.store.reading():
            landing.releases({})
        assert ("press", "release:active:accepted") not in calls
    finally:
        landing.close()


def test_immutable801_creator_requires_explicit_atomic_provisioning(shell, config, tmp_path, monkeypatch):
    old = old_actions()
    writer = old.Store(tmp_path / "old")
    stamp = old.stamp(shell.now())
    incident = old.Incident(runner_module.LANDING, "owner", 0, stamp, {runner_module.RELEASE_READ: []})
    incident.actions["release:old"] = old.Action(runner_module.LANDING, "release:old", "release", "{}", None, 0, 0, stamp)
    writer.create(incident)
    store = actions.Store(writer.root)
    before = store.load(runner_module.LANDING).dump()
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    landing = runner_module.Landing(runner, runner.config.landing)
    calls = dispatched(monkeypatch, landing)
    try:
        with pytest.raises(ValueError, match="release action tracking schema"):
            landing.releases({})
        assert calls == [] and landing.release_action_proof is None
        provision(store, runner_module.LANDING)
        assert store.load(runner_module.LANDING).dump() == before
        with store.reading():
            landing.releases({})
        assert calls == [("press", "release:old")]
        with writer.transaction(runner_module.LANDING) as state:
            state.actions["release:old"].response = {"old-writer": True}
        calls.clear()
        with store.reading():
            landing.releases({})
        assert calls == [("press", "release:old")]
    finally:
        landing.close()


def test_old_prepared_connection_observes_new_native_triggers(shell, tmp_path):
    old = old_actions()
    writer = old.Store(tmp_path / "old")
    stamp = old.stamp(shell.now())
    incident = old.Incident("sample", "owner", 0, stamp, {})
    action = old.Action("sample", "release:old", "release", "{}", None, 0, 0, stamp)
    incident.actions[action.action_id] = action
    writer.create(incident)
    store = actions.Store(writer.root)
    query = "UPDATE action SET record = ? WHERE action_key = ?"
    with closing(writer._connect(writer.path("sample"), writable=True)) as connection:
        connection.execute(query, (json.dumps(asdict(action)), action.action_id))
        connection.commit()
        provision(store, "sample")
        _, version = store.release_action_changes("sample", None, ())
        changed = asdict(action) | {"target": "new old-writer payload"}
        connection.execute(query, (json.dumps(changed), action.action_id))
        connection.commit()
        selected, after = store.release_action_changes("sample", version, ())
        assert [(current.action_id, current.target) for _, current in selected] == [(action.action_id, "new old-writer payload")]
        assert after.revision > version.revision and after.epoch == version.epoch
