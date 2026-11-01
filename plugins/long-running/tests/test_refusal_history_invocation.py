import json
import sqlite3
from datetime import timedelta

import pytest

from test_desk_runner import actions, config, landing_of, runner_module, shell
from long_running import sqlite_store


def refusal_history(shell, config, tmp_path, monkeypatch, history=4, eligible=3, target=None):
    landing = landing_of(shell, config, tmp_path)
    stamp = actions.stamp(shell.now())
    with landing.book.store.transaction(runner_module.LANDING) as state:
        for index in range(history):
            key = f"history:{index}"
            state.actions[key] = actions.Action(runner_module.LANDING, key, "bus", target or json.dumps({"kind": "note", "text": "x" * 128}), None, 0, 0, stamp)
    rows = {str(index): {"state": "open", "head": f"{index:040x}", "test_state": "success"} for index in range(1, eligible + 1)}
    landing.below_blocking = set()
    monkeypatch.setattr(landing, "down_since", lambda: None)
    monkeypatch.setattr(landing, "believed_queued", lambda rows: {})
    monkeypatch.setattr(landing, "head_gos", lambda pr, head: [{"at": actions.stamp(shell.now() - timedelta(minutes=10))}])
    monkeypatch.setattr(landing, "queue_states", lambda: {pr: "not queued" for pr in rows})
    visited = []
    monkeypatch.setattr(landing, "review_bot", lambda pr, head, row: visited.append(pr) or "")
    monkeypatch.setattr(landing.adapter, "queued", lambda prs: {})
    return landing, rows, visited


@pytest.mark.parametrize("history", [32, 10_000])
def test_unchanged_bus_history_is_loaded_once_per_invocation(shell, config, tmp_path, monkeypatch, history):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch, history, 32)
    store, load = landing.book.store, actions.Action.load
    connect = store._connect
    count = {"dtos": 0, "ro": 0, "rw": 0, "vm": 0}
    connections = []
    def loaded(cls, record):
        count["dtos"] += record["kind"] == "bus"
        return load(record)
    def opened(path, writable=False):
        connection = connect(path, writable)
        connections.append(connection)
        count["rw" if writable else "ro"] += 1
        def progress():
            count["vm"] += 1
            return 0
        connection.set_progress_handler(progress, 1)
        return connection
    monkeypatch.setattr(actions.Action, "load", classmethod(loaded))
    monkeypatch.setattr(store, "_connect", opened)
    landing.unqueued_gos(rows, [])
    assert visited == list(rows)
    print(json.dumps({"bus_history": history, "eligible_prs": 32, **count, "sqlite": sqlite3.sqlite_version}))
    assert count["dtos"] == history, f"refusal history materialized {count['dtos']} DTOs; expected {history} for 32 eligible PRs"
    assert count["ro"] == 1 and count["rw"] == 0
    assert count["vm"] < history * 30 + 32 * 300
    assert store._readers.connections is None
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
    visited.clear()
    count.update({key: 0 for key in count})
    landing.unqueued_gos(rows, [])
    assert visited == list(rows) and count["dtos"] == history


@pytest.mark.parametrize("status", ["accepted", "started", "completed", "verified", "failed", "unverifiable"])
@pytest.mark.parametrize("writer_kind", ["same_store", "independent"])
def test_between_pr_blocker_commit_is_visible(shell, config, tmp_path, monkeypatch, status, writer_kind):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch)
    store = landing.book.store
    writer = store if writer_kind == "same_store" else actions.Store(store.root)
    current = []
    def gos(pr, head):
        current.append(pr)
        if pr == "2":
            key = f"blocker:2:{2:040x}"
            with writer.transaction(runner_module.LANDING) as state:
                state.actions[key] = actions.Action(runner_module.LANDING, key, "bus", json.dumps({"kind": "blocker", "pr": "2"}), None, 0, 0, actions.stamp(shell.now()), status=status)
        return [{"at": actions.stamp(shell.now() - timedelta(minutes=10))}]
    monkeypatch.setattr(landing, "head_gos", gos)
    landing.unqueued_gos(rows, [])
    assert current == list(rows) and visited == ["1", "3"]
    assert store._readers.connections is None


