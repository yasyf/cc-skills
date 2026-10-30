from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path

from .model import Action, ActionState, Incident, StaleGeneration

_SCHEMA = """
CREATE TABLE incident (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    incident_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    owner_generation INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    input_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    pending_owner TEXT,
    action_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE action (
    ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
    action_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    record TEXT NOT NULL
);
CREATE INDEX action_kind ON action(kind, ordinal);
CREATE INDEX action_status ON action(status, ordinal);
CREATE INDEX action_kind_status ON action(kind, status, ordinal);
CREATE TRIGGER action_added AFTER INSERT ON action BEGIN
    UPDATE incident SET action_count = action_count + 1 WHERE singleton = 1;
END;
CREATE TRIGGER action_deleted AFTER DELETE ON action BEGIN
    UPDATE incident SET action_count = action_count - 1 WHERE singleton = 1;
END;
PRAGMA user_version = 1;
"""
_HEADER = ("incident_id", "owner", "owner_generation", "opened_at", "input_revision", "status", "pending_owner")
_METADATA = ("facts", "decisions", "milestones")


def _encode(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _action_record(action: Action) -> str:
    return _encode(asdict(action))


def _selected(kind: str | None, status: str | None) -> tuple[str, list[str]]:
    terms, values = [], []
    for name, value in (("kind", kind), ("status", status)):
        if value is not None:
            terms.append(f"{name} = ?")
            values.append(value)
    return (" WHERE " + " AND ".join(terms) if terms else ""), values


class _Actions(MutableMapping[str, Action]):
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.loaded: dict[str, Action] = {}
        self.original: dict[str, str] = {}
        self.ordinals: dict[str, int] = {}
        self.added: dict[str, Action] = {}
        self.deleted: set[str] = set()
        self.closed = False

    def _active(self) -> sqlite3.Connection:
        if self.closed:
            raise RuntimeError("unloaded transaction state requires an active edit context; use Store.load for a detached snapshot")
        return self.connection

    def _remember(self, row: sqlite3.Row) -> Action:
        key = row["action_key"]
        if key not in self.loaded:
            self.loaded[key] = Action.load(json.loads(row["record"]))
            self.original[key] = row["record"]
            self.ordinals[key] = row["ordinal"]
        return self.loaded[key]

    def __getitem__(self, key: str) -> Action:
        if key in self.added:
            return self.added[key]
        if key in self.deleted:
            raise KeyError(key)
        if key in self.loaded:
            return self.loaded[key]
        row = self._active().execute("SELECT action_key, ordinal, record FROM action WHERE action_key = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return self._remember(row)

    def __contains__(self, key: object) -> bool:
        if key in self.added:
            return True
        if key in self.deleted:
            return False
        if key in self.loaded:
            return True
        return self._active().execute("SELECT 1 FROM action WHERE action_key = ?", (key,)).fetchone() is not None

    def __setitem__(self, key: str, value: Action) -> None:
        self._active()
        if key in self.added:
            self.added[key] = value
        elif key in self:
            self[key]
            self.loaded[key] = value
        else:
            self.added[key] = value

    def __delitem__(self, key: str) -> None:
        self._active()
        if key in self.added:
            del self.added[key]
        elif key in self:
            self.deleted.add(key)
            self.loaded.pop(key, None)
        else:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        rows = self._active().execute("SELECT action_key FROM action ORDER BY ordinal")
        return iter([row[0] for row in rows if row[0] not in self.deleted] + list(self.added))

    def __len__(self) -> int:
        count = self._active().execute("SELECT action_count FROM incident WHERE singleton = 1").fetchone()[0]
        return count - len(self.deleted) + len(self.added)

    def _selection(self, kind: str | None = None, status: str | None = None) -> dict[str, Action]:
        where, parameters = _selected(kind, status)
        for row in self._active().execute("SELECT action_key, ordinal, record FROM action" + where + " ORDER BY ordinal", parameters):
            if row["action_key"] not in self.deleted:
                self._remember(row)
        selected = {
            key: value for key, value in self.loaded.items()
            if key not in self.deleted and (kind is None or value.kind == kind) and (status is None or value.status == status)
        }
        ordered = {key: selected[key] for key in sorted(selected, key=self.ordinals.__getitem__)}
        ordered.update((key, value) for key, value in self.added.items() if (kind is None or value.kind == kind) and (status is None or value.status == status))
        return ordered

    def values(self):
        return self._selection().values()

    def items(self):
        return self._selection().items()

    def select(self, kind: str | None = None, status: str | None = None) -> list[Action]:
        return list(self._selection(kind, status).values())

    def flush(self) -> None:
        connection = self._active()
        connection.executemany("DELETE FROM action WHERE action_key = ?", ((key,) for key in self.deleted))
        for key, action in self.loaded.items():
            record = _action_record(action)
            if record != self.original[key]:
                connection.execute("UPDATE action SET kind = ?, status = ?, record = ? WHERE action_key = ?", (action.kind, action.status, record, key))
        connection.executemany("INSERT INTO action(action_key, kind, status, record) VALUES (?, ?, ?, ?)", ((key, action.kind, action.status, _action_record(action)) for key, action in self.added.items()))


class Transaction(ActionState):
    """An incident unit of work; materialize snapshot() before retaining full state."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._metadata: dict[str, object] = {}
        self._original_metadata: dict[str, str] = {}
        row = connection.execute("SELECT " + ", ".join(_HEADER) + " FROM incident WHERE singleton = 1").fetchone()
        for name in _HEADER:
            setattr(self, name, row[name])
        self._original_header = tuple(getattr(self, name) for name in _HEADER)
        self._actions = _Actions(connection)
        self.actions = self._actions
        self._closed = False

    def _field(self, name: str):
        if name not in self._metadata:
            if self._closed:
                raise RuntimeError("unloaded transaction state requires an active edit context; use Store.load for a detached snapshot")
            record = self._connection.execute("SELECT value FROM metadata WHERE name = ?", (name,)).fetchone()[0]
            self._original_metadata[name] = record
            self._metadata[name] = json.loads(record)
        return self._metadata[name]

    @property
    def facts(self) -> dict:
        return self._field("facts")

    @facts.setter
    def facts(self, value: dict) -> None:
        self._metadata["facts"] = value

    @property
    def decisions(self) -> list[dict]:
        return self._field("decisions")

    @decisions.setter
    def decisions(self, value: list[dict]) -> None:
        self._metadata["decisions"] = value

    @property
    def milestones(self) -> list[dict]:
        return self._field("milestones")

    @milestones.setter
    def milestones(self, value: list[dict]) -> None:
        self._metadata["milestones"] = value

    def of_kind(self, kind: str) -> list[Action]:
        return self._actions.select(kind) if self.actions is self._actions else super().of_kind(kind)

    def snapshot(self) -> Incident:
        record = {entry.name: {key: asdict(value) for key, value in self.actions.items()} if entry.name == "actions" else getattr(self, entry.name) for entry in fields(Incident)}
        return Incident.load(json.loads(_encode(record)))

    def flush(self) -> None:
        current = tuple(getattr(self, name) for name in _HEADER)
        if current != self._original_header:
            self._connection.execute("UPDATE incident SET " + ", ".join(f"{name} = ?" for name in _HEADER) + " WHERE singleton = 1", current)
        for name, value in self._metadata.items():
            encoded = _encode(value)
            if encoded != self._original_metadata.get(name):
                self._connection.execute("UPDATE metadata SET value = ? WHERE name = ?", (encoded, name))
        if self.actions is not self._actions:
            self._connection.execute("DELETE FROM action")
            self._connection.executemany("INSERT INTO action(action_key, kind, status, record) VALUES (?, ?, ?, ?)", ((key, action.kind, action.status, _action_record(action)) for key, action in self.actions.items()))
        else:
            self._actions.flush()

    def close(self) -> None:
        self._closed = self._actions.closed = True


class Store:
    """Per-incident SQLite state with indexed detached reads and context-scoped edits."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, incident_id: str) -> Path:
        if not incident_id or "/" in incident_id or "\x00" in incident_id:
            raise ValueError("incident ID must be a nonempty filename component")
        return self.root / f"{incident_id}.sqlite3"

    @contextmanager
    def _locked(self, incident_id: str) -> Iterator[Path]:
        path = self.path(incident_id)
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / f".{incident_id}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield path

    def _connect(self, path: Path, writable: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(path.absolute().as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError(f"unsupported action store version: {path}")
            if writable:
                connection.execute("PRAGMA synchronous = FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def _read(self, incident_id: str) -> Iterator[sqlite3.Connection]:
        path = self.path(incident_id)
        if not path.is_file():
            raise SystemExit(f"no incident {incident_id} in {self.root}")
        connection = self._connect(path)
        try:
            connection.execute("BEGIN")
            yield connection
        finally:
            connection.close()

    def create(self, incident: Incident) -> Incident:
        with self._locked(incident.incident_id) as path:
            if path.exists():
                return self.load(incident.incident_id)
            descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.root)
            os.close(descriptor)
            staged = Path(staged_name)
            connection = sqlite3.connect(staged, isolation_level=None)
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.executescript(_SCHEMA)
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO incident(singleton, " + ", ".join(_HEADER) + ") VALUES (1, " + ", ".join("?" for _ in _HEADER) + ")", tuple(getattr(incident, name) for name in _HEADER))
                connection.executemany("INSERT INTO metadata(name, value) VALUES (?, ?)", ((name, _encode(getattr(incident, name))) for name in _METADATA))
                connection.executemany("INSERT INTO action(action_key, kind, status, record) VALUES (?, ?, ?, ?)", ((key, action.kind, action.status, _action_record(action)) for key, action in incident.actions.items()))
                connection.execute("COMMIT")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.close()
                with staged.open("rb") as stream:
                    os.fsync(stream.fileno())
                staged.replace(path)
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                connection.close()
                staged.unlink(missing_ok=True)
            return incident

    def load(self, incident_id: str) -> Incident:
        with self._read(incident_id) as connection:
            record = dict(connection.execute("SELECT " + ", ".join(_HEADER) + " FROM incident WHERE singleton = 1").fetchone())
            record.update((row["name"], json.loads(row["value"])) for row in connection.execute("SELECT name, value FROM metadata"))
            record["actions"] = {row["action_key"]: json.loads(row["record"]) for row in connection.execute("SELECT action_key, record FROM action ORDER BY ordinal")}
            return Incident.load(record)

    def ids(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.sqlite3"))

    def find_action(self, incident_id: str, action_id: str) -> Action | None:
        with self._read(incident_id) as connection:
            row = connection.execute("SELECT record FROM action WHERE action_key = ?", (action_id,)).fetchone()
            return Action.load(json.loads(row[0])) if row is not None else None

    def actions(self, incident_id: str, kind: str | None = None, status: str | None = None) -> list[Action]:
        where, parameters = _selected(kind, status)
        with self._read(incident_id) as connection:
            return [Action.load(json.loads(row[0])) for row in connection.execute("SELECT record FROM action" + where + " ORDER BY ordinal", parameters)]

    def ownership(self, incident_id: str) -> tuple[str, int]:
        with self._read(incident_id) as connection:
            row = connection.execute("SELECT owner, owner_generation FROM incident WHERE singleton = 1").fetchone()
            return row[0], row[1]

    @contextmanager
    def transaction(self, incident_id: str, *, generation: int | None = None, inputs: bool = False) -> Iterator[Transaction]:
        with self._locked(incident_id) as path:
            if not path.is_file():
                raise SystemExit(f"no incident {incident_id} in {self.root}")
            connection = self._connect(path, writable=True)
            transaction = None
            try:
                connection.execute("BEGIN IMMEDIATE")
                transaction = Transaction(connection)
                if generation is not None and transaction.owner_generation != generation:
                    raise StaleGeneration(f"{incident_id} is at owner generation {transaction.owner_generation} ({transaction.owner}), not {generation}")
                yield transaction
                if inputs:
                    transaction.input_revision += 1
                transaction.flush()
                connection.execute("COMMIT")
            finally:
                if transaction is not None:
                    transaction.close()
                connection.close()

    def transfer(self, incident_id: str, expect_generation: int, to: str) -> Incident:
        with self.transaction(incident_id, generation=expect_generation) as incident:
            incident.pending_owner = to
            snapshot = incident.snapshot()
        return snapshot

    def ack(self, incident_id: str, owner: str) -> Incident:
        with self.transaction(incident_id) as incident:
            if incident.pending_owner != owner:
                raise SystemExit(f"{incident_id} has no pending transfer to {owner} (pending: {incident.pending_owner})")
            incident.owner, incident.pending_owner = owner, None
            incident.owner_generation += 1
            snapshot = incident.snapshot()
        return snapshot
