import json
import sqlite3
import sys

import pytest

from landing_release_history_characterization import (
    actions,
    config,
    dispatched,
    release_history,
    runner_module,
    shell,
    test_external_progress_is_checked_without_database_change,
    test_inactive_target_remains_unparsed,
    test_old_ordinal_receipt_change_is_visible_next_batch,
    test_release_callee_error_identity_closes_read_scope,
    test_release_read_initialization_and_wrong_type_precede_actions,
    test_retired_dto_failure_precedes_every_external_dispatch,
    test_verified_receipt_shape_failure_remains_visible,
)


@pytest.mark.parametrize(
    "history,write,frequency",
    [(history, write, frequency) for history in (32, 10_000) for write, frequency in (("facts", "four"), ("facts", "every"), ("other", "every"), ("inactive", "every"), ("active", "four"))],
    ids=[f"history-{history}-{write}-{frequency}" for history in (32, 10_000) for write, frequency in (("facts", "four"), ("facts", "every"), ("other", "every"), ("inactive", "every"), ("active", "four"))],
)
def test_warm_release_batches_decode_only_changed_and_active(shell, config, tmp_path, monkeypatch, history, write, frequency):
    landing = release_history(shell, config, tmp_path, history)
    store = landing.book.store
    writer = actions.Store(store.root)
    stamp = actions.stamp(shell.now())
    with writer.transaction(runner_module.LANDING) as state:
        state.actions["other"] = actions.Action(runner_module.LANDING, "other", "ordinary", "old", None, 0, 0, stamp)
    connect, loads, load = store._connect, json.loads, actions.Action.load
    counts = {"ro": 0, "rw": 0, "action_dtos": 0, "decoded_bytes": 0, "vm": 0, "read_transactions": 0}
    connections, queries = [], []
    observing = True
    calls = dispatched(monkeypatch, landing)
    def opened(path, writable=False):
        connection = connect(path, writable)
        connections.append(connection)
        if observing:
            counts["rw" if writable else "ro"] += 1
        def progress():
            if observing:
                counts["vm"] += 1
            return 0
        def traced(statement):
            if observing and statement == "BEGIN":
                counts["read_transactions"] += 1
            if observing and statement.startswith("SELECT ordinal, record FROM action") and "release_action_version" in statement:
                queries.append(statement)
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
    expected_calls = [("press", "release:active:accepted"), ("press", "release:active:completed"), ("publish", "release:active:publishing")]
    try:
        with store.reading(), landing.build_cache.book.store.reading():
            landing.releases({})
        assert calls == expected_calls
        calls.clear()
        cold = dict(counts)
        counts.update({name: 0 for name in counts})
        writes = 0
        for index in range(32):
            if frequency == "every" or index % 8 == 0:
                observing = False
                with writer.transaction(runner_module.LANDING) as state:
                    if write == "facts":
                        state.facts["unrelated-history-probe"] = index
                    elif write == "other":
                        state.action("other").target = str(index)
                    elif write == "inactive":
                        state.action(f"release:history:{index % history}").response = {"tick": index}
                    else:
                        state.action("release:active:accepted").response = {"tick": index}
                writes += 1
                observing = True
            with store.reading(), landing.build_cache.book.store.reading():
                landing.releases({})
                assert calls == expected_calls
                calls.clear()
                assert connections[-1].total_changes == 0 and not connections[-1].in_transaction
            assert store._readers.connections is None
            assert landing.build_cache.book.store._readers.connections is None
        measured = dict(counts)
        print(json.dumps({"release_history": history, "write": write, "frequency": frequency, "polls": 32, "cold": cold, "warm": measured, "external_writes": writes, "required_dispatches": 96, "sqlite": sqlite3.sqlite_version}))
        expected_dtos = 96 + (writes if write == "inactive" else 0)
        assert measured["action_dtos"] == expected_dtos, f"warm release-side batches materialized {measured['action_dtos']} DTOs for {history} retired actions in 32 polls"
        assert measured["ro"] == 32 and measured["rw"] == 0 and measured["read_transactions"] == 64
        assert measured["decoded_bytes"] < 32 * 8192 and measured["vm"] < 32 * 1500
        assert cold["action_dtos"] == history + 3
        assert len(queries) == 32
        observing = False
        with store._read(runner_module.LANDING) as connection:
            plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + queries[-1])]
            mapped = connection.execute("SELECT COUNT(*) FROM release_action_version").fetchone()[0]
        print(json.dumps({"release_history": history, "write": write, "actual_unforced_plan": plan, "version_rows": mapped}))
        assert any("release_action_changed" in step for step in plan)
        assert mapped == history + 3
        assert len(landing.release_action_proof[1]) == 3
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
    finally:
        landing.close()
    assert landing.release_action_proof is None


