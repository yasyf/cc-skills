import json
import sqlite3

import pytest

from test_desk_runner import actions, config, runner_module, shell
from test_open_cut_clock_noop import clock_runner, initialized, release_action


def release_runner(shell, config, tmp_path, history=4, late=False):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = {f"release:{pr}": {"ended": "done", "since": "kept"} for pr in range(1, history + 1)}
    runner = clock_runner(shell, config, tmp_path, initial)
    stamp = actions.stamp(shell.now())
    records = {}
    for pr in ([90001] if late else []) + list(range(1, history + 1)):
        pending = pr == 90001
        action = actions.Action(runner_module.LANDING, f"release:{pr}", "release", json.dumps({"pr": pr, "components": ["sand"], "sha": "94d61b91951"}), None, 0, 0, stamp, status="accepted" if pending else "completed", response=None if pending else {"at": stamp})
        records[action.action_id] = action
    runner.book.edit(runner_module.LANDING, lambda state: state.actions.update(records))
    return runner


@pytest.mark.parametrize("history", [32, 10_000])
@pytest.mark.parametrize("writes", ["unchanged", "runner", "landing", "every-poll"])
def test_warm_release_poll_uses_bounded_unchanged_proof(shell, config, tmp_path, monkeypatch, history, writes):
    runner = release_runner(shell, config, tmp_path, history)
    store = runner.book.store
    writer = actions.Store(store.root)
    connect, loads, load = store._connect, json.loads, actions.Action.load
    counts = {"ro": 0, "rw": 0, "action_dtos": 0, "decoded_bytes": 0, "vm": 0, "read_transactions": 0}
    connections = []
    observing = True

    def opened(path, writable=False):
        connection = connect(path, writable)
        connections.append(connection)
        counts["rw" if writable else "ro"] += 1
        def progress():
            if observing:
                counts["vm"] += 1
            return 0
        connection.set_progress_handler(progress, 1)
        def traced(statement):
            if observing and statement == "BEGIN":
                counts["read_transactions"] += 1
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
    with store.reading():
        runner.open_cut_clocks()
        cold = dict(counts)
        counts.update(action_dtos=0, decoded_bytes=0, vm=0, read_transactions=0)
        external_writes = 0
        for index in range(32):
            if writes != "unchanged" and (index % 8 == 0 or writes == "every-poll"):
                observing = False
                container = runner_module.RUNNER if writes == "runner" else runner_module.LANDING
                with writer.transaction(container) as state:
                    state.facts["unrelated"] = index
                external_writes += 1
                observing = True
            runner.cci_tail.forget()
            runner.open_cut_clocks()
        measured = dict(counts)
        print(json.dumps({"completed_release_actions": history, "writes": writes, "polls": 32, "cold": cold, "warm": measured, "external_writer_transactions": external_writes, "immutable794_baseline_dtos": 32 * history, "avoided_dtos": 32 * history - measured["action_dtos"], "sqlite": sqlite3.sqlite_version, "scope": "actual caller/private snapshots; no live-host IO inference"}))
        expected = 0 if writes == "unchanged" else history * external_writes
        assert measured["action_dtos"] == expected, f"warm release polling materialized {measured['action_dtos']} DTOs for {history} actions in 32 polls"
        assert measured["ro"] == 2 and measured["rw"] == 0
        if writes == "unchanged":
            assert measured["decoded_bytes"] < 32 * 256 and measured["vm"] < 32 * 1500
        elif writes != "every-poll":
            assert measured["decoded_bytes"] < cold["decoded_bytes"] * 8
            assert measured["vm"] < cold["vm"] * 12
        assert len(store._readers.connections) == 2
        assert all(connection.total_changes == 0 and not connection.in_transaction for connection in connections)
        assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10
        for pr in (1, history):
            assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, f"release:{pr}"]) == {"ended": "done", "since": "kept"}
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_old_ordinal_late_completion_invalidates_proof(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, late=True)
    store = runner.book.store
    writer = actions.Store(store.root)
    with store.reading():
        runner.open_cut_clocks()
        with writer.transaction(runner_module.LANDING) as state:
            before = state._connection.execute("SELECT ordinal FROM action WHERE action_key = ?", ("release:90001",)).fetchone()[0]
            state.start("release:90001", shell.now())
            state.complete("release:90001", {"at": actions.stamp(shell.now())})
            after = state._connection.execute("SELECT ordinal FROM action WHERE action_key = ?", ("release:90001",)).fetchone()[0]
            later = state._connection.execute("SELECT ordinal FROM action WHERE action_key = ?", ("release:1",)).fetchone()[0]
        assert before == after < later
        runner.open_cut_clocks()
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:90001", "pr"]) == 90001


def test_writer_after_action_snapshot_does_not_relabel_validated_rows(shell, config, tmp_path, monkeypatch):
    runner = release_runner(shell, config, tmp_path, late=True)
    writer = actions.Store(runner.book.store.root)
    read = runner.book.actions_with_version
    pending = [True]
    def snapshot(*args, **kwargs):
        result = read(*args, **kwargs)
        if pending[0]:
            pending[0] = False
            with writer.transaction(runner_module.LANDING) as state:
                state.start("release:90001", shell.now())
                state.complete("release:90001", {"at": actions.stamp(shell.now())})
        return result
    monkeypatch.setattr(runner.book, "actions_with_version", snapshot)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:90001"], None) is None
        runner.open_cut_clocks()
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:90001", "pr"]) == 90001


