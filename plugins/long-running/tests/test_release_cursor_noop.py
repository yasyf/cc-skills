import json
import sqlite3

import pytest

from test_desk_runner import actions, config, landing_of, runner_module, shell


def release_runner(shell, config, tmp_path, initial=None):
    runner = landing_of(shell, config, tmp_path).runner
    runner.book.ensure(runner_module.RUNNER, runner_module.RUNNER)
    if initial is not None:
        runner.book.edit(runner_module.RUNNER, lambda state: state.facts.update(initial))
    runner.release_polled = shell.now()
    return runner


def initialized(cursor=10):
    return {runner_module.RELEASE_CURSOR: cursor, runner_module.RELEASE_BUILDS: {}, runner_module.FREEZES: {}}


def record(seq, kind="release", lane="release-fix", text="", refs=None):
    return {"seq": seq, "drive": "d1", "lane": lane, "kind": kind, "text": text, "refs": refs or {}, "at": "2026-10-01T18:00:00Z", "to": []}


@pytest.mark.parametrize("history", [32, 10_000])
@pytest.mark.parametrize("route", ["read", "coupling"])
def test_no_event_release_poll_avoids_writer_transaction(shell, config, tmp_path, monkeypatch, history, route):
    initial = initialized()
    initial[runner_module.RELEASE_BUILDS] = {str(index): {"payload": "x" * 1024} for index in range(history)}
    initial[runner_module.FREEZES] = {str(index): {"payload": "y" * 1024} for index in range(history)}
    runner = release_runner(shell, config, tmp_path, initial)
    store = runner.book.store
    connect, loads = store._connect, json.loads
    flush = runner_module.actions.Transaction.flush
    connections, changes = [], []
    counts = {"ro": 0, "rw": 0, "decoded_bytes": 0, "vm": 0}

    def decoded(value, *args, **kwargs):
        counts["decoded_bytes"] += len(value.encode())
        return loads(value, *args, **kwargs)

    def opened(path, writable=False):
        connection = connect(path, writable)
        connections.append(connection)
        counts["rw" if writable else "ro"] += 1

        def progress():
            counts["vm"] += 1
            return 0

        connection.set_progress_handler(progress, 1)
        return connection

    def written(transaction):
        flush(transaction)
        if transaction._connection in connections:
            changes.append(transaction._connection.total_changes)

    monkeypatch.setattr(store, "_connect", opened)
    monkeypatch.setattr(json, "loads", decoded)
    monkeypatch.setattr(runner_module.actions.Transaction, "flush", written)
    with store.reading():
        for _ in range(32):
            runner.cci_tail.forget()
            (runner.read_release_records if route == "read" else runner.release_coupling)()
        print(json.dumps({"history": history, "route": route, "counts": counts, "writer_changes": changes, "scope": "actual caller/private Store; no physical or SHM attribution"}))
        assert counts["rw"] == 0, f"no-event release polling opened {counts['rw']} writers for 32 calls"
        assert counts["ro"] == 1 and not changes
        assert counts["decoded_bytes"] < 32 * 256
        assert counts["vm"] < 32 * 500
        assert connections[0].total_changes == 0 and not connections[0].in_transaction
        plan = connections[0].execute("EXPLAIN QUERY PLAN SELECT kind, record FROM fact WHERE path = ?", ("/release-builds",)).fetchall()
        assert any("INDEX" in row[3] and "path" in row[3] for row in plan)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("cursor", [0, 10])
@pytest.mark.parametrize("missing", [runner_module.RELEASE_BUILDS, runner_module.FREEZES, "both"])
def test_no_event_initializes_missing_maps_and_preserves_falsey_cursor(shell, config, tmp_path, cursor, missing):
    initial = initialized(cursor)
    for key in (runner_module.RELEASE_BUILDS, runner_module.FREEZES):
        if missing in (key, "both"):
            initial.pop(key)
    runner = release_runner(shell, config, tmp_path, initial)
    runner.read_release_records()
    assert runner.book.facts(runner_module.RUNNER, initialized()) == initialized(cursor)


