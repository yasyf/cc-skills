"""One SQLite store per drive, owned by the supervisor.

Every wait in a drive is an open work item with a mover and a deadline, both ``NOT NULL``,
so an unowned or unbounded wait cannot be written. Grades, taps, holds and verdicts are
fact rows keyed by PR, head and kind: posting one twice changes nothing, a later value
supersedes an earlier one. An escalation is unique per subject and cause. Workers live in
the store from their first heartbeat and go stale after fifteen silent minutes. One lease
row per drive carries the generation a supervisor runs under; a stale generation is told
so on its next renewal. The lanes table is the registry of lanes the drive needs alive, with each
lane's brief, model, newest handoff and owned PRs; only the lanes mover writes it. The store's
one migration is additive: a column the schema declares that an older file lacks is added on open.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

STALE_AFTER = timedelta(minutes=15)
ADDED_COLUMNS = (("lanes", "waiting", "INTEGER"), ("lanes", "answered", "TEXT"), ("lanes", "page", "TEXT"), ("lanes", "awaits", "TEXT"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS prs (
  repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL, base TEXT NOT NULL,
  lane TEXT NOT NULL, state TEXT NOT NULL, read_at TEXT NOT NULL,
  PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL,
  mover TEXT NOT NULL, worker TEXT,
  created TEXT NOT NULL, deadline TEXT NOT NULL, closed TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS items_open ON items (repo, number, head, kind) WHERE closed IS NULL;
CREATE TABLE IF NOT EXISTS facts (
  repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL, kind TEXT NOT NULL,
  value TEXT NOT NULL, posted TEXT NOT NULL,
  PRIMARY KEY (repo, number, head, kind)
);
CREATE TABLE IF NOT EXISTS escalations (
  subject TEXT NOT NULL, cause TEXT NOT NULL, to_mover TEXT NOT NULL, raised TEXT NOT NULL,
  PRIMARY KEY (subject, cause)
);
CREATE TABLE IF NOT EXISTS workers (
  name TEXT PRIMARY KEY, generation INTEGER NOT NULL, heartbeat TEXT NOT NULL,
  item INTEGER, context_left INTEGER
);
CREATE TABLE IF NOT EXISTS lease (
  drive TEXT PRIMARY KEY, generation INTEGER NOT NULL, version TEXT NOT NULL,
  pid INTEGER NOT NULL, renewed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
  id TEXT PRIMARY KEY, words TEXT NOT NULL, said TEXT NOT NULL,
  check_kind TEXT CHECK (check_kind IN ('predicate', 'judgment')), check_ref TEXT,
  CHECK ((check_kind IS NULL) = (check_ref IS NULL))
);
CREATE TABLE IF NOT EXISTS cursors (source TEXT PRIMARY KEY, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lanes (
  name TEXT PRIMARY KEY, brief TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL, role TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('launching', 'desired', 'held', 'retired')),
  since TEXT NOT NULL, heard TEXT, handoff TEXT, prs TEXT NOT NULL DEFAULT '[]',
  predecessor TEXT, reason TEXT, strikes INTEGER NOT NULL DEFAULT 0, asked TEXT, flushed TEXT, retired_by TEXT, waiting INTEGER, answered TEXT, page TEXT, awaits TEXT
);
"""


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass(frozen=True)
class Item:
    id: int
    kind: str
    repo: str
    number: int
    head: str
    mover: str
    worker: str | None
    created: datetime
    deadline: datetime
    closed: datetime | None

    @classmethod
    def row(cls, row: sqlite3.Row) -> Item:
        return cls(row["id"], row["kind"], row["repo"], row["number"], row["head"], row["mover"], row["worker"], parse(row["created"]), parse(row["deadline"]), parse(row["closed"]) if row["closed"] else None)


@dataclass(frozen=True)
class Lane:
    name: str
    brief: str
    model: str
    effort: str
    role: str
    state: str
    since: datetime
    heard: datetime | None
    handoff: str | None
    prs: tuple[int, ...]
    predecessor: str | None
    reason: str | None
    strikes: int
    asked: datetime | None
    flushed: datetime | None
    retired_by: str | None
    waiting: int | None
    answered: datetime | None
    page: dict | None
    awaits: str | None

    @classmethod
    def row(cls, row: sqlite3.Row) -> Lane:
        when = {name: parse(row[name]) if row[name] else None for name in ("heard", "asked", "flushed", "answered")}
        return cls(row["name"], row["brief"], row["model"], row["effort"], row["role"], row["state"], parse(row["since"]), when["heard"], row["handoff"], tuple(json.loads(row["prs"])), row["predecessor"], row["reason"], row["strikes"], when["asked"], when["flushed"], row["retired_by"], row["waiting"], when["answered"], json.loads(row["page"]) if row["page"] else None, row["awaits"])


@dataclass(frozen=True)
class Lease:
    drive: str
    generation: int
    version: str
    pid: int
    renewed: datetime


class StaleGeneration(Exception):
    """The lease moved to a newer supervisor; the caller exits on this tick."""