def test_target_error_stays_lazy_after_first_refusal(shell, config, tmp_path, monkeypatch):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch, history=0)
    with landing.book.store.transaction(runner_module.LANDING) as state:
        key = f"blocker:1:{1:040x}"
        state.actions[key] = actions.Action(runner_module.LANDING, key, "bus", json.dumps({"kind": "blocker", "pr": "1"}), None, 0, 0, actions.stamp(shell.now()))
        state.actions["invalid-target"] = actions.Action(runner_module.LANDING, "invalid-target", "bus", "{", None, 0, 0, actions.stamp(shell.now()))
    with pytest.raises(json.JSONDecodeError):
        landing.unqueued_gos(rows, [])
    assert visited == []
    assert landing.book.store._readers.connections is None


def test_late_dto_error_precedes_first_target(shell, config, tmp_path, monkeypatch):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch, history=0)
    store = landing.book.store
    with store.transaction(runner_module.LANDING) as state:
        key = f"blocker:1:{1:040x}"
        state.actions[key] = actions.Action(runner_module.LANDING, key, "bus", json.dumps({"kind": "blocker", "pr": "1"}), None, 0, 0, actions.stamp(shell.now()))
        state.actions["bad-dto"] = actions.Action(runner_module.LANDING, "bad-dto", "bus", "{}", None, 0, 0, actions.stamp(shell.now()))
    with sqlite3.connect(store.path(runner_module.LANDING)) as connection:
        connection.execute("UPDATE action SET record = '{}' WHERE action_key = 'bad-dto'")
    with pytest.raises(TypeError):
        landing.unqueued_gos(rows, [])
    assert visited == [] and store._readers.connections is None


def test_schema_change_between_prs_still_refuses(shell, config, tmp_path, monkeypatch):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch)
    def gos(pr, head):
        if pr == "2":
            with sqlite3.connect(landing.book.store.path(runner_module.LANDING)) as connection:
                connection.execute("PRAGMA user_version = 3")
        return [{"at": actions.stamp(shell.now() - timedelta(minutes=10))}]
    monkeypatch.setattr(landing, "head_gos", gos)
    with pytest.raises(ValueError, match="unsupported action store version"):
        landing.unqueued_gos(rows, [])
    assert visited == ["1"] and landing.book.store._readers.connections is None


@pytest.mark.parametrize("commit_at", ["inside_read", "after_read"])
def test_rows_and_version_share_snapshot_before_external_commit(shell, config, tmp_path, monkeypatch, commit_at):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch)
    store = landing.book.store
    writer = actions.Store(store.root)
    calls = []
    def write():
        key = f"blocker:2:{2:040x}"
        with writer.transaction(runner_module.LANDING) as state:
            state.actions[key] = actions.Action(runner_module.LANDING, key, "bus", json.dumps({"kind": "blocker", "pr": "2"}), None, 0, 0, actions.stamp(shell.now()))
    if commit_at == "inside_read":
        version = sqlite_store._read_version
        def sampled(connection):
            result = version(connection)
            if not calls:
                calls.append("committed")
                write()
            return result
        monkeypatch.setattr(sqlite_store, "_read_version", sampled)
    else:
        selected = landing.book.actions_with_version
        def read(*args, **kwargs):
            result = selected(*args, **kwargs)
            if not calls:
                calls.append("committed")
                write()
            return result
        monkeypatch.setattr(landing.book, "actions_with_version", read)
    landing.unqueued_gos(rows, [])
    assert calls == ["committed"] and visited == ["1", "3"]
    assert store._readers.connections is None