@pytest.mark.parametrize("value", [None, False, 0, "", []])
def test_wrong_build_parent_retains_existing_error_and_rollback(shell, config, tmp_path, value):
    initial = initialized()
    initial[runner_module.RELEASE_BUILDS] = value
    runner = release_runner(shell, config, tmp_path, initial)
    with pytest.raises(AttributeError):
        runner.read_release_records()
    assert runner.book.facts(runner_module.RUNNER, initial) == initial


@pytest.mark.parametrize("value", [None, False, 0, "", []])
def test_wrong_freeze_parent_is_preserved_by_empty_original_callback(shell, config, tmp_path, monkeypatch, value):
    initial = initialized()
    initial[runner_module.FREEZES] = value
    runner = release_runner(shell, config, tmp_path, initial)
    edit, called = runner.book.edit, []

    def written(container, change):
        called.append(container)
        return edit(container, change)

    monkeypatch.setattr(runner.book, "edit", written)
    runner.read_release_records()
    assert called == [runner_module.RUNNER]
    assert runner.book.facts(runner_module.RUNNER, initial) == initial


def test_first_cursor_then_empty_initialization_preserves_original_pass_order(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path)
    shell.records = [record(11)]
    runner.read_release_records()
    assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_CURSOR) == 11
    assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_BUILDS, None) is None
    assert runner.book.fact(runner_module.RUNNER, runner_module.FREEZES, None) is None
    runner.cci_tail.forget()
    runner.read_release_records()
    assert runner.book.facts(runner_module.RUNNER, initialized()) == initialized(11)


def test_nonempty_records_claim_before_unchanged_merge_callback(shell, config, tmp_path, monkeypatch):
    runner = release_runner(shell, config, tmp_path, initialized())
    shell.records = [record(11, refs={"builds": ["7", "8"]}), record(12, "hold", text="froze api until later as freeze F1")]
    order = []
    monkeypatch.setattr(runner, "claim_release", lambda build, info: order.append(("claim", build)))
    edit = runner.book.edit
    writer = actions.Store(runner.book.store.root)

    def changed(container, change):
        with writer.transaction(container) as state:
            state.facts[runner_module.RELEASE_BUILDS]["outside"] = {"kept": True}
            state.facts[runner_module.FREEZES]["outside"] = {"kept": True}
        order.append(("edit", container))
        return edit(container, change)

    monkeypatch.setattr(runner.book, "edit", changed)
    runner.read_release_records()
    assert order == [("claim", "7"), ("claim", "8"), ("edit", runner_module.RUNNER)]
    builds = runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_BUILDS)
    freezes = runner.book.fact(runner_module.RUNNER, runner_module.FREEZES)
    assert list(builds) == ["outside", "7", "8"] and builds["outside"] == {"kept": True}
    assert list(freezes) == ["outside", "F1"] and freezes["outside"] == {"kept": True}
    assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_CURSOR) == 12


def test_advanced_filtered_record_still_saves_cursor(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, initialized())
    shell.records = [record(11, lane=runner_module.ROOT)]
    runner.read_release_records()
    assert runner.book.facts(runner_module.RUNNER, initialized()) == initialized(11)


def test_same_build_record_keeps_existing_entry_and_advances_cursor(shell, config, tmp_path, monkeypatch):
    initial = initialized()
    initial[runner_module.RELEASE_BUILDS]["7"] = {"done": True, "lane": "original"}
    runner = release_runner(shell, config, tmp_path, initial)
    shell.records = [record(11, refs={"builds": ["7"]})]
    monkeypatch.setattr(runner, "claim_release", lambda *args: pytest.fail("existing build was claimed again"))
    runner.read_release_records()
    assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_BUILDS) == initial[runner_module.RELEASE_BUILDS]
    assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_CURSOR) == 11


