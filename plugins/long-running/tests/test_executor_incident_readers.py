from collections import Counter
import json
import sqlite3
import threading

import pytest

from long_running import actions
from test_build_cache import readonly_connections
from test_desk_runner import config, landing_of, shell


def executor(store, shell, identity, *, status="open", adopted=True, not_ours=None, repair_pr=33001):
    facts = {"repair_pr": repair_pr, "not_ours": not_ours}
    if adopted:
        facts["adopted"] = {}
    incident = actions.Incident(identity, "owner", 0, actions.stamp(shell.now()), facts)
    incident.status = status
    store.create(incident)


def global_store(tmp_path, monkeypatch, name="home"):
    home = tmp_path / name
    monkeypatch.setenv("HOME", str(home))
    store = actions.Store()
    assert store.root == home / ".claude/long-running/incidents"
    return store


def selected(connections, store):
    prefix = store.root.absolute().as_uri() + "/"
    return [connection for connection in connections if connection.database.startswith(prefix)]


@pytest.mark.parametrize("history", [32, 256])
def test_executor_scan_reuses_each_incident_reader(shell, config, tmp_path, monkeypatch, readonly_connections, history, record_property):
    landing = landing_of(shell, config, tmp_path)
    store = global_store(tmp_path, monkeypatch)
    assert landing.book.store.root != store.root
    for number in range(history):
        executor(store, shell, f"settled-{number:04}", status="closed", repair_pr=99999)
    cleared = []
    monkeypatch.setattr(landing, "incident_cleared", lambda pr: cleared.append(pr))
    try:
        assert landing.is_incident("33001", {"lane": "ordinary-fix"}) is False
        first_opened = selected(readonly_connections, store)
        first = len(first_opened)
        record_property("caller_thread", threading.get_ident())
        record_property("first_reader_owners", json.dumps(dict(Counter(connection.owner for connection in first_opened))))
        record_property("first_database_count", len({connection.database for connection in first_opened}))
        record_property("open_readers_by_owner", json.dumps(dict(Counter(connection.owner for connection in first_opened if not connection.closed))))
        assert landing.is_incident("33001", {"lane": "ordinary-fix"}) is False
        opened = selected(readonly_connections, store)
        record_property("second_reader_owners", json.dumps(dict(Counter(connection.owner for connection in opened))))
        record_property("thread_names", json.dumps({thread.ident: thread.name for thread in threading.enumerate()}))
        print(json.dumps({"history": history, "first_readers": first, "two_pr_reads": len(opened), "closed": sum(connection.closed for connection in opened), "scope": "actual is_incident/private default Store; corpus scan remains linear"}))
        assert first == history, f"executor scan opened {first} readers for {history} incidents"
        assert len(opened) == 2 * history and all(connection.closed for connection in opened)
        assert cleared == []
    finally:
        landing.close()


@pytest.mark.parametrize(("status", "adopted", "not_ours", "repair_pr", "cleared", "expected"), [
    ("open", True, None, 33001, False, True),
    ("closed", True, None, 33001, False, False),
    ("recovered", True, None, 33001, False, False),
    ("waiting", True, False, "33001", False, True),
    ("open", False, None, 33001, False, False),
    ("open", True, "another owner's incident", 33001, False, False),
    ("open", True, {}, 33001, False, True),
    ("open", True, [], 33001, False, True),
    ("open", True, None, 33002, False, False),
    ("open", True, None, None, False, False),
    ("open", True, None, 33001, True, False),
])
def test_executor_reader_preserves_classification(shell, config, tmp_path, monkeypatch, readonly_connections, status, adopted, not_ours, repair_pr, cleared, expected):
    landing = landing_of(shell, config, tmp_path)
    store = global_store(tmp_path, monkeypatch)
    executor(store, shell, "one", status=status, adopted=adopted, not_ours=not_ours, repair_pr=repair_pr)
    checked = []
    monkeypatch.setattr(landing, "incident_cleared", lambda pr: checked.append(pr) or cleared)
    try:
        assert landing.is_incident("33001", {"lane": "ordinary-fix"}) is expected
        assert checked == (["33001"] if expected or cleared else [])
        assert all(connection.closed for connection in selected(readonly_connections, store))
    finally:
        landing.close()