class Store:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        for table, column, kind in ADDED_COLUMNS:
            if column not in {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    @classmethod
    def open(cls, path: Path) -> Store:
        db = sqlite3.connect(path, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return cls(db)

    @classmethod
    def memory(cls) -> Store:
        return cls(sqlite3.connect(":memory:", isolation_level=None))

    def put_pr(self, repo: str, number: int, head: str, base: str, lane: str, state: str, at: datetime) -> None:
        self.db.execute(
            "INSERT INTO prs (repo, number, head, base, lane, state, read_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number) DO UPDATE SET head = excluded.head, base = excluded.base, lane = excluded.lane, state = excluded.state, read_at = excluded.read_at",
            (repo, number, head, base, lane, state, stamp(at)),
        )

    def prs(self, repo: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM prs WHERE repo = ? ORDER BY number", (repo,)).fetchall()

    def open_item(self, kind: str, repo: str, number: int, head: str, mover: str, at: datetime, deadline: timedelta, worker: str | None = None) -> Item:
        self.db.execute(
            "INSERT INTO items (kind, repo, number, head, mover, worker, created, deadline) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, head, kind) WHERE closed IS NULL DO NOTHING",
            (kind, repo, number, head, mover, worker, stamp(at), stamp(at + deadline)),
        )
        return Item.row(self.db.execute("SELECT * FROM items WHERE repo = ? AND number = ? AND head = ? AND kind = ? AND closed IS NULL", (repo, number, head, kind)).fetchone())

    def close_item(self, item_id: int, at: datetime) -> None:
        self.db.execute("UPDATE items SET closed = ? WHERE id = ? AND closed IS NULL", (stamp(at), item_id))

    def close_items(self, repo: str, number: int, at: datetime, keep: str | None = None) -> int:
        cursor = self.db.execute("UPDATE items SET closed = ? WHERE repo = ? AND number = ? AND closed IS NULL AND kind IS NOT ?", (stamp(at), repo, number, keep))
        return cursor.rowcount

    def open_items(self, repo: str | None = None, number: int | None = None) -> list[Item]:
        rows = self.db.execute("SELECT * FROM items WHERE closed IS NULL AND (? IS NULL OR repo = ?) AND (? IS NULL OR number = ?) ORDER BY id", (repo, repo, number, number)).fetchall()
        return [Item.row(row) for row in rows]

    def overdue(self, now: datetime) -> list[Item]:
        rows = self.db.execute("SELECT * FROM items WHERE closed IS NULL AND deadline <= ? ORDER BY deadline, id", (stamp(now),)).fetchall()
        return [Item.row(row) for row in rows]

    def put_fact(self, repo: str, number: int, head: str, kind: str, value: str, at: datetime) -> bool:
        cursor = self.db.execute(
            "INSERT INTO facts (repo, number, head, kind, value, posted) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, head, kind) DO UPDATE SET value = excluded.value, posted = excluded.posted WHERE facts.value IS NOT excluded.value",
            (repo, number, head, kind, value, stamp(at)),
        )
        return cursor.rowcount == 1

    def facts(self, repo: str, number: int, head: str) -> dict[str, str]:
        rows = self.db.execute("SELECT kind, value FROM facts WHERE repo = ? AND number = ? AND head = ?", (repo, number, head)).fetchall()
        return {row["kind"]: row["value"] for row in rows}

    def escalate(self, subject: str, cause: str, to_mover: str, at: datetime) -> bool:
        cursor = self.db.execute("INSERT OR IGNORE INTO escalations (subject, cause, to_mover, raised) VALUES (?, ?, ?, ?)", (subject, cause, to_mover, stamp(at)))
        return cursor.rowcount == 1

    def escalations(self, subject: str | None = None) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM escalations WHERE ? IS NULL OR subject = ? ORDER BY raised", (subject, subject)).fetchall()

    def heartbeat(self, name: str, generation: int, at: datetime, item: int | None = None, context_left: int | None = None) -> None:
        self.db.execute(
            "INSERT INTO workers (name, generation, heartbeat, item, context_left) VALUES (?, ?, ?, ?, ?) ON CONFLICT (name) DO UPDATE SET generation = excluded.generation, heartbeat = excluded.heartbeat, item = excluded.item, context_left = excluded.context_left",
            (name, generation, stamp(at), item, context_left),
        )
        if item is not None:
            self.db.execute("UPDATE items SET worker = ? WHERE id = ? AND closed IS NULL", (name, item))

    def workers(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM workers ORDER BY name").fetchall()

    def stale_workers(self, now: datetime) -> list[str]:
        rows = self.db.execute("SELECT name FROM workers WHERE heartbeat <= ? ORDER BY name", (stamp(now - STALE_AFTER),)).fetchall()
        return [row["name"] for row in rows]

    def release_worker(self, name: str) -> list[Item]:
        held = [Item.row(row) for row in self.db.execute("SELECT * FROM items WHERE worker = ? AND closed IS NULL ORDER BY id", (name,)).fetchall()]
        self.db.execute("UPDATE items SET worker = NULL WHERE worker = ? AND closed IS NULL", (name,))
        self.db.execute("DELETE FROM workers WHERE name = ?", (name,))
        return held

    def acquire_lease(self, drive: str, version: str, pid: int, at: datetime) -> Lease:
        self.db.execute(
            "INSERT INTO lease (drive, generation, version, pid, renewed) VALUES (?, 1, ?, ?, ?) ON CONFLICT (drive) DO UPDATE SET generation = lease.generation + 1, version = excluded.version, pid = excluded.pid, renewed = excluded.renewed",
            (drive, version, pid, stamp(at)),
        )
        return self.lease(drive)

    def renew_lease(self, drive: str, generation: int, at: datetime) -> None:
        cursor = self.db.execute("UPDATE lease SET renewed = ? WHERE drive = ? AND generation = ?", (stamp(at), drive, generation))
        if cursor.rowcount == 0:
            raise StaleGeneration(f"{drive}: generation {generation} is behind {self.lease(drive).generation}")

    def lease(self, drive: str) -> Lease:
        row = self.db.execute("SELECT * FROM lease WHERE drive = ?", (drive,)).fetchone()
        return Lease(row["drive"], row["generation"], row["version"], row["pid"], parse(row["renewed"]))

    def put_rule(self, rule_id: str, words: str, said: str, check_kind: str | None = None, check_ref: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO rules (id, words, said, check_kind, check_ref) VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET words = excluded.words, said = excluded.said, check_kind = excluded.check_kind, check_ref = excluded.check_ref",
            (rule_id, words, said, check_kind, check_ref),
        )

    def unchecked_rules(self) -> list[str]:
        return [row["id"] for row in self.db.execute("SELECT id FROM rules WHERE check_kind IS NULL ORDER BY said, id").fetchall()]

    def cursor(self, source: str) -> int:
        row = self.db.execute("SELECT position FROM cursors WHERE source = ?", (source,)).fetchone()
        return row["position"] if row else 0

    def want_lane(self, name: str, brief: str, model: str, effort: str, role: str, state: str, at: datetime, **fields) -> bool:
        columns = {"name": name, "brief": brief, "model": model, "effort": effort, "role": role, "state": state, "since": stamp(at)} | {key: json.dumps(list(value)) if key == "prs" else value for key, value in fields.items()}
        cursor = self.db.execute(f"INSERT INTO lanes ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))}) ON CONFLICT (name) DO NOTHING", tuple(columns.values()))
        return cursor.rowcount == 1

    def lane(self, name: str) -> Lane | None:
        row = self.db.execute("SELECT * FROM lanes WHERE name = ?", (name,)).fetchone()
        return Lane.row(row) if row else None

    def lanes(self, *states: str) -> list[Lane]:
        rows = self.db.execute(f"SELECT * FROM lanes WHERE state IN ({', '.join('?' * len(states))}) ORDER BY name", states).fetchall()
        return [Lane.row(row) for row in rows]

    def all_lanes(self) -> list[Lane]:
        return [Lane.row(row) for row in self.db.execute("SELECT * FROM lanes ORDER BY name").fetchall()]

    def lane_state(self, name: str, state: str, at: datetime, retired_by: str | None = None, reason: str | None = None) -> None:
        self.db.execute("UPDATE lanes SET state = ?, since = ?, retired_by = coalesce(?, retired_by), reason = coalesce(?, reason) WHERE name = ?", (state, stamp(at), retired_by, reason, name))

    def lane_heard(self, name: str, at: datetime) -> None:
        self.db.execute("UPDATE lanes SET heard = ? WHERE name = ? AND (heard IS NULL OR heard < ?)", (stamp(at), name, stamp(at)))

    def lane_handoff(self, name: str, doc: str, flushed: datetime | None) -> None:
        self.db.execute("UPDATE lanes SET handoff = ?, flushed = coalesce(?, flushed) WHERE name = ?", (doc, stamp(flushed) if flushed else None, name))

    def lane_asked(self, name: str, at: datetime | None) -> None:
        self.db.execute("UPDATE lanes SET asked = ? WHERE name = ?", (stamp(at) if at else None, name))

    def lane_waiting(self, name: str, ask: int | None, answered: datetime | None = None, awaits: str | None = None) -> None:
        self.db.execute("UPDATE lanes SET waiting = ?, answered = ?, awaits = ? WHERE name = ?", (ask, stamp(answered) if answered else None, awaits, name))

    def lane_page(self, name: str, page: dict | None) -> None:
        self.db.execute("UPDATE lanes SET page = ? WHERE name = ?", (json.dumps(page) if page else None, name))

    def lane_prs(self, name: str, prs: Iterable[int]) -> None:
        self.db.execute("UPDATE lanes SET prs = ? WHERE name = ?", (json.dumps(sorted(set(prs))), name))

    def advance(self, source: str, position: int) -> None:
        self.db.execute("INSERT INTO cursors (source, position) VALUES (?, ?) ON CONFLICT (source) DO UPDATE SET position = max(cursors.position, excluded.position)", (source, position))
