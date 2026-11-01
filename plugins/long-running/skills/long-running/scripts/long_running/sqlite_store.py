from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from random import Random

from .model import Action, ActionState, Incident, StaleGeneration, UnknownAction

_COMPACT_RECORD = "replace(replace(replace(replace(record, ' ', ''), char(9), ''), char(10), ''), char(13), '')"
_COMPACT_TARGET = "replace(replace(replace(replace(json_extract(record, '$.target'), ' ', ''), char(9), ''), char(10), ''), char(13), '')"
_BLOCKER_CANDIDATES = rf"""
CASE WHEN kind = 'bus' AND status = 'completed' THEN
    CASE
        WHEN NOT json_valid(record) THEN 1
        WHEN json_type(record, '$.target') IS NOT 'text' THEN 1
        WHEN NOT json_valid(json_extract(record, '$.target')) THEN 1
        WHEN json_type(json_extract(record, '$.target')) IS NOT 'object' THEN 1
        WHEN instr(lower(record), '\u006') OR instr(lower(record), '\u007') THEN 1
        WHEN length({_COMPACT_RECORD}) - length(replace({_COMPACT_RECORD}, '"target":', '')) > 9 THEN 1
        WHEN length({_COMPACT_TARGET}) - length(replace({_COMPACT_TARGET}, '"kind":', '')) > 7 THEN 1
        ELSE json_extract(json_extract(record, '$.target'), '$.kind') = 'blocker'
    END
ELSE 0 END
"""
_DIRECT_MERGE_CANDIDATES = rf"""
CASE WHEN kind = 'bus' THEN
    CASE
        WHEN NOT json_valid(record) THEN 1
        WHEN json_type(record, '$.action_id') IS NOT 'text' THEN 1
        WHEN instr(lower(record), '\u006') OR instr(lower(record), '\u007') OR instr(lower(record), '\u005f') THEN 1
        WHEN length({_COMPACT_RECORD}) - length(replace({_COMPACT_RECORD}, '"action_id":', '')) > 12 THEN 1
        ELSE substr(json_extract(record, '$.action_id'), 1, 14) = 'merged-direct:'
    END
ELSE 0 END
"""
_SCHEMA = f"""
CREATE TABLE incident (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    incident_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    owner_generation INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    input_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    pending_owner TEXT,
    action_count INTEGER NOT NULL DEFAULT 0,
    fact_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE fact (
    ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    parent TEXT NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    record TEXT,
    child_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX fact_parent ON fact(parent, ordinal);
CREATE INDEX fact_value ON fact(key, record, parent) WHERE kind = 'value';
CREATE TRIGGER fact_added AFTER INSERT ON fact BEGIN
    UPDATE incident SET fact_count = fact_count + 1 WHERE singleton = 1 AND NEW.parent = '';
    UPDATE fact SET child_count = child_count + 1 WHERE path = NEW.parent;
END;
CREATE TRIGGER fact_deleted AFTER DELETE ON fact BEGIN
    UPDATE incident SET fact_count = fact_count - 1 WHERE singleton = 1 AND OLD.parent = '';
    UPDATE fact SET child_count = child_count - 1 WHERE path = OLD.parent;
END;
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
CREATE INDEX action_blocker_candidates ON action(ordinal) WHERE {_BLOCKER_CANDIDATES};
CREATE INDEX action_direct_merge_candidates ON action(ordinal) WHERE {_DIRECT_MERGE_CANDIDATES};
CREATE TRIGGER action_added AFTER INSERT ON action BEGIN
    UPDATE incident SET action_count = action_count + 1 WHERE singleton = 1;
END;
CREATE TRIGGER action_deleted AFTER DELETE ON action BEGIN
    UPDATE incident SET action_count = action_count - 1 WHERE singleton = 1;
END;
PRAGMA user_version = 2;
"""
_HEADER = ("incident_id", "owner", "owner_generation", "opened_at", "input_revision", "status", "pending_owner")
_METADATA = ("decisions", "milestones")


