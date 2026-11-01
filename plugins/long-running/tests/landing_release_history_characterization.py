import json
import sqlite3

import pytest

from test_desk_runner import actions, config, landing_of, runner_module, shell


def release_history(shell, config, tmp_path, history=4, mode="retired", active_first=False):
    landing = landing_of(shell, config, tmp_path)
    stamp = actions.stamp(shell.now())
    target = json.dumps({"pr": 41, "components": ["sand"], "sha": "94d61b91951", "landed_at": stamp})
    retired = {"builds": ["build/1"], "targets": ["sand"], "release": "1", "live": 123}
    records = {}
    for index in range(history):
        key = f"release:history:{index}"
        records[key] = actions.Action(runner_module.LANDING, key, "release", target, None, 0, 0, stamp, status="verified" if mode == "retired" else "completed", verification_receipt=dict(retired) if mode == "retired" else None)
    for name, status in (("accepted", "accepted"), ("completed", "completed"), ("publishing", "verified")):
        key = f"release:active:{name}"
        receipt = {"builds": ["build/2"], "targets": ["sand"], "release": "2"} if name == "publishing" else None
        records[key] = actions.Action(runner_module.LANDING, key, "release", target, None, 0, 0, stamp, status=status, verification_receipt=receipt)
    if active_first:
        records = {key: value for key, value in records.items() if ":active:" in key} | {key: value for key, value in records.items() if ":history:" in key}
    def seed(state):
        state.facts[runner_module.RELEASE_READ] = []
        state.actions.update(records)
    landing.book.edit(runner_module.LANDING, seed)
    return landing


def dispatched(monkeypatch, landing):
    calls = []
    monkeypatch.setattr(landing, "press_release", lambda action: calls.append(("press", action.action_id)))
    monkeypatch.setattr(landing, "publish_line", lambda action: calls.append(("publish", action.action_id)))
    return calls


@pytest.mark.parametrize("history", [32, 10_000])
@pytest.mark.parametrize("mode", ["retired", "completed"])
def test_immutable801_per_batch_release_history_cost(shell, config, tmp_path, monkeypatch, history, mode):
    landing = release_history(shell, config, tmp_path, history, mode)
    store = landing.book.store
    writer = actions.Store(store.root)
    connect, loads, load = store._connect, json.loads, actions.Action.load
    counts = {"ro": 0, "rw": 0, "action_dtos": 0, "decoded_bytes": 0, "vm": 0, "read_transactions": 0}
    connections = []
    observing = True
    calls = dispatched(monkeypatch, landing)

    def opened(path, writable=False):
        connection = connect(path, writable)
        counts["rw" if writable else "ro"] += 1
        connections.append(connection)
        def progress():
            if observing:
                counts["vm"] += 1
            return 0
        def traced(statement):
            if observing and statement == "BEGIN":
                counts["read_transactions"] += 1
        connection.set_progress_handler(progress, 1)
        connection.set_trace_callback(traced)
        return connection

    def decoded(value, *args, **kwargs):
        if observing:
            counts["decoded_bytes"] += len(value.encode())
        return loads(value, *args, **kwargs)

    def loaded(cls, record):
        if observing:
            counts["action_dtos"] += 1
        return load(record)

    monkeypatch.setattr(store, "_connect", opened)
    monkeypatch.setattr(json, "loads", decoded)
    monkeypatch.setattr(actions.Action, "load", classmethod(loaded))
    expected = [("press", f"release:history:{index}") for index in range(history)] if mode == "completed" else []
    expected += [("press", "release:active:accepted"), ("press", "release:active:completed"), ("publish", "release:active:publishing")]
    external_writes = 0
    try:
        for index in range(32):
            if index % 8 == 0:
                observing = False
                with writer.transaction(runner_module.LANDING) as state:
                    state.facts["unrelated-history-probe"] = index
                external_writes += 1
                observing = True
            with store.reading(), landing.build_cache.book.store.reading():
                landing.releases({})
                assert calls == expected
                calls.clear()
                assert connections[-1].total_changes == 0 and not connections[-1].in_transaction
            assert store._readers.connections is None
            assert landing.build_cache.book.store._readers.connections is None
        print(json.dumps({"release_history": history, "history_mode": mode, "per_batch_calls": 32, "counts": counts, "external_unrelated_writes": external_writes, "required_dispatches": 32 * len(expected), "retired_history_dispatches": 0 if mode == "retired" else 32 * history, "sqlite": sqlite3.sqlite_version, "scope": "immutable801 actual Landing.releases with exact side-batch reading scopes; external callee dispatch recorded, no live IO attribution"}))
        assert counts["action_dtos"] == 32 * (history + 3)
        assert counts["ro"] == 32 and counts["rw"] == 0
        assert counts["read_transactions"] == 64
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
    finally:
        landing.close()


