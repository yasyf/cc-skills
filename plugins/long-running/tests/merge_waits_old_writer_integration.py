import sqlite3

import pytest

from blocker_index_old_writer_integration import old_actions
from test_desk_runner import actions, config, landing_of, runner_module, shell
from test_merge_wait_fact_cost import wait_entry


def test_immutable_old_creator_and_writer_need_no_schema_change(shell, tmp_path):
    old = old_actions()
    writer = old.Store(tmp_path)
    incident = old.Incident("old-waits", "old-owner", 0, old.stamp(shell.now()), {})
    incident.facts["a/~"] = {"1": wait_entry()}
    writer.create(incident)
    store = actions.Store(tmp_path)
    with sqlite3.connect(store.path("old-waits")) as observer:
        schema = observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall()
    with store.reading():
        assert store.fact_children("old-waits", "a/~", ["1", "missing"]) == {"1": wait_entry()}
        with writer.transaction("old-waits") as state:
            state.facts["a/~"]["1"]["rows"].append("old-writer")
        assert store.fact_children("old-waits", "a/~", ["1"])["1"]["rows"] == ["reserved", "old-writer"]
        with pytest.raises(ValueError, match="private rollback"):
            with writer.transaction("old-waits") as state:
                state.facts["a/~"]["1"]["rows"].append("rollback")
                raise ValueError("private rollback")
        assert store.fact_children("old-waits", "a/~", ["1"])["1"]["rows"] == ["reserved", "old-writer"]
        with writer.transaction("old-waits") as state:
            del state.facts["a/~"]["1"]
        assert store.fact_children("old-waits", "a/~", ["1"]) == {}
    with sqlite3.connect(store.path("old-waits")) as observer:
        assert observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall() == schema


def test_actual_record_waits_observes_immutable_old_writer(shell, config, tmp_path, monkeypatch):
    old = old_actions()
    landing = landing_of(shell, config, tmp_path)
    monkeypatch.setattr(landing, "reserved_touches", lambda pr, head: [("reserved", "unused")])
    store = landing.book.store
    writer = old.Store(store.root)
    with writer.transaction(runner_module.LANDING) as state:
        state.facts[runner_module.MERGE_WAITS] = {"other": ["kept"], "1": wait_entry()}
    with store.reading():
        landing.record_waits({"1": "a" * 40}, {"1": ["ignored"]})
        with writer.transaction(runner_module.LANDING) as state:
            state.facts[runner_module.MERGE_WAITS]["1"]["rows"] = ["changed"]
        landing.record_waits({"1": "a" * 40}, {"1": ["ignored"]})
    assert store.fact(runner_module.LANDING, runner_module.MERGE_WAITS) == {"other": ["kept"], "1": wait_entry()}