def test_executor_reader_observes_old_writer_between_facts_and_header(shell, config, tmp_path, monkeypatch, readonly_connections):
    landing = landing_of(shell, config, tmp_path)
    store = global_store(tmp_path, monkeypatch)
    executor(store, shell, "one")
    facts = type(store).facts

    def changed(self, identity, keys=None):
        result = facts(self, identity, keys)
        writer = sqlite3.connect(self.path(identity).absolute().as_uri() + "?mode=rw", uri=True)
        try:
            writer.execute("UPDATE incident SET status='closed' WHERE singleton=1")
            writer.commit()
        finally:
            writer.close()
        return result

    monkeypatch.setattr(type(store), "facts", changed)
    monkeypatch.setattr(landing, "incident_cleared", lambda pr: pytest.fail("closed incident reached clearing"))
    try:
        assert landing.is_incident("33001", {"lane": "ordinary-fix"}) is False
        assert all(connection.closed for connection in selected(readonly_connections, store))
    finally:
        landing.close()


def test_executor_reader_uses_the_current_default_root_on_every_call(shell, config, tmp_path, monkeypatch, readonly_connections):
    landing = landing_of(shell, config, tmp_path)
    first = global_store(tmp_path, monkeypatch, "first")
    executor(first, shell, "one")
    monkeypatch.setattr(landing, "incident_cleared", lambda pr: False)
    try:
        assert landing.is_incident("33001", {"lane": "ordinary-fix"}) is True
        second = global_store(tmp_path, monkeypatch, "second")
        executor(second, shell, "one", status="closed")
        assert landing.is_incident("33001", {"lane": "ordinary-fix"}) is False
        assert all(connection.closed for connection in selected(readonly_connections, first) + selected(readonly_connections, second))
    finally:
        landing.close()


def test_executor_reader_closes_after_header_error(shell, config, tmp_path, monkeypatch, readonly_connections):
    landing = landing_of(shell, config, tmp_path)
    store = global_store(tmp_path, monkeypatch)
    executor(store, shell, "one")
    header = type(store).header

    def failed(self, identity):
        header(self, identity)
        raise ValueError("unreadable header")

    monkeypatch.setattr(type(store), "header", failed)
    try:
        with pytest.raises(ValueError, match="unreadable header"):
            landing.is_incident("33001", {"lane": "ordinary-fix"})
        assert all(connection.closed for connection in selected(readonly_connections, store))
    finally:
        landing.close()


@pytest.mark.parametrize("lane", ["incident-one", "known-fix"])
def test_executor_reader_keeps_lane_shortcuts(shell, config, tmp_path, monkeypatch, readonly_connections, lane):
    landing = landing_of(shell, config, tmp_path)
    store = global_store(tmp_path, monkeypatch)
    executor(store, shell, "one")
    landing.book.ensure("desk-lane-known-fix", "owner")
    monkeypatch.setattr(landing.runner, "fix_launch", lambda container: True)
    monkeypatch.setattr(landing, "incident_cleared", lambda pr: False)
    try:
        assert landing.is_incident("33001", {"lane": lane}) is True
        assert selected(readonly_connections, store) == []
    finally:
        landing.close()


def test_executor_reader_closes_on_first_matching_incident(shell, config, tmp_path, monkeypatch, readonly_connections):
    landing = landing_of(shell, config, tmp_path)
    store = global_store(tmp_path, monkeypatch)
    executor(store, shell, "a-matching")
    executor(store, shell, "z-unrelated", status="closed", repair_pr=99999)
    monkeypatch.setattr(landing, "incident_cleared", lambda pr: False)
    try:
        assert landing.is_incident("33001", {"lane": "ordinary-fix"}) is True
        opened = selected(readonly_connections, store)
        assert len(opened) == 1 and opened[0].closed
    finally:
        landing.close()
