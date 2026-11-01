from dataclasses import asdict
import json
import sqlite3
import threading

import pytest

from long_running import sqlite_store
from test_desk_runner import actions, config, landing_of, runner_module, shell


def admission_rows():
    return {str(pr): {"ours": True, "state": "open", "test_state": "success", "head": "a" * 40, "base": "dev", "branch": f"pr/{pr}"} for pr in range(1, 8)}


def prepare_admission(landing, monkeypatch):
    monkeypatch.setattr(landing, "root_gos", lambda: [])
    monkeypatch.setattr(landing, "free_to_land", lambda gos, pr, head: True)
    monkeypatch.setattr(landing, "down_since", lambda: None)


@pytest.mark.parametrize("history", [32, 10_000])
def test_admission_avoids_unrelated_bus_history(shell, config, tmp_path, monkeypatch, history):
    landing = landing_of(shell, config, tmp_path)
    prepare_admission(landing, monkeypatch)
    store = landing.book.store
    with store.transaction(runner_module.LANDING) as state:
        for index in range(history):
            state.accept(f"unrelated:{index}", "bus", "x" * 8192, None, shell.now())
        for pr, status in enumerate(actions.STATUSES, 1):
            action = state.accept(f"{runner_module.MERGED_DIRECT}{pr}@{'a' * 40}", "bus", "merged", None, shell.now())
            action.status = status
    costs = {"action_dtos": 0, "target_bytes": 0, "sql_opcodes": 0, "read_connections": 0, "write_connections": 0}
    owner = threading.get_ident()
    load, connect = actions.Action.load.__func__, store._connect
    statements = []

    def built(cls, record):
        if threading.get_ident() == owner:
            costs["action_dtos"] += 1
            costs["target_bytes"] += len(record["target"].encode())
        return load(cls, record)

    def opened(path, writable=False):
        connection = connect(path, writable)
        costs["write_connections" if writable else "read_connections"] += 1
        connection.set_trace_callback(statements.append)

        def progress():
            costs["sql_opcodes"] += 1
            return 0

        connection.set_progress_handler(progress, 1)
        return connection

    monkeypatch.setattr(actions.Action, "load", classmethod(built))
    monkeypatch.setattr(store, "_connect", opened)
    shell.calls.clear()
    with store.reading():
        first = landing.admission(admission_rows(), [], None)
        first_cost = costs.copy()
        second = landing.admission(admission_rows(), [], None)
        print(json.dumps({"history": history, "first": first_cost, "twice": costs, "scope": "actual admission/private Book/Store; logical decoded target bytes, not OS or physical IO"}))
        assert first.heads == second.heads == {"7": "a" * 40}
        assert first_cost["action_dtos"] == 6, f"direct-merge admission decoded {first_cost['action_dtos']} actions instead of 6 candidates"
        assert costs["action_dtos"] == 12 and costs["target_bytes"] == 72
        assert first_cost["sql_opcodes"] < 1000 and costs["sql_opcodes"] < 2000
        assert costs["read_connections"] == 1 and costs["write_connections"] == 0
        with store._read(runner_module.LANDING) as connection:
            plan = connection.execute("EXPLAIN QUERY PLAN SELECT record FROM action WHERE " + sqlite_store._DIRECT_MERGE_CANDIDATES + " ORDER BY ordinal").fetchall()
            assert any("action_direct_merge_candidates" in row[3] for row in plan)
            assert connection.total_changes == 0
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER")) for statement in statements)
    assert not shell.calls


@pytest.mark.parametrize("indexed", [True, False], ids=["indexed", "indexless"])
def test_admission_preserves_all_statuses_and_raw_writer_identity(shell, config, tmp_path, monkeypatch, indexed):
    landing = landing_of(shell, config, tmp_path)
    prepare_admission(landing, monkeypatch)
    store = landing.book.store
    with store.transaction(runner_module.LANDING) as state:
        for pr, status in enumerate(actions.STATUSES, 1):
            action = state.accept(f"column-key-{pr}", "bus", "opaque target", None, shell.now())
            action.action_id = f"{runner_module.MERGED_DIRECT}{pr}@{'b' * 40}"
            action.status = status
        state.accept("merged-direct:7@not-the-record-id", "bus", "ordinary", None, shell.now()).action_id = "ordinary"
        state.accept("not-bus", "relay", "ordinary", None, shell.now()).action_id = "merged-direct:7@other-kind"
    with sqlite3.connect(store.path(runner_module.LANDING)) as writer:
        if not indexed:
            writer.execute("DROP INDEX action_direct_merge_candidates")
        before = writer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall()
    assert landing.admission(admission_rows(), [], None).heads == {"7": "a" * 40}
    assert [a.action_id for a in store.direct_merge_candidates(runner_module.LANDING)] == [f"merged-direct:{pr}@{'b' * 40}" for pr in range(1, 7)]
    with sqlite3.connect(store.path(runner_module.LANDING)) as observer:
        assert observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall() == before


@pytest.mark.parametrize("indexed", [True, False], ids=["indexed", "indexless"])
@pytest.mark.parametrize("encoding", ["normal", "duplicate-last-direct", "duplicate-last-other", "escaped-key", "escaped-value"])
def test_admission_preserves_python_action_id_decoding(shell, config, tmp_path, monkeypatch, indexed, encoding):
    landing = landing_of(shell, config, tmp_path)
    prepare_admission(landing, monkeypatch)
    store = landing.book.store
    with store.transaction(runner_module.LANDING) as state:
        action = state.accept("column-key", "bus", "opaque", None, shell.now())
        action.action_id = "merged-direct:1@abcdef1234"
        record = json.dumps(asdict(action))
    if encoding == "duplicate-last-direct":
        record = record.replace('"merged-direct:1@abcdef1234"', '"ordinary"')[:-1] + ', "action_id": "merged-direct:1@abcdef1234"}'
    elif encoding == "duplicate-last-other":
        record = record[:-1] + ', "action_id": "ordinary"}'
    elif encoding == "escaped-key":
        record = record.replace('"merged-direct:1@abcdef1234"', '"ordinary"')[:-1] + r', "action\u005fid": "merged-direct:1@abcdef1234"}'
    elif encoding == "escaped-value":
        record = record.replace('"merged-direct:', r'"\u006derged-direct:')
    with sqlite3.connect(store.path(runner_module.LANDING)) as writer:
        if not indexed:
            writer.execute("DROP INDEX action_direct_merge_candidates")
        writer.execute("UPDATE action SET record=? WHERE action_key='column-key'", (record,))
    wanted = set(admission_rows()) - ({"1"} if encoding != "duplicate-last-other" else set())
    assert set(landing.admission(admission_rows(), [], None).heads) == wanted


@pytest.mark.parametrize("indexed", [True, False], ids=["indexed", "indexless"])
@pytest.mark.parametrize("record,error", [("{broken", json.JSONDecodeError), ('{"action_id": "merged-direct:1@abc"}', TypeError)])
def test_admission_selected_record_validation_still_raises(shell, config, tmp_path, monkeypatch, indexed, record, error):
    landing = landing_of(shell, config, tmp_path)
    prepare_admission(landing, monkeypatch)
    store = landing.book.store
    with store.transaction(runner_module.LANDING) as state:
        state.accept("column-key", "bus", "opaque", None, shell.now())
    with sqlite3.connect(store.path(runner_module.LANDING)) as writer:
        if not indexed:
            writer.execute("DROP INDEX action_direct_merge_candidates")
        writer.execute("UPDATE action SET record=? WHERE action_key='column-key'", (record,))
    with pytest.raises(error):
        landing.admission(admission_rows(), [], None)