@pytest.mark.parametrize("malformed", ["extra", "missing", "nonobject", "invalid-json"])
def test_retired_dto_failure_precedes_every_external_dispatch(shell, config, tmp_path, monkeypatch, malformed):
    landing = release_history(shell, config, tmp_path, active_first=True)
    calls = dispatched(monkeypatch, landing)
    try:
        with landing.book.store.transaction(runner_module.LANDING) as state:
            row = state._connection.execute("SELECT record FROM action WHERE action_key = ?", ("release:history:3",)).fetchone()
            record = json.loads(row[0])
            if malformed == "extra":
                record["unrecognized_dto_field"] = True
            elif malformed == "missing":
                record.pop("target")
            else:
                record = None
            raw = "{" if malformed == "invalid-json" else json.dumps(record)
            state._connection.execute("UPDATE action SET record = ? WHERE action_key = ?", (raw, "release:history:3"))
        error_class = json.JSONDecodeError if malformed == "invalid-json" else TypeError
        with pytest.raises(error_class):
            with landing.book.store.reading():
                landing.releases({})
        assert calls == []
        assert landing.book.store._readers.connections is None
    finally:
        landing.close()


def test_old_ordinal_receipt_change_is_visible_next_batch(shell, config, tmp_path, monkeypatch):
    landing = release_history(shell, config, tmp_path)
    calls = dispatched(monkeypatch, landing)
    writer = actions.Store(landing.book.store.root)
    try:
        with landing.book.store.reading():
            landing.releases({})
        calls.clear()
        with writer.transaction(runner_module.LANDING) as state:
            state.action("release:history:0").verification_receipt.pop("live")
        with landing.book.store.reading():
            landing.releases({})
        assert calls == [("publish", "release:history:0"), ("press", "release:active:accepted"), ("press", "release:active:completed"), ("publish", "release:active:publishing")]
    finally:
        landing.close()


def test_external_progress_is_checked_without_database_change(shell, config, tmp_path, monkeypatch):
    landing = release_history(shell, config, tmp_path)
    seen = []
    state = {"external": "before"}
    monkeypatch.setattr(landing, "press_release", lambda action: seen.append((action.action_id, state["external"])))
    monkeypatch.setattr(landing, "publish_line", lambda action: seen.append((action.action_id, state["external"])))
    try:
        for value in ("before", "after"):
            state["external"] = value
            with landing.book.store.reading():
                landing.releases({})
        assert seen == [(f"release:active:{name}", value) for value in ("before", "after") for name in ("accepted", "completed", "publishing")]
    finally:
        landing.close()


@pytest.mark.parametrize("error_class", [RuntimeError, runner_module.StopRequested])
def test_release_callee_error_identity_closes_read_scope(shell, config, tmp_path, monkeypatch, error_class):
    landing = release_history(shell, config, tmp_path)
    marker = error_class("release-progress-boundary")
    def fail(action):
        raise marker
    monkeypatch.setattr(landing, "press_release", fail)
    try:
        with pytest.raises(error_class) as caught:
            with landing.book.store.reading():
                landing.releases({})
        assert caught.value is marker
        assert landing.book.store._readers.connections is None
    finally:
        landing.close()


@pytest.mark.parametrize("receipt", [["nonmapping"], 1, "nonmapping"])
def test_verified_receipt_shape_failure_remains_visible(shell, config, tmp_path, monkeypatch, receipt):
    landing = release_history(shell, config, tmp_path, active_first=True)
    calls = dispatched(monkeypatch, landing)
    try:
        with landing.book.store.transaction(runner_module.LANDING) as state:
            state.action("release:history:0").verification_receipt = receipt
        with pytest.raises(AttributeError):
            landing.releases({})
        assert calls == [("press", "release:active:accepted"), ("press", "release:active:completed"), ("publish", "release:active:publishing")]
    finally:
        landing.close()


@pytest.mark.parametrize("missing", [True, False])
def test_release_read_initialization_and_wrong_type_precede_actions(shell, config, tmp_path, monkeypatch, missing):
    landing = release_history(shell, config, tmp_path)
    calls = dispatched(monkeypatch, landing)
    try:
        with landing.book.store.transaction(runner_module.LANDING) as state:
            if missing:
                state.facts.pop(runner_module.RELEASE_READ)
            else:
                state.facts[runner_module.RELEASE_READ] = 7
        if missing:
            landing.releases({})
            assert landing.book.fact(runner_module.LANDING, runner_module.RELEASE_READ) == []
        else:
            with pytest.raises(TypeError):
                landing.releases({})
        assert calls == []
    finally:
        landing.close()


@pytest.mark.parametrize("status", ["failed", "verified"])
def test_inactive_target_remains_unparsed(shell, config, tmp_path, monkeypatch, status):
    landing = release_history(shell, config, tmp_path)
    calls = dispatched(monkeypatch, landing)
    try:
        with landing.book.store.transaction(runner_module.LANDING) as state:
            action = state.action("release:history:0")
            action.target = "not-json"
            action.status = status
        landing.releases({})
        assert calls == [("press", "release:active:accepted"), ("press", "release:active:completed"), ("publish", "release:active:publishing")]
    finally:
        landing.close()
