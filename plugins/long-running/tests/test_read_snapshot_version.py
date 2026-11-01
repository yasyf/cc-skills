import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from long_running import actions
from test_sqlite_action_store import NOW


def snapshot_store(tmp_path, value=1):
    store = actions.Store(tmp_path)
    store.create(actions.Incident("snapshot", "owner", 0, actions.stamp(NOW), {"value": value}))
    return store


@pytest.mark.parametrize("phase", ["PRAGMA user_version", "PRAGMA data_version"])
def test_version_labels_the_same_read_snapshot_across_concurrent_commit(tmp_path, monkeypatch, phase):
    store = snapshot_store(tmp_path)
    writer = actions.Store(tmp_path)
    connect = sqlite3.connect
    pending = [True]
    readers = []

    class FencedConnection(sqlite3.Connection):
        def execute(self, statement, parameters=()):
            self.statements.append((statement, self.in_transaction))
            result = super().execute(statement, parameters)
            if self.readonly and statement == phase and pending[0]:
                pending[0] = False
                with writer.transaction("snapshot") as state:
                    state.facts["value"] = 2
            return result

    def opened(database, *args, **kwargs):
        connection = connect(database, *args, **kwargs, factory=FencedConnection)
        connection.readonly = str(database).endswith("?mode=ro")
        connection.statements = []
        if connection.readonly:
            readers.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", opened)
    with store.reading():
        value, old = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        assert value == 1 and not pending[0]
        assert not store.unchanged("snapshot", old)
        value, current = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        assert value == 2 and store.unchanged("snapshot", current)
        assert len(readers) == 1 and readers[0].total_changes == 0 and not readers[0].in_transaction
        validations = [active for statement, active in readers[0].statements if statement in {"PRAGMA user_version", "PRAGMA data_version"}]
        assert validations and all(validations)
    with pytest.raises(sqlite3.ProgrammingError):
        readers[0].execute("SELECT 1")


def test_equal_connection_local_numbers_do_not_cross_connection_owners(tmp_path):
    store = snapshot_store(tmp_path)
    other = actions.Store(tmp_path)
    with store.reading(), other.reading():
        _, version = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        assert store.unchanged("snapshot", version)
        assert not other.unchanged("snapshot", version)


def test_version_does_not_cross_reading_scope_or_restart(tmp_path):
    store = snapshot_store(tmp_path)
    with store.reading():
        _, version = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
    with pytest.raises(sqlite3.ProgrammingError):
        version._connection.execute("SELECT 1")
    with store.reading():
        assert not store.unchanged("snapshot", version)
    successor = actions.Store(tmp_path)
    with successor.reading():
        assert not successor.unchanged("snapshot", version)


def test_version_does_not_cross_thread_read_owner(tmp_path):
    store = snapshot_store(tmp_path)
    def other_owner(version):
        with store.reading():
            return store.unchanged("snapshot", version)
    with store.reading():
        _, version = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        with ThreadPoolExecutor(1) as pool:
            assert not pool.submit(other_owner, version).result()
        assert store.unchanged("snapshot", version)


def test_version_is_invalidated_when_its_connection_is_evicted(tmp_path, monkeypatch):
    store = snapshot_store(tmp_path)
    for index in range(64):
        store.create(actions.Incident(f"other-{index}", "owner", 0, actions.stamp(NOW), {"value": index}))
    path = store.path("snapshot")
    monkeypatch.setattr(store._readers.eviction, "choice", lambda keys: path if path in keys else keys[0])
    with store.reading():
        _, version = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        for index in range(64):
            assert store.fact(f"other-{index}", "value") == index
        assert len(store._readers.connections) == 64
        with pytest.raises(sqlite3.ProgrammingError):
            version._connection.execute("SELECT 1")
        assert not store.unchanged("snapshot", version)
        assert len(store._readers.connections) == 64


def test_inode_replacement_cannot_reuse_a_prior_version(tmp_path):
    store = snapshot_store(tmp_path / "store")
    replacement = snapshot_store(tmp_path / "replacement", 3)
    with store.reading():
        value, version = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        assert value == 1
        writer = store._connect(store.path("snapshot"), writable=True)
        try:
            assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        finally:
            writer.close()
        replacement.path("snapshot").replace(store.path("snapshot"))
        assert not store.unchanged("snapshot", version)
        assert store.fact("snapshot", "value") == 3
        with pytest.raises(sqlite3.ProgrammingError):
            version._connection.execute("SELECT 1")


def test_schema_validation_and_callback_errors_still_close_the_reader(tmp_path):
    store = snapshot_store(tmp_path)
    writer = actions.Store(tmp_path)
    error = LookupError("private read cancellation")
    def failed(facts):
        raise error
    with store.reading():
        _, version = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        with pytest.raises(LookupError) as raised:
            store.read_facts_with_version("snapshot", failed)
        assert raised.value is error and not store._readers.connections
        with pytest.raises(sqlite3.ProgrammingError):
            version._connection.execute("SELECT 1")
        _, version = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        with writer.transaction("snapshot") as state:
            state._connection.execute("PRAGMA user_version = 3")
        with pytest.raises(ValueError, match="unsupported action store version"):
            store.unchanged("snapshot", version)
        assert not store._readers.connections


def test_rollback_changes_neither_values_nor_read_side_schema(tmp_path):
    store = snapshot_store(tmp_path)
    writer = actions.Store(tmp_path)
    with closing(sqlite3.connect(store.path("snapshot"))) as observer:
        schema = observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall()
    with store.reading():
        value, version = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        assert value == 1
        with pytest.raises(ValueError, match="private rollback"):
            with writer.transaction("snapshot") as state:
                state.facts["value"] = 2
                raise ValueError("private rollback")
        value, current = store.read_facts_with_version("snapshot", lambda facts: facts["value"])
        assert value == 1 and store.unchanged("snapshot", current)
        assert version._connection is current._connection
    with closing(sqlite3.connect(store.path("snapshot"))) as observer:
        assert observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall() == schema