def _encode(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _action_record(action: Action) -> str:
    return _encode(asdict(action))


def detached(value):
    if isinstance(value, Mapping):
        return {key: detached(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(detached(item) for item in value)
    if isinstance(value, list):
        return [detached(item) for item in value]
    return deepcopy(value)


def _fact_path(parent: str, key: str) -> str:
    return parent + "/" + key.replace("~", "~0").replace("/", "~1")


class _FactTree:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.loaded: dict[str, object] = {}
        self.original: dict[str, str] = {}
        self.cached_children: dict[str, set[str]] = {}
        self.versions: dict[str, int] = {}
        self.closed = False

    def active(self) -> sqlite3.Connection:
        if self.closed:
            raise RuntimeError("fact state requires an active transaction; retain a detached value instead")
        return self.connection

    def remember(self, path: str, value, record: str) -> None:
        self.loaded[path], self.original[path] = value, record
        while path:
            parent = path.rsplit("/", 1)[0]
            self.cached_children.setdefault(parent, set()).add(path)
            path = parent

    def forget(self, path: str) -> None:
        parent = path.rsplit("/", 1)[0]
        if parent in self.cached_children:
            self.cached_children[parent].discard(path)
        pending = [path]
        while pending:
            selected = pending.pop()
            pending.extend(self.cached_children.pop(selected, ()))
            self.loaded.pop(selected, None)
            self.original.pop(selected, None)

    def version(self, path: str) -> tuple[int, ...]:
        versions = []
        while path:
            versions.append(self.versions.get(path, 0))
            path = path.rsplit("/", 1)[0]
        return tuple(versions)

    def invalidate(self, path: str) -> None:
        self.versions[path] = self.versions.get(path, 0) + 1
        self.forget(path)

    def remove(self, path: str, descendants_only: bool = False) -> None:
        query = "SELECT path FROM fact WHERE parent = ?" if descendants_only else "SELECT path FROM fact WHERE path = ?"
        self.active().execute("WITH RECURSIVE subtree(path) AS (" + query + " UNION ALL SELECT fact.path FROM fact JOIN subtree ON fact.parent = subtree.path) DELETE FROM fact WHERE path IN (SELECT path FROM subtree)", (path,))
        self.invalidate(path)

    def replace(self, parent: str, value: Mapping) -> None:
        value = detached(value)
        for key in tuple(_Facts(self, parent)):
            if key not in value:
                self.remove(_fact_path(parent, key))
        for key, item in value.items():
            self.set(parent, key, item)
        if tuple(_Facts(self, parent)) != tuple(value):
            ordinal = self.active().execute("SELECT MAX(ordinal) FROM fact").fetchone()[0]
            self.connection.executemany("UPDATE fact SET ordinal = ? WHERE path = ?", ((ordinal + index, _fact_path(parent, key)) for index, key in enumerate(value, 1)))

    def set(self, parent: str, key: str, value) -> None:
        path = _fact_path(parent, key)
        value = detached(value) if isinstance(value, Mapping) else value
        self.invalidate(path)
        row = self.active().execute("SELECT kind, record FROM fact WHERE path = ?", (path,)).fetchone()
        kind = "dict" if isinstance(value, dict) else "value"
        record = None if kind == "dict" else _encode(value)
        if row is None:
            self.connection.execute("INSERT INTO fact(path, parent, key, kind, record) VALUES (?, ?, ?, ?, ?)", (path, parent, key, kind, record))
        elif row["kind"] != kind or row["record"] != record:
            if row["kind"] == "dict":
                self.remove(path, descendants_only=True)
            self.connection.execute("UPDATE fact SET kind = ?, record = ? WHERE path = ?", (kind, record, path))
        if kind == "dict":
            self.replace(path, value)
        else:
            self.remember(path, value, record)

    def flush(self) -> None:
        for path, value in self.loaded.items():
            record = _encode(value)
            if record != self.original[path]:
                self.active().execute("UPDATE fact SET record = ? WHERE path = ?", (record, path))


class _Facts(MutableMapping[str, object]):
    def __init__(self, tree: _FactTree, parent: str = ""):
        self.tree, self.parent = tree, parent
        self.identity = (tree.active().execute("SELECT ordinal FROM fact WHERE path = ?", (parent,)).fetchone()[0], tree.version(parent)) if parent else None

    def _connection(self):
        connection = self.tree.active()
        if self.parent:
            row = connection.execute("SELECT ordinal, kind FROM fact WHERE path = ?", (self.parent,)).fetchone()
            if row is None or (row[0], self.tree.version(self.parent)) != self.identity or row[1] != "dict":
                raise RuntimeError("fact mapping was replaced or removed during the transaction")
        return connection

    def __getitem__(self, key: str):
        self._connection()
        path = _fact_path(self.parent, key)
        if path in self.tree.loaded:
            return self.tree.loaded[path]
        row = self._connection().execute("SELECT kind, record FROM fact WHERE path = ?", (path,)).fetchone()
        if row is None:
            raise KeyError(key)
        if row["kind"] == "dict":
            return _Facts(self.tree, path)
        self.tree.remember(path, json.loads(row["record"]), row["record"])
        return self.tree.loaded[path]

    def __setitem__(self, key: str, value) -> None:
        self._connection()
        self.tree.set(self.parent, key, value)

    def __delitem__(self, key: str) -> None:
        if key not in self:
            raise KeyError(key)
        self.tree.remove(_fact_path(self.parent, key))

    def __contains__(self, key: object) -> bool:
        return self._connection().execute("SELECT 1 FROM fact WHERE path = ?", (_fact_path(self.parent, key),)).fetchone() is not None

    def __iter__(self) -> Iterator[str]:
        return iter([row[0] for row in self._connection().execute("SELECT key FROM fact WHERE parent = ? ORDER BY ordinal", (self.parent,))])

    def __len__(self) -> int:
        if self.parent:
            return self._connection().execute("SELECT child_count FROM fact WHERE path = ?", (self.parent,)).fetchone()[0]
        return self._connection().execute("SELECT fact_count FROM incident WHERE singleton = 1").fetchone()[0]

    def pop(self, key: str, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = detached(self[key])
        del self[key]
        return value

    def setdefault(self, key: str, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def __ior__(self, other):
        self.update(other)
        return self


def _selected(kind: str | None, status: str | None, statuses=None) -> tuple[str, list[str]]:
    terms, values = [], []
    for name, value in (("kind", kind), ("status", status)):
        if value is not None:
            terms.append(f"{name} = ?")
            values.append(value)
    if statuses is not None:
        statuses = tuple(statuses)
        terms.append("status IN (" + ", ".join("?" for _ in statuses) + ")" if statuses else "0")
        values.extend(statuses)
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

    def family(self, base: str) -> list[Action]:
        for row in self._active().execute("SELECT action_key, ordinal, record FROM action WHERE action_key = ? OR (action_key >= ? AND action_key < ?) ORDER BY ordinal", (base, base + "#", base + "$")):
            if row["action_key"] not in self.deleted:
                self._remember(row)
        selected = {key: action for key, action in self.loaded.items() if key not in self.deleted and (key == base or key.startswith(base + "#"))}
        ordered = [selected[key] for key in sorted(selected, key=self.ordinals.__getitem__)]
        return ordered + [action for key, action in self.added.items() if key == base or key.startswith(base + "#")]

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
        self._fact_tree = _FactTree(connection)
        self._facts = _Facts(self._fact_tree)
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
    def facts(self) -> MutableMapping[str, object]:
        self._fact_tree.active()
        return self._facts

    @facts.setter
    def facts(self, value: Mapping) -> None:
        if value is self._facts:
            return
        self._fact_tree.replace("", detached(value))

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

    def action_family(self, base: str) -> list[Action]:
        if self.actions is self._actions:
            return self._actions.family(base)
        return [action for key, action in self.actions.items() if key == base or key.startswith(base + "#")]

    def fact_keys_before(self, key: str, field: str, before: str) -> list[str]:
        """Keys of child dictionaries whose named string field sorts before the bound."""
        parent = _fact_path("", key)
        selected = self._fact_tree.active().execute("SELECT child.key FROM fact AS leaf JOIN fact AS child ON child.path = leaf.parent WHERE leaf.kind = 'value' AND leaf.key = ? AND leaf.record < ? AND leaf.parent >= ? AND leaf.parent < ? AND child.parent = ? ORDER BY child.ordinal", (field, _encode(before), parent + "/", parent + "0", parent))
        return [row["key"] for row in selected]

    def snapshot(self) -> Incident:
        record = {entry.name: {key: asdict(value) for key, value in self.actions.items()} if entry.name == "actions" else detached(getattr(self, entry.name)) for entry in fields(Incident)}
        return Incident.load(record)

    def flush(self) -> None:
        current = tuple(getattr(self, name) for name in _HEADER)
        if current != self._original_header:
            self._connection.execute("UPDATE incident SET " + ", ".join(f"{name} = ?" for name in _HEADER) + " WHERE singleton = 1", current)
        self._fact_tree.flush()
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
        self._closed = self._actions.closed = self._fact_tree.closed = True


def _incident_path(root: Path, incident_id: str) -> Path:
    if not incident_id or "/" in incident_id or "\x00" in incident_id:
        raise ValueError("incident ID must be a nonempty filename component")
    return root / f"{incident_id}.sqlite3"


def _load(connection: sqlite3.Connection) -> Incident:
    record = dict(connection.execute("SELECT " + ", ".join(_HEADER) + " FROM incident WHERE singleton = 1").fetchone())
    record.update((row["name"], json.loads(row["value"])) for row in connection.execute("SELECT name, value FROM metadata"))
    record["facts"] = detached(_Facts(_FactTree(connection)))
    record["actions"] = {row["action_key"]: json.loads(row["record"]) for row in connection.execute("SELECT action_key, record FROM action ORDER BY ordinal")}
    return Incident.load(record)


def _create_database(root: Path, incident: Incident) -> Incident:
    path = _incident_path(root, incident.incident_id)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=root)
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
        _Facts(_FactTree(connection)).update(incident.facts)
        connection.executemany("INSERT INTO action(action_key, kind, status, record) VALUES (?, ?, ?, ?)", ((key, action.kind, action.status, _action_record(action)) for key, action in incident.actions.items()))
        connection.execute("COMMIT")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        staged.replace(path)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        connection.close()
        staged.unlink(missing_ok=True)
    return incident


@dataclass(frozen=True)
class ReadVersion:
    _connection: sqlite3.Connection = field(repr=False)
    _value: int


def _read_version(connection: sqlite3.Connection) -> ReadVersion:
    return ReadVersion(connection, connection.execute("PRAGMA data_version").fetchone()[0])


def _read_facts(connection: sqlite3.Connection, read: Callable[[Mapping[str, object]], object]) -> object:
    tree = _FactTree(connection)
    try:
        return detached(read(_Facts(tree)))
    finally:
        tree.closed = True


class _ReadConnections(threading.local):
    def __init__(self):
        self.connections: OrderedDict[Path, tuple[tuple[int, int], sqlite3.Connection]] | None = None
        self.eviction = Random(0)


class Store:
    """Per-incident SQLite state with indexed detached reads and context-scoped edits."""

    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else Path.home() / ".claude" / "long-running" / "incidents"
        self._readers = _ReadConnections()

    def path(self, incident_id: str) -> Path:
        return _incident_path(self.root, incident_id)

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
            if writable and connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise ValueError(f"unsupported action store version: {path}")
            if writable:
                connection.execute("PRAGMA synchronous = FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def reading(self) -> Iterator[None]:
        """Reuse at most 64 read-only connections on this thread, with a fresh transaction for every read."""
        if self._readers.connections is not None:
            yield
            return
        connections = OrderedDict()
        self._readers.connections = connections
        try:
            yield
        finally:
            self._readers.connections = None
            for _, connection in connections.values():
                connection.close()

    def _reader(self, path: Path) -> sqlite3.Connection:
        connections = self._readers.connections
        if connections is None:
            return self._connect(path)
        state = path.stat()
        identity = state.st_dev, state.st_ino
        existing = connections.pop(path, None)
        if existing is not None:
            bound, connection = existing
            if bound == identity:
                connections[path] = existing
                return connection
            connection.close()
        if len(connections) == 64:
            _, retired = connections.pop(self._readers.eviction.choice(tuple(connections)))
            retired.close()
        connection = self._connect(path)
        connections[path] = identity, connection
        return connection

    @contextmanager
    def _read(self, incident_id: str) -> Iterator[sqlite3.Connection]:
        path = self.path(incident_id)
        connections = self._readers.connections
        if not path.is_file():
            if connections is not None and (existing := connections.pop(path, None)) is not None:
                existing[1].close()
            raise SystemExit(f"no incident {incident_id} in {self.root}")
        connection = self._reader(path)
        try:
            connection.execute("BEGIN")
            try:
                if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                    raise ValueError(f"unsupported action store version: {path}")
                yield connection
            finally:
                connection.execute("ROLLBACK")
        except BaseException:
            if connections is not None:
                connections.pop(path)
            connection.close()
            raise
        else:
            if connections is None:
                connection.close()

    def _create(self, incident: Incident, existing_snapshot: bool) -> Incident | None:
        with self._locked(incident.incident_id) as path:
            if path.exists():
                return self.load(incident.incident_id) if existing_snapshot else None
            return _create_database(self.root, incident)

    def create(self, incident: Incident) -> Incident:
        return self._create(incident, existing_snapshot=True)

    def ensure(self, incident: Incident) -> None:
        self._create(incident, existing_snapshot=False)

    def exists(self, incident_id: str) -> bool:
        return self.path(incident_id).is_file()

    def header(self, incident_id: str) -> dict:
        with self._read(incident_id) as connection:
            return dict(connection.execute("SELECT " + ", ".join(_HEADER) + " FROM incident WHERE singleton = 1").fetchone())

    def fact(self, incident_id: str, key: str, *default):
        return self.fact_path(incident_id, (key,), *default)

    def fact_path(self, incident_id: str, keys, *default):
        with self._read(incident_id) as connection:
            selected = _Facts(_FactTree(connection))
            try:
                for key in keys:
                    selected = selected[key]
                return detached(selected)
            except KeyError:
                if default:
                    return default[0]
                raise

    def facts(self, incident_id: str, keys=None) -> dict:
        with self._read(incident_id) as connection:
            facts = _Facts(_FactTree(connection))
            return {key: detached(facts[key]) for key in (facts if keys is None else keys) if key in facts}

    def read_facts(self, incident_id: str, read: Callable[[Mapping[str, object]], object]) -> object:
        """Detach a selection from a single fresh view of the lazy facts."""
        with self._read(incident_id) as connection:
            return _read_facts(connection, read)

    def read_facts_with_version(self, incident_id: str, read: Callable[[Mapping[str, object]], object]) -> tuple[object, ReadVersion]:
        with self._read(incident_id) as connection:
            version = _read_version(connection)
            return _read_facts(connection, read), version

    def unchanged(self, incident_id: str, version: ReadVersion) -> bool:
        with self._read(incident_id) as connection:
            return connection is version._connection and connection.execute("PRAGMA data_version").fetchone()[0] == version._value

    def fact_children(self, incident_id: str, key: str, keys) -> dict:
        """Named child values of a fact, detached from one fresh read snapshot."""
        with self._read(incident_id) as connection:
            children = _Facts(_FactTree(connection)).get(key) or {}
            available = children.keys()
            return {child: detached(children[child]) for child in keys if child in available}

    def fact_entries(self, incident_id: str, key: str, field: str, values) -> dict:
        parent = _fact_path("", key)
        encoded = [_encode(value) for value in values]
        if not encoded:
            return {}
        with self._read(incident_id) as connection:
            selected = connection.execute("SELECT child.key, child.path FROM fact AS leaf JOIN fact AS child ON child.path = leaf.parent WHERE leaf.kind = 'value' AND leaf.key = ? AND leaf.record IN (" + ",".join("?" for _ in encoded) + ") AND leaf.parent >= ? AND leaf.parent < ? AND child.parent = ? ORDER BY child.ordinal", (field, *encoded, parent + "/", parent + "0", parent))
            tree = _FactTree(connection)
            return {row["key"]: detached(_Facts(tree, row["path"])) for row in selected}

    def metadata(self, incident_id: str, name: str):
        if name not in _METADATA:
            raise ValueError(name)
        with self._read(incident_id) as connection:
            return json.loads(connection.execute("SELECT value FROM metadata WHERE name = ?", (name,)).fetchone()[0])

    def load(self, incident_id: str) -> Incident:
        with self._read(incident_id) as connection:
            return _load(connection)

    def ids(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.sqlite3"))

    def find_action(self, incident_id: str, action_id: str) -> Action | None:
        with self._read(incident_id) as connection:
            row = connection.execute("SELECT record FROM action WHERE action_key = ?", (action_id,)).fetchone()
            return Action.load(json.loads(row[0])) if row is not None else None

    def action(self, incident_id: str, action_key: str) -> Action:
        action = self.find_action(incident_id, action_key)
        if action is None:
            raise UnknownAction(action_key)
        return action

    def action_family(self, incident_id: str, base: str) -> list[Action]:
        with self._read(incident_id) as connection:
            return [Action.load(json.loads(row[0])) for row in connection.execute("SELECT record FROM action WHERE action_key = ? OR (action_key >= ? AND action_key < ?) ORDER BY ordinal", (base, base + "#", base + "$"))]

    def actions(self, incident_id: str, kind: str | None = None, status: str | None = None, *, statuses=None) -> list[Action]:
        where, parameters = _selected(kind, status, statuses)
        index = " INDEXED BY action_kind_status" if kind is not None and statuses is not None else ""
        with self._read(incident_id) as connection:
            return [Action.load(json.loads(row[0])) for row in connection.execute("SELECT record FROM action" + index + where + " ORDER BY ordinal", parameters)]

    def actions_with_version(self, incident_id: str, kind: str | None = None, status: str | None = None, *, statuses=None) -> tuple[list[Action], ReadVersion]:
        where, parameters = _selected(kind, status, statuses)
        index = " INDEXED BY action_kind_status" if kind is not None and statuses is not None else ""
        with self._read(incident_id) as connection:
            version = _read_version(connection)
            return [Action.load(json.loads(row[0])) for row in connection.execute("SELECT record FROM action" + index + where + " ORDER BY ordinal", parameters)], version

    def blocker_candidates(self, incident_id: str) -> list[Action]:
        with self._read(incident_id) as connection:
            return [Action.load(json.loads(row[0])) for row in connection.execute("SELECT record FROM action WHERE " + _BLOCKER_CANDIDATES + " ORDER BY ordinal")]

    def direct_merge_candidates(self, incident_id: str) -> list[Action]:
        with self._read(incident_id) as connection:
            return [Action.load(json.loads(row[0])) for row in connection.execute("SELECT record FROM action WHERE " + _DIRECT_MERGE_CANDIDATES + " ORDER BY ordinal")]

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
