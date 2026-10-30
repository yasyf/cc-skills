from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from long_running import actions
from long_running import sqlite_store

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def history(root, count=8, name="runner"):
    store = sqlite_store.Store(root)
    incident = actions.Incident(name, "root", 0, actions.stamp(NOW), {"nested": {"items": [1]}})
    for index in range(count):
        action = incident.accept(str(index), "send", "target", "owner", NOW)
        action.status = "accepted" if index == count - 1 else "verified"
        action.response = {"items": [index]}
    store.create(incident)
    return store


@pytest.mark.parametrize("count", [32, 10_000])
def test_cold_selected_reads_and_single_transition_are_history_bounded(tmp_path, monkeypatch, count):
    store = history(tmp_path, count)
    costs = {"loads": 0, "builds": 0, "encodes": 0, "opcodes": 0, "changed_rows": 0}
    decode, construct, encode = json.loads, actions.Action.load.__func__, sqlite_store._action_record
    connect, flush = sqlite_store.Store._connect, sqlite_store.Transaction.flush

    def load(value, *args, **kwargs):
        costs["loads"] += 1
        return decode(value, *args, **kwargs)

    def build(cls, record):
        costs["builds"] += 1
        return construct(cls, record)

    def record(action):
        costs["encodes"] += 1
        return encode(action)

    def opened(self, *args, **kwargs):
        connection = connect(self, *args, **kwargs)

        def progress():
            costs["opcodes"] += 1
            return 0

        connection.set_progress_handler(progress, 1)
        return connection

    def written(self):
        flush(self)
        costs["changed_rows"] += self._connection.total_changes

    monkeypatch.setattr(json, "loads", load)
    monkeypatch.setattr(actions.Action, "load", classmethod(build))
    monkeypatch.setattr(sqlite_store, "_action_record", record)
    monkeypatch.setattr(sqlite_store.Store, "_connect", opened)
    monkeypatch.setattr(sqlite_store.Transaction, "flush", written)
    for _ in range(50):
        assert sqlite_store.Store(tmp_path).ownership("runner") == ("root", 0)
        assert store.find_action("runner", str(count - 1)).status == "accepted"
        assert [item.action_id for item in store.actions("runner", "send", "accepted")] == [str(count - 1)]
    assert costs["loads"] == costs["builds"] == 100
    assert costs["opcodes"] < 50_000
    before = costs.copy()
    with store.transaction("runner") as incident:
        incident.start(str(count - 1), NOW)
    delta = {key: costs[key] - before[key] for key in costs}
    assert delta["loads"] == delta["builds"] == delta["encodes"] == delta["changed_rows"] == 1
    assert delta["opcodes"] < 4_000
    print(json.dumps({"history": count, "selected_150_queries": before, "one_transition": delta}))


def test_nested_mutations_and_read_results_remain_detached(tmp_path):
    store = history(tmp_path)
    with store.transaction("runner") as incident:
        incident.facts["nested"]["items"].append(2)
        incident.action("7").response["items"].append(8)
        incident.decide("choice", "which", NOW)
        incident.resolve("choice", NOW)
        incident.milestone("ready", "done", NOW)
    snapshot = store.load("runner")
    assert snapshot.facts["nested"]["items"] == [1, 2]
    assert snapshot.actions["7"].response == {"items": [7, 8]}
    assert snapshot.open_decisions() == []
    assert snapshot.reached("ready")["text"] == "done"
    snapshot.facts["nested"]["items"].append(3)
    snapshot.actions["7"].response["items"].append(9)
    assert store.load("runner").facts["nested"]["items"] == [1, 2]
    assert store.find_action("runner", "7").response == {"items": [7, 8]}


def test_index_selection_tracks_mutated_kind_and_delete_reinsert_order(tmp_path):
    store = history(tmp_path, 4)
    with store.transaction("runner") as incident:
        incident.action("0").kind = "other"
        assert [action.action_id for action in incident.of_kind("other")] == ["0"]
        assert [action.action_id for action in incident.of_kind("send")] == ["1", "2", "3"]
        old = incident.actions.pop("1")
        incident.actions["1"] = old
        incident.actions["new"] = incident.actions.pop("2")
        assert list(incident.actions) == ["0", "3", "1", "new"]
        assert [action.action_id for action in reversed(incident.actions.values())] == ["2", "1", "3", "0"]
        assert len(incident.actions) == 4
    snapshot = store.load("runner")
    assert list(snapshot.actions) == ["0", "3", "1", "new"]
    assert snapshot.actions["new"].action_id == "2"
    assert [action.action_id for action in store.actions("runner", "other")] == ["0"]


def test_replacement_and_field_assignment_preserve_domain_dump(tmp_path):
    store = history(tmp_path, 2)
    with store.transaction("runner") as incident:
        incident.facts = {"fresh": [1]}
        incident.actions = {"changed": incident.action("1")}
    snapshot = store.load("runner")
    assert snapshot.facts == {"fresh": [1]}
    assert list(snapshot.actions) == ["changed"]
    assert snapshot.dump() == asdict(snapshot)


def test_scoped_edits_return_loaded_dtos_and_no_retained_database_snapshot(tmp_path):
    store = history(tmp_path)
    with store.transaction("runner") as transaction:
        selected = transaction.action("7")
    assert selected.status == "accepted"
    with store.transaction("runner") as incident:
        assert incident.owner == "root"
    assert incident.owner == "root"
    with pytest.raises(RuntimeError, match="active edit context"):
        incident.action("0")
    with pytest.raises(RuntimeError, match="active edit context"):
        _ = incident.facts
    assert store.transfer("runner", 0, "next").actions["7"].status == "accepted"
    assert store.ack("runner", "next").actions["7"].status == "accepted"


