import json
import sqlite3

import pytest

from test_desk_runner import actions, config, runner_module, shell
from test_open_cut_clock_noop import clock_runner, initialized


def release_history_runner(shell, config, tmp_path, history):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = {f"release:{pr}": {"ended": "done", "since": "kept"} for pr in range(1, history + 1)}
    runner = clock_runner(shell, config, tmp_path, initial)
    stamp = actions.stamp(shell.now())
    records = {}
    for pr in range(1, history + 1):
        action = actions.Action(runner_module.LANDING, f"release-{pr}", "release", json.dumps({"pr": pr, "components": ["sand"], "sha": "94d61b91951"}), None, 0, 0, stamp, status="completed", response={"at": stamp})
        records[action.action_id] = action
    runner.book.edit(runner_module.LANDING, lambda state: state.actions.update(records))
    return runner


@pytest.mark.parametrize("history", [32, 10_000])
def test_completed_release_history_materialization_control(shell, config, tmp_path, monkeypatch, history):
    runner = release_history_runner(shell, config, tmp_path, history)
    store = runner.book.store
    connect, loads, load = store._connect, json.loads, actions.Action.load
    counts = {"ro": 0, "rw": 0, "action_dtos": 0, "decoded_bytes": 0, "vm": 0}
    connections = []

    def opened(path, writable=False):
        connection = connect(path, writable)
        connections.append(connection)
        counts["rw" if writable else "ro"] += 1
        def progress():
            counts["vm"] += 1
            return 0
        connection.set_progress_handler(progress, 1)
        return connection

    def decoded(value, *args, **kwargs):
        counts["decoded_bytes"] += len(value.encode())
        return loads(value, *args, **kwargs)

    def loaded(cls, record):
        counts["action_dtos"] += 1
        return load(record)

    monkeypatch.setattr(store, "_connect", opened)
    monkeypatch.setattr(json, "loads", decoded)
    monkeypatch.setattr(actions.Action, "load", classmethod(loaded))
    with store.reading():
        for _ in range(32):
            runner.cci_tail.forget()
            runner.open_cut_clocks()
        measured = dict(counts)
        print(json.dumps({"completed_release_actions": history, "existing_clock_keys": history, "polls": 32, "counts": measured, "scope": "immutable794 actualcaller diagnostic; no hostpopulation or physicalIO inference"}))
        assert measured["action_dtos"] == 32 * history, f"completed release control materialized {measured['action_dtos']} DTOs for {history} actions in 32 polls"
        assert measured["ro"] == 2 and measured["rw"] == 0
        assert all(connection.total_changes == 0 and not connection.in_transaction for connection in connections)
        assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10
        for pr in (1, history):
            assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, f"release:{pr}"]) == {"ended": "done", "since": "kept"}
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_completed_release_history_keeps_error_order_for_existing_clocks(shell, config, tmp_path):
    runner = release_history_runner(shell, config, tmp_path, 32)
    with runner.book.store.transaction(runner_module.LANDING) as state:
        first = state.actions["release-1"].target
        state.actions["release-1"].target = "{"
        state.actions["release-2"].response = None
        state.actions["release-10"].target = "{"
    with pytest.raises(json.JSONDecodeError):
        runner.open_cut_clocks()
    with runner.book.store.transaction(runner_module.LANDING) as state:
        state.actions["release-1"].target = first
    with pytest.raises(TypeError):
        runner.open_cut_clocks()
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10
    assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:1"]) == {"ended": "done", "since": "kept"}


def test_completed_release_history_preserves_cancellation_identity_and_closure(shell, config, tmp_path, monkeypatch):
    runner = release_history_runner(shell, config, tmp_path, 32)
    store = runner.book.store
    error = runner_module.StopRequested("private completed-release cancellation")
    connections = []
    connect = store._connect

    def opened(path, writable=False):
        assert not writable
        connection = connect(path, writable)
        connections.append(connection)
        return connection

    def cancelled(*args, **kwargs):
        raise error

    monkeypatch.setattr(store, "_connect", opened)
    monkeypatch.setattr(runner_module.cci, "records", cancelled)
    with store.reading():
        with pytest.raises(runner_module.StopRequested) as raised:
            runner.open_cut_clocks()
        assert raised.value is error
        assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10
        assert len(connections) == 1 and not connections[0].in_transaction and connections[0].total_changes == 0
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")
