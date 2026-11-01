from contextlib import nullcontext
from datetime import datetime, timezone
import sqlite3

import pytest

from long_running import actions, sqlite_store


@pytest.fixture
def store(tmp_path):
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    state = actions.Incident("runner", "owner", 0, actions.stamp(now), {"value": 1})
    state.accept("effect", "send", "target", "owner", now)
    store = sqlite_store.Store(tmp_path)
    store.create(state)
    return store


@pytest.fixture
def connections(monkeypatch):
    opened = []
    connect = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        def execute(self, statement, parameters=()):
            self.statements.append((statement, self.in_transaction))
            if self.before_execute is not None:
                self.before_execute(self, statement)
            result = super().execute(statement, parameters)
            if statement == "ROLLBACK":
                self.rollback_changes.append(self.total_changes)
            if self.after_execute is not None:
                self.after_execute(self, statement)
            return result

    def observed(*args, **kwargs):
        connection = connect(*args, **kwargs, factory=ObservedConnection)
        connection.statements = []
        connection.before_execute = None
        connection.after_execute = None
        connection.rollback_changes = []
        connection.readonly = str(args[0]).endswith("?mode=ro")
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed)
    return opened


@pytest.mark.parametrize("count", [32, 256])
@pytest.mark.parametrize("scoped", [False, True], ids=["unscoped", "scoped"])
@pytest.mark.parametrize("route", ["fact", "ownership", "actions"])
def test_every_reader_validation_uses_the_requested_snapshot(store, connections, count, scoped, route):
    def read():
        if route == "fact":
            assert store.fact("runner", "value") == 1
        elif route == "ownership":
            assert store.ownership("runner") == ("owner", 0)
        else:
            assert [action.action_id for action in store.actions("runner", "send", "accepted")] == ["effect"]

    with store.reading() if scoped else nullcontext():
        for _ in range(count):
            read()
        readers = [connection for connection in connections if connection.readonly]
        validations = [active for connection in readers for sql, active in connection.statements if sql == "PRAGMA user_version"]
        implicit = sum(not active for active in validations)
        assert implicit == 0, f"reader validation opened {implicit} implicit snapshots"
        assert len(validations) == count
        assert len(readers) == (1 if scoped else count)
        assert sum(sql == "BEGIN" for connection in readers for sql, _ in connection.statements) == count
        assert sum(sql == "ROLLBACK" for connection in readers for sql, _ in connection.statements) == count
        assert [changes for connection in readers for changes in connection.rollback_changes] == [0] * count
        if scoped:
            assert not readers[0].in_transaction
            assert readers[0].total_changes == 0
    for connection in readers:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("scoped", [False, True])
def test_read_validation_and_values_share_snapshot_then_see_external_version_change(store, connections, monkeypatch, scoped):
    writer = sqlite_store.Store(store.root)
    connect = store._connect
    changed = False

    def committed(connection, statement):
        nonlocal changed
        if statement == "PRAGMA user_version" and not changed:
            changed = True
            with writer.transaction("runner") as current:
                current.facts["value"] = 2
                current._connection.execute("PRAGMA user_version = 3")

    def opened(path, writable=False):
        connection = connect(path, writable)
        if not writable:
            connection.after_execute = committed
        return connection

    monkeypatch.setattr(store, "_connect", opened)
    with store.reading() if scoped else nullcontext():
        assert store.fact("runner", "value") == 1
        assert changed
        with pytest.raises(ValueError, match="unsupported action store version"):
            store.fact("runner", "value")
        if scoped:
            assert not store._readers.connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("failure", ["BEGIN", "PRAGMA user_version", "body"])
@pytest.mark.parametrize("scoped", [False, True])
def test_reader_failures_preserve_exception_rollback_and_close(store, connections, monkeypatch, failure, scoped):
    error = sqlite3.OperationalError(f"injected {failure}")
    connect = store._connect

    def failed(connection, statement):
        if statement == failure:
            raise error

    def opened(path, writable=False):
        connection = connect(path, writable)
        if not writable:
            connection.before_execute = failed
        return connection

    monkeypatch.setattr(store, "_connect", opened)
    with store.reading() if scoped else nullcontext():
        with pytest.raises(sqlite3.OperationalError) as caught:
            with store._read("runner"):
                assert failure == "body"
                raise error
        assert caught.value is error
        if scoped:
            assert not store._readers.connections
    assert len(connections) == 1
    reader = connections[0]
    assert sum(sql == "ROLLBACK" for sql, _ in reader.statements) == (0 if failure == "BEGIN" else 1)
    with pytest.raises(sqlite3.ProgrammingError):
        reader.execute("SELECT 1")


def test_writer_validation_still_refuses_before_transaction_and_closes(store, connections):
    connection = sqlite3.connect(store.path("runner"), isolation_level=None)
    try:
        connection.execute("PRAGMA user_version = 3")
    finally:
        connection.close()
    with pytest.raises(ValueError, match="unsupported action store version"):
        with store.transaction("runner"):
            pytest.fail("unsupported writer entered its transaction")
    writer = connections[-1]
    assert writer.statements == [("PRAGMA user_version", False)]
    with pytest.raises(sqlite3.ProgrammingError):
        writer.execute("SELECT 1")