@pytest.mark.parametrize("warm", [False, True])
def test_inactive_integer_decode_error_precedes_earlier_active_callback(shell, config, tmp_path, monkeypatch, warm):
    landing = release_history(shell, config, tmp_path, active_first=True)
    calls = dispatched(monkeypatch, landing)
    limit = sys.get_int_max_str_digits()
    assert limit > 0
    try:
        if warm:
            with landing.book.store.reading():
                landing.releases({})
            calls.clear()
        with landing.book.store.transaction(runner_module.LANDING) as state:
            record = state._connection.execute("SELECT record FROM action WHERE action_key = ?", ("release:history:3",)).fetchone()[0]
            decoded = json.loads(record)
            decoded["response"] = {"unread": "OVERSIZED_DECIMAL_LITERAL"}
            raw = json.dumps(decoded).replace('"OVERSIZED_DECIMAL_LITERAL"', "1" * (limit + 1))
            state._connection.execute("UPDATE action SET record = ? WHERE action_key = ?", (raw, "release:history:3"))
        with pytest.raises(ValueError, match="integer string conversion"):
            landing.book.actions(runner_module.LANDING, kind="release")
        with pytest.raises(ValueError, match="integer string conversion"):
            with landing.book.store.reading():
                landing.releases({})
        assert calls == []
        assert landing.release_action_proof is None
        assert landing.book.store._readers.connections is None
    finally:
        landing.close()


def test_callee_failure_after_warm_proof_forces_full_validation(shell, config, tmp_path, monkeypatch):
    landing = release_history(shell, config, tmp_path)
    dispatched(monkeypatch, landing)
    marker = runner_module.StopRequested("release stop")
    try:
        with landing.book.store.reading():
            landing.releases({})
        def stop(action):
            raise marker
        monkeypatch.setattr(landing, "press_release", stop)
        with pytest.raises(runner_module.StopRequested) as caught:
            with landing.book.store.reading():
                landing.releases({})
        assert caught.value is marker
        assert landing.release_action_proof is None
        loaded = []
        load = actions.Action.load
        def observed(cls, record):
            loaded.append(record["action_id"])
            return load(record)
        monkeypatch.setattr(actions.Action, "load", classmethod(observed))
        dispatched(monkeypatch, landing)
        with landing.book.store.reading():
            landing.releases({})
        assert len(loaded) == 7
    finally:
        landing.close()


def test_writer_after_returned_snapshot_does_not_relabel_validation(shell, config, tmp_path, monkeypatch):
    landing = release_history(shell, config, tmp_path)
    calls = dispatched(monkeypatch, landing)
    writer = actions.Store(landing.book.store.root)
    original = landing.book.release_action_changes
    pending = [True]
    def snapshot(*args, **kwargs):
        result = original(*args, **kwargs)
        if pending[0]:
            pending[0] = False
            with writer.transaction(runner_module.LANDING) as state:
                state.action("release:history:0").verification_receipt.pop("live")
        return result
    monkeypatch.setattr(landing.book, "release_action_changes", snapshot)
    try:
        with landing.book.store.reading():
            landing.releases({})
        assert ("publish", "release:history:0") not in calls
        calls.clear()
        with landing.book.store.reading():
            landing.releases({})
        assert calls[0] == ("publish", "release:history:0")
    finally:
        landing.close()


def test_writer_after_clock_read_stays_outside_the_same_snapshot(shell, config, tmp_path, monkeypatch):
    landing = release_history(shell, config, tmp_path)
    calls = dispatched(monkeypatch, landing)
    store = landing.book.store
    writer = actions.Store(store.root)
    connect = store._connect
    pending = [True]
    class SnapshotConnection:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def execute(self, statement, *args):
            result = self.connection.execute(statement, *args)
            if pending[0] and statement == "SELECT epoch, revision FROM release_action_clock WHERE singleton = 1":
                pending[0] = False
                with writer.transaction(runner_module.LANDING) as state:
                    state.action("release:history:0").verification_receipt.pop("live")
            return result
    monkeypatch.setattr(store, "_connect", lambda path, writable=False: SnapshotConnection(connect(path, writable)))
    try:
        with store.reading():
            landing.releases({})
        assert not pending[0]
        assert ("publish", "release:history:0") not in calls
        calls.clear()
        with store.reading():
            landing.releases({})
        assert calls[0] == ("publish", "release:history:0")
    finally:
        landing.close()