def test_fact_view_is_one_snapshot_detached_and_closed(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, initialized())
    writer = actions.Store(runner.book.store.root)
    borrowed = []

    def read(facts):
        borrowed.append(facts)
        before = facts[runner_module.RELEASE_CURSOR]
        with writer.transaction(runner_module.RUNNER) as state:
            state.facts[runner_module.RELEASE_CURSOR] = 20
            state.facts[runner_module.RELEASE_BUILDS]["new"] = {"items": [1]}
        assert facts[runner_module.RELEASE_CURSOR] == before == 10
        return facts[runner_module.RELEASE_BUILDS]

    with runner.book.store.reading():
        assert runner.book.read_facts(runner_module.RUNNER, read) == {}
        with pytest.raises(RuntimeError, match="active transaction"):
            borrowed[0][runner_module.RELEASE_CURSOR]
        assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_CURSOR) == 20
        detached = runner.book.read_facts(runner_module.RUNNER, lambda facts: facts[runner_module.RELEASE_BUILDS])
        detached["new"]["items"].append(2)
        assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_BUILDS) == {"new": {"items": [1]}}


def test_fact_view_error_closes_owned_reader_and_preserves_identity(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, initialized())
    error = ValueError("selection failed")
    borrowed = []

    def failed(facts):
        borrowed.append(facts)
        raise error

    with runner.book.store.reading():
        with pytest.raises(ValueError) as caught:
            runner.book.read_facts(runner_module.RUNNER, failed)
        assert caught.value is error
        assert not runner.book.store._readers.connections
        with pytest.raises(RuntimeError, match="active transaction"):
            borrowed[0][runner_module.RELEASE_CURSOR]
        assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_CURSOR) == 10


def test_cursor_changed_before_guard_uses_original_writer_callback(shell, config, tmp_path, monkeypatch):
    runner = release_runner(shell, config, tmp_path, initialized())
    writer = actions.Store(runner.book.store.root)
    read = runner.book.read_facts
    edit = runner.book.edit
    calls = []

    def changed(container, selection):
        with writer.transaction(container) as state:
            state.facts[runner_module.RELEASE_CURSOR] = 20
            state.facts[runner_module.RELEASE_BUILDS]["outside"] = {"kept": True}
        return read(container, selection)

    def written(container, change):
        calls.append(container)
        return edit(container, change)

    monkeypatch.setattr(runner.book, "read_facts", changed)
    monkeypatch.setattr(runner.book, "edit", written)
    runner.read_release_records()
    assert calls == [runner_module.RUNNER]
    assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_CURSOR) == 10
    assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_BUILDS) == {"outside": {"kept": True}}


@pytest.mark.parametrize("removed", [True, False])
def test_mapping_changed_before_guard_uses_original_initialization_or_error(shell, config, tmp_path, monkeypatch, removed):
    runner = release_runner(shell, config, tmp_path, initialized())
    writer = actions.Store(runner.book.store.root)
    read = runner.book.read_facts

    def changed(container, selection):
        with writer.transaction(container) as state:
            if removed:
                state.facts.pop(runner_module.RELEASE_BUILDS)
            else:
                state.facts[runner_module.RELEASE_BUILDS] = 0
        return read(container, selection)

    monkeypatch.setattr(runner.book, "read_facts", changed)
    if removed:
        runner.read_release_records()
        assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_BUILDS) == {}
    else:
        with pytest.raises(AttributeError):
            runner.read_release_records()
        assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_BUILDS) == 0


def test_fact_view_cannot_write_appdata(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, initialized())

    def changed(facts):
        facts[runner_module.RELEASE_CURSOR] = 99

    with runner.book.store.reading():
        with pytest.raises(sqlite3.OperationalError):
            runner.book.read_facts(runner_module.RUNNER, changed)
        assert not runner.book.store._readers.connections
        assert runner.book.fact(runner_module.RUNNER, runner_module.RELEASE_CURSOR) == 10


def test_source_error_and_changed_schema_keep_existing_failure_paths(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, initialized())
    shell.tail_error = "source failed"
    with pytest.raises(runner_module.CciTailFailed):
        runner.read_release_records()
    assert runner.book.facts(runner_module.RUNNER, initialized()) == initialized()
    with runner.book.store.transaction(runner_module.RUNNER) as state:
        state._connection.execute("PRAGMA user_version = 3")
    with pytest.raises(ValueError, match="unsupported action store version"):
        runner.read_release_records()
