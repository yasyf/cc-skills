import json
import sqlite3
import threading

import pytest

from test_desk_runner import actions, config, landing_of, runner_module, shell


def wait_entry(pr="1", head="a" * 40, rows=None):
    return {"key": f"merge:{pr}:{head[:7]}", "rows": ["reserved"] if rows is None else rows}


def prepare_waits(shell, config, tmp_path, monkeypatch):
    landing = landing_of(shell, config, tmp_path)
    monkeypatch.setattr(landing, "reserved_touches", lambda pr, head: [("reserved", "unused")])
    return landing


@pytest.mark.parametrize("history", [32, 10_000])
def test_unchanged_merge_wait_reads_only_graded_children(shell, config, tmp_path, monkeypatch, history):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    store = landing.book.store
    with store.transaction(runner_module.LANDING) as state:
        state.facts[runner_module.MERGE_WAITS] = {str(index + 10): wait_entry(str(index + 10), rows=["x" * 8192]) for index in range(history)} | {"1": wait_entry()}
    costs = {"decoded_values": 0, "decoded_bytes": 0, "sql_opcodes": 0, "ro": 0, "rw": 0}
    loads, connect = json.loads, store._connect
    owner = threading.get_ident()
    connections, statements = [], []

    def decoded(value, *args, **kwargs):
        if threading.get_ident() == owner:
            costs["decoded_values"] += 1
            costs["decoded_bytes"] += len(value.encode())
        return loads(value, *args, **kwargs)

    def opened(path, writable=False):
        connection = connect(path, writable)
        connections.append(connection)
        costs["rw" if writable else "ro"] += 1
        connection.set_trace_callback(statements.append)

        def progress():
            costs["sql_opcodes"] += 1
            return 0

        connection.set_progress_handler(progress, 1)
        return connection

    monkeypatch.setattr(json, "loads", decoded)
    monkeypatch.setattr(store, "_connect", opened)
    with store.reading():
        assert landing.record_waits({"1": "a" * 40}, {"1": ["ignored"]}) is None
        first = costs.copy()
        assert landing.record_waits({"1": "a" * 40}, {"1": ["ignored"]}) is None
        print(json.dumps({"history": history, "first": first, "twice": costs, "scope": "actual record_waits/private Book/Store; decoded persisted fact values, not physical IO"}))
        assert first["decoded_values"] == 2, f"unchanged merge waits decoded {first['decoded_values']} fact values instead of 2"
        assert costs["decoded_values"] == 4 and costs["decoded_bytes"] < 1024
        assert first["sql_opcodes"] < 1000 and costs["sql_opcodes"] < 2000
        assert costs["ro"] == 1 and costs["rw"] == 0
        assert all(connection.total_changes == 0 for connection in connections)
        plan = connections[0].execute("EXPLAIN QUERY PLAN SELECT kind, record FROM fact WHERE path = ?", ("/merge_waits/1",)).fetchall()
        assert any("INDEX" in row[3] and "path" in row[3] for row in plan)
    assert not any(line.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER")) for line in statements)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("initial", [None, {}, [], False, 0, "", {"other": {"key": "kept", "rows": []}}, {"1": None}, {"1": wait_entry()}, {"1": wait_entry(head="b" * 40)}, {"1": wait_entry(rows=["different"])}, {"1": wait_entry(), "2": wait_entry("2"), "other": [1]}])
def test_record_waits_preserves_changed_merge_and_order(shell, config, tmp_path, monkeypatch, initial):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    with landing.book.store.transaction(runner_module.LANDING) as state:
        state.facts[runner_module.MERGE_WAITS] = initial
    heads, waiting = {"1": "a" * 40, "2": "b" * 40}, {"1": ["ignored"]}
    original = initial or {}
    expected = {pr: value for pr, value in original.items() if pr not in heads} | {"1": wait_entry()}
    landing.record_waits(heads, waiting)
    found = landing.book.fact(runner_module.LANDING, runner_module.MERGE_WAITS)
    assert found == expected and list(found) == list(expected)


@pytest.mark.parametrize("heads,waiting", [({}, {}), ({"1": "a" * 40}, {})])
def test_record_waits_empty_and_removal(shell, config, tmp_path, monkeypatch, heads, waiting):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    with landing.book.store.transaction(runner_module.LANDING) as state:
        state.facts[runner_module.MERGE_WAITS] = {"1": None, "other": ["kept"]}
    landing.record_waits(heads, waiting)
    assert landing.book.fact(runner_module.LANDING, runner_module.MERGE_WAITS) == {pr: value for pr, value in {"1": None, "other": ["kept"]}.items() if pr not in heads}


def test_unchanged_record_waits_preserves_existing_order(shell, config, tmp_path, monkeypatch):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    with landing.book.store.transaction(runner_module.LANDING) as state:
        state.facts[runner_module.MERGE_WAITS] = {"1": wait_entry(), "outside": [1]}
    landing.record_waits({"1": "a" * 40}, {"1": ["ignored"]})
    assert list(landing.book.fact(runner_module.LANDING, runner_module.MERGE_WAITS)) == ["1", "outside"]


def test_record_waits_changed_callback_merges_current_writer_state(shell, config, tmp_path, monkeypatch):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    store = landing.book.store
    writer = actions.Store(store.root)
    with writer.transaction(runner_module.LANDING) as state:
        state.facts[runner_module.MERGE_WAITS] = {"outside": ["old"], "1": None}
    selected = store.fact_children

    def changed(*args):
        result = selected(*args)
        with writer.transaction(runner_module.LANDING) as state:
            state.facts[runner_module.MERGE_WAITS]["outside"] = ["new"]
            state.facts[runner_module.MERGE_WAITS]["inserted"] = True
        return result

    monkeypatch.setattr(store, "fact_children", changed)
    landing.record_waits({"1": "a" * 40}, {"1": ["ignored"]})
    found = store.fact(runner_module.LANDING, runner_module.MERGE_WAITS)
    assert found == {"outside": ["new"], "inserted": True, "1": wait_entry()}
    assert list(found) == ["outside", "inserted", "1"]


def test_fact_children_share_one_snapshot_and_refresh_next_read(shell, config, tmp_path, monkeypatch):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    store = landing.book.store
    writer = actions.Store(store.root)
    with writer.transaction(runner_module.LANDING) as state:
        state.facts["a/~"] = {"a": "old-a", "b": "old-b"}
    loads = json.loads
    changed = False

    def decoded(value, *args, **kwargs):
        nonlocal changed
        result = loads(value, *args, **kwargs)
        if value == '"old-a"' and not changed:
            changed = True
            with writer.transaction(runner_module.LANDING) as state:
                state.facts["a/~"].update({"a": "new-a", "b": "new-b"})
        return result

    monkeypatch.setattr(json, "loads", decoded)
    with store.reading():
        assert store.fact_children(runner_module.LANDING, "a/~", ["a", "b"]) == {"a": "old-a", "b": "old-b"}
        assert changed
        assert store.fact_children(runner_module.LANDING, "a/~", ["b", "a"]) == {"b": "new-b", "a": "new-a"}


def test_fact_children_missing_keys_escaping_and_detachment(shell, config, tmp_path, monkeypatch):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    store = landing.book.store
    with store.transaction(runner_module.LANDING) as state:
        state.facts["a/~"] = {"a/b": {"value": [1]}, "~": False, "null": None}
    found = store.fact_children(runner_module.LANDING, "a/~", ["~", "missing", "a/b", "null"])
    assert list(found) == ["~", "a/b", "null"]
    assert found == {"~": False, "a/b": {"value": [1]}, "null": None}
    found["a/b"]["value"].append(2)
    assert store.fact_path(runner_module.LANDING, ["a/~", "a/b"]) == {"value": [1]}
    assert store.fact_children(runner_module.LANDING, "missing", ["x"]) == {}
    assert store.fact_children(runner_module.LANDING, "a/~", []) == {}


def test_record_waits_error_keeps_original_facts(shell, config, tmp_path, monkeypatch):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    with landing.book.store.transaction(runner_module.LANDING) as state:
        state.facts[runner_module.MERGE_WAITS] = {"other": [1]}
    with pytest.raises(KeyError):
        landing.record_waits({}, {"missing": ["ignored"]})
    assert landing.book.fact(runner_module.LANDING, runner_module.MERGE_WAITS) == {"other": [1]}


def test_record_waits_missing_fact_stays_absent_until_needed(shell, config, tmp_path, monkeypatch):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    landing.record_waits({}, {})
    assert runner_module.MERGE_WAITS not in landing.book.facts(runner_module.LANDING, [runner_module.MERGE_WAITS])
    landing.record_waits({"1": "a" * 40}, {"1": ["ignored"]})
    assert landing.book.fact(runner_module.LANDING, runner_module.MERGE_WAITS) == {"1": wait_entry()}


@pytest.mark.parametrize("value", [1, True, "not-a-map", [1]])
def test_record_waits_rejects_nonmapping_wait_state(shell, config, tmp_path, monkeypatch, value):
    landing = prepare_waits(shell, config, tmp_path, monkeypatch)
    with landing.book.store.transaction(runner_module.LANDING) as state:
        state.facts[runner_module.MERGE_WAITS] = value
    with pytest.raises(AttributeError):
        landing.record_waits({}, {})
    assert landing.book.fact(runner_module.LANDING, runner_module.MERGE_WAITS) == value