def test_unrelated_commit_requires_fresh_full_validation(shell, config, tmp_path, monkeypatch):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch, history=32)
    writer = actions.Store(landing.book.store.root)
    load, counts = actions.Action.load, []
    def loaded(cls, record):
        if record["kind"] == "bus":
            counts.append(record["action_id"])
        return load(record)
    def gos(pr, head):
        if pr == "2":
            with writer.transaction(runner_module.LANDING) as state:
                state.facts["unrelated"] = True
        return [{"at": actions.stamp(shell.now() - timedelta(minutes=10))}]
    monkeypatch.setattr(actions.Action, "load", classmethod(loaded))
    monkeypatch.setattr(landing, "head_gos", gos)
    landing.unqueued_gos(rows, [])
    assert visited == list(rows) and len(counts) == 64


def test_changed_late_dto_is_revalidated_before_next_pr_target(shell, config, tmp_path, monkeypatch):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch)
    def gos(pr, head):
        if pr == "2":
            with sqlite3.connect(landing.book.store.path(runner_module.LANDING)) as connection:
                connection.execute("UPDATE action SET record = '{}' WHERE action_key = 'history:3'")
        return [{"at": actions.stamp(shell.now() - timedelta(minutes=10))}]
    monkeypatch.setattr(landing, "head_gos", gos)
    with pytest.raises(TypeError):
        landing.unqueued_gos(rows, [])
    assert visited == ["1"] and landing.book.store._readers.connections is None


def test_invocation_keeps_existing_outer_reader_scope(shell, config, tmp_path, monkeypatch):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch)
    store = landing.book.store
    with store.reading():
        pooled = store._readers.connections
        landing.unqueued_gos(rows, [])
        assert visited == list(rows) and store._readers.connections is pooled
        assert all(not connection.in_transaction for _, connection in pooled.values())
    assert store._readers.connections is None


@pytest.mark.parametrize("mode", ["no_go", "held", "no_queue"])
def test_ineligible_rows_do_not_load_bus_history(shell, config, tmp_path, monkeypatch, mode):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail("ineligible row loaded bus history")
    monkeypatch.setattr(landing.book, "actions_with_version", forbidden)
    if mode == "no_go":
        monkeypatch.setattr(landing, "head_gos", lambda *args: [])
    if mode == "no_queue":
        monkeypatch.setattr(landing, "queue_states", lambda: None)
    landing.unqueued_gos(rows, list(rows) if mode == "held" else [])
    assert visited == [] and landing.book.store._readers.connections is None


def test_target_failure_identity_releases_invocation_readers(shell, config, tmp_path, monkeypatch):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch)
    failure = runner_module.StopRequested()
    def stopped(*args):
        raise failure
    monkeypatch.setattr(runner_module, "refused_members", stopped)
    with pytest.raises(runner_module.StopRequested) as raised:
        landing.unqueued_gos(rows, [])
    assert raised.value is failure
    assert visited == [] and landing.book.store._readers.connections is None


@pytest.mark.parametrize("encoding", ["string", "integer", "escaped"])
def test_existing_key_mismatch_unicode_and_all_bus_statuses_keep_target_semantics(shell, config, tmp_path, monkeypatch, encoding):
    landing, rows, visited = refusal_history(shell, config, tmp_path, monkeypatch)
    target = r'{"kind":"blocker","pr":"\u0032"}' if encoding == "escaped" else json.dumps({"kind": "blocker", "pr": 2 if encoding == "integer" else "2"})
    if encoding == "escaped":
        assert "\\u0032" in target
    with landing.book.store.transaction(runner_module.LANDING) as state:
        state.actions["unrelated-native-key"] = actions.Action(runner_module.LANDING, f"blocker:2:{2:040x}", "bus", target, None, 0, 0, actions.stamp(shell.now()), status="accepted")
    landing.unqueued_gos(rows, [])
    assert visited == ["1", "3"]