def test_generation_input_revision_and_rollback(tmp_path):
    store = history(tmp_path)
    before = store.load("runner").dump()
    with pytest.raises(ValueError):
        with store.transaction("runner") as incident:
            incident.facts["wrong"] = True
            incident.start("7", NOW)
            raise ValueError("abort")
    assert store.load("runner").dump() == before
    with store.transaction("runner", inputs=True) as incident:
        incident.facts["new"] = True
    assert incident.input_revision == 2
    store.transfer("runner", 0, "peer")
    store.ack("runner", "peer")
    with pytest.raises(actions.StaleGeneration):
        with store.transaction("runner", generation=0):
            pytest.fail("old generation admitted")
    with store.transaction("runner", generation=1) as incident:
        incident.start("7", NOW)
    assert store.find_action("runner", "7").status == "started"


def test_explicit_snapshot_detaches_nested_values_without_json_type_coercion(tmp_path):
    store = history(tmp_path)
    with store.transaction("runner") as transaction:
        transaction.facts["pair"] = (1, 2)
        transaction.action("7").response["pair"] = (3, 4)
        snapshot = transaction.snapshot()
        transaction.facts["nested"]["items"].append(2)
        transaction.action("7").response["items"].append(8)
    assert isinstance(snapshot, actions.Incident)
    assert isinstance(snapshot.actions, dict)
    assert snapshot.facts["pair"] == (1, 2)
    assert snapshot.actions["7"].response["pair"] == (3, 4)
    assert snapshot.facts["nested"]["items"] == [1]
    assert snapshot.actions["7"].response["items"] == [7]
    assert store.load("runner").facts["pair"] == [1, 2]
    assert store.find_action("runner", "7").response["items"] == [7, 8]


def _contend(root, ready, start):
    store = sqlite_store.Store(root)
    ready.put(True)
    assert start.wait(10)
    for _ in range(5):
        with store.transaction("runner", generation=0) as incident:
            incident.facts["count"] = incident.facts.get("count", 0) + 1
            incident.accept("idempotent", "send", "target", None, NOW)


def test_concurrent_processes_serialize_same_incident_and_accept_idempotently(tmp_path):
    store = history(tmp_path, 0)
    context = multiprocessing.get_context("spawn")
    ready, start = context.Queue(), context.Event()
    children = [context.Process(target=_contend, args=(tmp_path, ready, start)) for _ in range(2)]
    for child in children:
        child.start()
    try:
        assert ready.get(timeout=15) and ready.get(timeout=15)
        start.set()
        for child in children:
            child.join(20)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(5)
    state = store.load("runner")
    assert state.facts["count"] == 10
    assert list(state.actions) == ["idempotent"]


def test_another_incident_commits_while_first_has_uncommitted_writer(tmp_path):
    first = history(tmp_path, name="first")
    second = history(tmp_path, name="second")
    with first.transaction("first") as left:
        left.facts["pending"] = True
        with second.transaction("second") as right:
            right.facts["committed"] = True
        assert second.load("second").facts["committed"]
        assert "pending" not in first.load("first").facts
    assert first.load("first").facts["pending"]


def _crash_after_started(root, receipt):
    store = sqlite_store.Store(root)
    with store.transaction("runner", generation=0) as incident:
        incident.start("0", NOW)
    receipt.write_text(store.find_action("runner", "0").status)
    os._exit(7)


def test_started_is_durable_before_effect_and_crash_is_never_retried(tmp_path):
    store = history(tmp_path, 1)
    receipt = tmp_path / "effect"
    child = multiprocessing.get_context("spawn").Process(target=_crash_after_started, args=(tmp_path, receipt))
    child.start()
    child.join(20)
    assert child.exitcode == 7
    assert receipt.read_text() == "started"
    with store.transaction("runner", generation=0) as incident:
        incident.lose("0")
        assert incident.accept("0", "send", "ignored", None, NOW).status == "unverifiable"
        with pytest.raises(ValueError, match="not accepted"):
            incident.start("0", NOW)
    assert store.find_action("runner", "0").status == "unverifiable"


def test_cli_json_contract_and_schema_durability(tmp_path, capsys):
    store = history(tmp_path, 2)
    assert actions.main(["show", "--incident", "runner", "--json"], store) == 0
    assert json.loads(capsys.readouterr().out) == store.load("runner").dump()
    assert actions.main(["list", "--json"], store) == 0
    assert json.loads(capsys.readouterr().out) == [store.load("runner").dump()]
    connection = store._connect(store.path("runner"), writable=True)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


@pytest.mark.parametrize("name", ["", "../escape", "nested/id", "nul\x00id"])
def test_incident_paths_do_not_expand_store_authority(tmp_path, name):
    with pytest.raises(ValueError):
        sqlite_store.Store(tmp_path).path(name)


def test_missing_corrupt_or_unknown_format_never_reads_legacy_json(tmp_path):
    store = sqlite_store.Store(tmp_path)
    (tmp_path / "runner.json").write_text("{}")
    with pytest.raises(SystemExit, match="no incident"):
        store.load("runner")
    assert store.ids() == []
    history(tmp_path)
    with sqlite3.connect(store.path("runner")) as connection:
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(ValueError, match="unsupported action store version"):
        store.ownership("runner")