@pytest.mark.parametrize("malformed", ["extra", "missing", "nonobject", "invalid-json"])
def test_changed_inactive_dto_error_still_precedes_active_dispatch(shell, config, tmp_path, monkeypatch, malformed):
    landing = release_history(shell, config, tmp_path, active_first=True)
    calls = dispatched(monkeypatch, landing)
    try:
        with landing.book.store.reading():
            landing.releases({})
        calls.clear()
        with landing.book.store.transaction(runner_module.LANDING) as state:
            raw = state._connection.execute("SELECT record FROM action WHERE action_key = ?", ("release:history:3",)).fetchone()[0]
            value = json.loads(raw)
            if malformed == "extra":
                value["unrecognized_dto_field"] = True
            elif malformed == "missing":
                value.pop("target")
            else:
                value = None
            raw = "{" if malformed == "invalid-json" else json.dumps(value)
            state._connection.execute("UPDATE action SET record = ? WHERE action_key = ?", (raw, "release:history:3"))
        error_class = json.JSONDecodeError if malformed == "invalid-json" else TypeError
        with pytest.raises(error_class):
            landing.book.actions(runner_module.LANDING, kind="release")
        with pytest.raises(error_class):
            with landing.book.store.reading():
                landing.releases({})
        assert calls == []
        assert landing.release_action_proof is None
    finally:
        landing.close()


def test_all_active_work_is_preserved_under_small_bind_limit(shell, config, tmp_path, monkeypatch):
    landing = release_history(shell, config, tmp_path, 1500, "completed")
    calls = dispatched(monkeypatch, landing)
    store = landing.book.store
    connect = store._connect
    def limited(path, writable=False):
        connection = connect(path, writable)
        if not writable:
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 16)
        return connection
    monkeypatch.setattr(store, "_connect", limited)
    try:
        for _ in range(2):
            with store.reading():
                landing.releases({})
            assert len(calls) == 1503
            assert calls[0] == ("press", "release:history:0")
            assert calls[-1] == ("publish", "release:active:publishing")
            calls.clear()
        assert len(landing.release_action_proof[1]) == 1503
    finally:
        landing.close()


@pytest.mark.parametrize("shape", ["duplicate-status", "duplicate-receipt", "escaped-status", "unicode", "utf16", "falsey-receipt"])
def test_changed_raw_json_preserves_python_decoding_and_receipt_semantics(shell, config, tmp_path, monkeypatch, shape):
    landing = release_history(shell, config, tmp_path)
    calls = dispatched(monkeypatch, landing)
    try:
        with landing.book.store.reading():
            landing.releases({})
        calls.clear()
        with landing.book.store.transaction(runner_module.LANDING) as state:
            value = json.loads(state._connection.execute("SELECT record FROM action WHERE action_key = ?", ("release:history:0",)).fetchone()[0])
            value["verification_receipt"] = {"builds": ["build/1"], "live": None}
            value["target"] = json.dumps({"components": ["café", "🦀"]}, ensure_ascii=False)
            if shape == "falsey-receipt":
                value["verification_receipt"] = 0
            raw = json.dumps(value)
            if shape == "duplicate-status":
                raw = '{"status":"failed",' + raw[1:]
            elif shape == "duplicate-receipt":
                raw = '{"verification_receipt":{"builds":[],"live":1},' + raw[1:]
            elif shape == "escaped-status":
                raw = raw.replace('"status"', '"st' + chr(92) + 'u0061tus"')
            elif shape == "utf16":
                raw = raw.encode("utf-16")
            state._connection.execute("UPDATE action SET record = ? WHERE action_key = ?", (raw, "release:history:0"))
        with landing.book.store.reading():
            landing.releases({})
        expected = [] if shape == "falsey-receipt" else [("publish", "release:history:0")]
        expected += [("press", "release:active:accepted"), ("press", "release:active:completed"), ("publish", "release:active:publishing")]
        assert calls == expected
    finally:
        landing.close()


@pytest.mark.parametrize("cast_text", [False, True])
def test_changed_invalid_encoding_keeps_legacy_error_before_callbacks(shell, config, tmp_path, monkeypatch, cast_text):
    landing = release_history(shell, config, tmp_path, active_first=True)
    calls = dispatched(monkeypatch, landing)
    try:
        with landing.book.store.reading():
            landing.releases({})
        calls.clear()
        with landing.book.store.transaction(runner_module.LANDING) as state:
            expression = "CAST(? AS TEXT)" if cast_text else "?"
            state._connection.execute(f"UPDATE action SET record = {expression} WHERE action_key = ?", (bytes([255]), "release:history:3"))
        error_class = sqlite3.OperationalError if cast_text else UnicodeDecodeError
        with pytest.raises(error_class):
            landing.book.actions(runner_module.LANDING, kind="release")
        with pytest.raises(error_class):
            with landing.book.store.reading():
                landing.releases({})
        assert calls == [] and landing.release_action_proof is None
    finally:
        landing.close()