def test_writer_after_fact_snapshot_does_not_relabel_membership(shell, config, tmp_path, monkeypatch):
    runner = release_runner(shell, config, tmp_path)
    writer = actions.Store(runner.book.store.root)
    read = runner.book.read_facts_with_version
    pending = [True]
    def snapshot(*args, **kwargs):
        result = read(*args, **kwargs)
        if pending[0]:
            pending[0] = False
            with writer.transaction(runner_module.RUNNER) as state:
                del state.facts[runner_module.CUT_CLOCKS]["release:1"]
        return result
    monkeypatch.setattr(runner.book, "read_facts_with_version", snapshot)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:1"], None) is None
        runner.open_cut_clocks()
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:1", "pr"]) == 1


@pytest.mark.parametrize("change", ["child", "parent", "wrong-type"])
def test_clock_membership_changes_invalidate_proof(shell, config, tmp_path, change):
    runner = release_runner(shell, config, tmp_path)
    writer = actions.Store(runner.book.store.root)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        with writer.transaction(runner_module.RUNNER) as state:
            if change == "child":
                del state.facts[runner_module.CUT_CLOCKS]["release:1"]
            elif change == "parent":
                state.facts.pop(runner_module.CUT_CLOCKS)
            else:
                state.facts[runner_module.CUT_CLOCKS] = None
        if change == "wrong-type":
            with pytest.raises(AttributeError):
                runner.open_cut_clocks()
        else:
            runner.open_cut_clocks()
            assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:1", "pr"]) == 1
        assert runner.cut_clock_proof is None


def test_missing_owner_after_warm_proof_retains_error(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        runner.config.landing.pop("release_owner")
        with pytest.raises(KeyError, match="release_owner"):
            runner.open_cut_clocks()
        assert runner.cut_clock_proof is None


@pytest.mark.parametrize("error_class", [runner_module.CciTailFailed, runner_module.StopRequested])
def test_source_failure_after_warm_proof_preserves_identity(shell, config, tmp_path, monkeypatch, error_class):
    runner = release_runner(shell, config, tmp_path)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        error = error_class("private source failure")
        def failed(*args, **kwargs):
            raise error
        monkeypatch.setattr(runner_module.cci, "records", failed)
        with pytest.raises(error_class) as raised:
            runner.open_cut_clocks()
        assert raised.value is error and runner.cut_clock_proof is None


def test_later_malformed_target_is_not_hidden_by_proof(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path)
    writer = actions.Store(runner.book.store.root)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        with writer.transaction(runner_module.LANDING) as state:
            state.actions["release:1"].target = "{"
        with pytest.raises(json.JSONDecodeError):
            runner.open_cut_clocks()
        assert runner.cut_clock_proof is None


def test_later_scalar_decode_error_is_not_hidden_by_proof(shell, config, tmp_path):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = {"release:41": "valid before corruption"}
    runner = clock_runner(shell, config, tmp_path, initial)
    release_action(runner, shell)
    writer = actions.Store(runner.book.store.root)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        with writer.transaction(runner_module.RUNNER) as state:
            state._connection.execute("UPDATE fact SET record = ? WHERE path = ?", ("{", "/cut-clocks/release:41"))
        with pytest.raises(json.JSONDecodeError):
            runner.open_cut_clocks()
        assert runner.cut_clock_proof is None


@pytest.mark.parametrize("container", [runner_module.RUNNER, runner_module.LANDING])
def test_warm_proof_does_not_cache_schema_validation(shell, config, tmp_path, container):
    runner = release_runner(shell, config, tmp_path)
    writer = actions.Store(runner.book.store.root)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        with writer.transaction(container) as state:
            state._connection.execute("PRAGMA user_version = 3")
        with pytest.raises(ValueError, match="unsupported action store version"):
            runner.open_cut_clocks()
        assert runner.cut_clock_proof is None


def test_new_runner_starts_without_prior_proof(shell, config, tmp_path, monkeypatch):
    runner = release_runner(shell, config, tmp_path)
    load = actions.Action.load
    calls = []
    def loaded(cls, record):
        calls.append(record["action_id"])
        return load(record)
    monkeypatch.setattr(actions.Action, "load", classmethod(loaded))
    with runner.book.store.reading():
        runner.open_cut_clocks()
        calls.clear()
        successor = runner_module.Runner(shell, runner.config, runner.book.store)
        assert successor.cut_clock_proof is None
        successor.open_cut_clocks()
        assert len(calls) == 4


def test_proof_keeps_only_fixed_identity_metadata(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, 32)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        cursor, source, clocks, owner_required = runner.cut_clock_proof
        assert cursor == 10 and owner_required is True
        for version in (source, clocks):
            assert isinstance(version, runner_module.ReadVersion)
            assert set(vars(version)) == {"_connection", "_value"}
            assert isinstance(version._value, int) and not version._connection.in_transaction


def test_existing_clocks_keep_original_ordinal_and_eager_error_order(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, 32)
    with runner.book.store.transaction(runner_module.LANDING) as state:
        first = state.actions["release:1"].target
        state.actions["release:1"].target = "{"
        state.actions["release:2"].response = None
        state.actions["release:10"].target = "{"
    with runner.book.store.reading():
        with pytest.raises(json.JSONDecodeError):
            runner.open_cut_clocks()
        assert runner.cut_clock_proof is None
        with runner.book.store.transaction(runner_module.LANDING) as state:
            state.actions["release:1"].target = first
        with pytest.raises(TypeError):
            runner.open_cut_clocks()
        assert runner.cut_clock_proof is None
        assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:1"]) == {"ended": "done", "since": "kept"}
