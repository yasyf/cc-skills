"""The drive store refuses the states tonight's runner held: unowned waits, unbounded waits, repeated escalations, two writers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from lr.store import StaleGeneration, Store, stamp

T0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
HEAD = "e718a7434fa01ee9835f992a9a647a825350732c"
REPO = "org/repo"


@pytest.fixture
def store() -> Store:
    return Store.memory()


def test_an_item_without_a_mover_cannot_be_written(store):
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL.*mover"):
        store.db.execute("INSERT INTO items (kind, repo, number, head, mover, created, deadline) VALUES ('enqueue', ?, 1, ?, NULL, '2026-10-30T21:00:00Z', '2026-10-30T21:10:00Z')", (REPO, HEAD))


def test_an_item_without_a_deadline_cannot_be_written(store):
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL.*deadline"):
        store.db.execute("INSERT INTO items (kind, repo, number, head, mover, created, deadline) VALUES ('enqueue', ?, 1, ?, 'supervisor', '2026-10-30T21:00:00Z', NULL)", (REPO, HEAD))


def test_opening_the_same_wait_twice_keeps_one_item(store):
    first = store.open_item("enqueue", REPO, 45583, HEAD, "supervisor", T0, timedelta(minutes=10))
    again = store.open_item("enqueue", REPO, 45583, HEAD, "supervisor", T0 + timedelta(minutes=3), timedelta(minutes=10))

    assert again == first
    assert again.deadline == T0 + timedelta(minutes=10)
    assert [item.id for item in store.open_items(REPO, 45583)] == [first.id]


def test_a_closed_wait_can_reopen_as_a_new_item(store):
    first = store.open_item("restack", REPO, 45410, HEAD, "supervisor", T0, timedelta(minutes=10))
    store.close_item(first.id, T0 + timedelta(minutes=4))
    second = store.open_item("restack", REPO, 45410, HEAD, "supervisor", T0 + timedelta(hours=1), timedelta(minutes=10))

    assert second.id != first.id
    assert store.open_items(REPO, 45410) == [second]


def test_overdue_lists_only_items_past_their_deadline(store):
    late = store.open_item("verdict", REPO, 45470, HEAD, "lane", T0, timedelta(minutes=20))
    store.open_item("grade", REPO, 45489, HEAD, "pool", T0 + timedelta(minutes=15), timedelta(minutes=10))

    assert [item.id for item in store.overdue(T0 + timedelta(minutes=21))] == [late.id]
    assert store.overdue(T0 + timedelta(minutes=19)) == []


def test_a_state_change_closes_every_other_wait_on_the_pr(store):
    store.open_item("tap", REPO, 45583, HEAD, "owner", T0, timedelta(hours=12))
    store.open_item("grade", REPO, 45583, HEAD, "pool", T0, timedelta(minutes=10))
    kept = store.open_item("enqueue", REPO, 45583, HEAD, "supervisor", T0, timedelta(minutes=10))

    assert store.close_items(REPO, 45583, T0 + timedelta(minutes=1), keep="enqueue") == 2
    assert store.open_items(REPO, 45583) == [kept]


def test_a_grade_posted_twice_changes_nothing(store):
    assert store.put_fact(REPO, 46389, HEAD, "finder", "clear", T0)
    assert not store.put_fact(REPO, 46389, HEAD, "finder", "clear", T0 + timedelta(minutes=5))

    assert store.facts(REPO, 46389, HEAD) == {"finder": "clear"}


def test_a_withdrawal_supersedes_an_earlier_tap(store):
    store.put_fact(REPO, 45911, HEAD, "tap", "tapped", T0)

    assert store.put_fact(REPO, 45911, HEAD, "tap", "withdrawn", T0 + timedelta(minutes=1))
    assert store.facts(REPO, 45911, HEAD)["tap"] == "withdrawn"


def test_facts_are_per_head(store):
    store.put_fact(REPO, 45744, "aa11bb22cc33dd44ee55ff6677889900aabbccdd", "tap", "tapped", T0)

    assert store.facts(REPO, 45744, HEAD) == {}


def test_one_escalation_per_subject_and_cause(store):
    assert store.escalate("org/repo#45470", "verdict overdue", "root", T0)
    assert not store.escalate("org/repo#45470", "verdict overdue", "root", T0 + timedelta(minutes=20))
    assert store.escalate("org/repo#45470", "restack overdue", "root", T0 + timedelta(minutes=20))

    assert [row["cause"] for row in store.escalations("org/repo#45470")] == ["verdict overdue", "restack overdue"]


def test_a_worker_silent_fifteen_minutes_is_stale_and_its_items_come_back(store):
    item = store.open_item("fix-red", REPO, 45651, HEAD, "lane", T0, timedelta(minutes=20))
    store.heartbeat("lane-7", 1, T0, item=item.id)
    store.heartbeat("lane-8", 1, T0 + timedelta(minutes=14))

    assert store.stale_workers(T0 + timedelta(minutes=14, seconds=59)) == []
    assert store.stale_workers(T0 + timedelta(minutes=15)) == ["lane-7"]

    released = store.release_worker("lane-7")

    assert [held.id for held in released] == [item.id]
    assert store.open_items(REPO, 45651)[0].worker is None
    assert [row["name"] for row in store.workers()] == ["lane-8"]


def test_a_heartbeat_claims_the_item_for_the_worker(store):
    item = store.open_item("restack", REPO, 45580, HEAD, "lane", T0, timedelta(minutes=20))
    store.heartbeat("lane-3", 2, T0 + timedelta(minutes=1), item=item.id, context_left=120_000)

    assert store.open_items(REPO, 45580)[0].worker == "lane-3"
    assert store.workers()[0]["context_left"] == 120_000


def test_a_second_supervisor_takes_the_lease_and_the_first_exits_on_renewal(store):
    first = store.acquire_lease("drive", "0.8.0", 101, T0)
    second = store.acquire_lease("drive", "0.8.1", 202, T0 + timedelta(minutes=1))

    assert (first.generation, second.generation) == (1, 2)
    store.renew_lease("drive", second.generation, T0 + timedelta(minutes=2))
    with pytest.raises(StaleGeneration, match="generation 1 is behind 2"):
        store.renew_lease("drive", first.generation, T0 + timedelta(minutes=2))
    assert store.lease("drive").pid == 202


def test_a_rule_row_carries_a_whole_check_or_none(store):
    store.put_rule("3f34533", "every PR needs a tap", "2026-10-22T09:49:00Z", "predicate", "gates.tapped")
    store.put_rule("9f51e67", "ports need no tap unless they conflict with the plan", "2026-10-29T19:46:00Z")

    assert store.unchecked_rules() == ["9f51e67"]
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        store.put_rule("broken", "words", "2026-10-30T00:00:00Z", "predicate", None)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        store.put_rule("broken", "words", "2026-10-30T00:00:00Z", "vibe", "x")


def test_a_cursor_never_moves_backwards(store):
    assert store.cursor("cci") == 0
    store.advance("cci", 239378)
    store.advance("cci", 239000)

    assert store.cursor("cci") == 239378


def test_a_pr_row_is_upserted_by_repo_and_number(store):
    store.put_pr(REPO, 45757, "aa11bb22cc33dd44ee55ff6677889900aabbccdd", "dev", "unregistered-lane", "grading", T0)
    store.put_pr(REPO, 45757, HEAD, "dev", "unregistered-lane", "ready", T0 + timedelta(hours=11))

    rows = store.prs(REPO)
    assert [(row["number"], row["head"], row["state"]) for row in rows] == [(45757, HEAD, "ready")]


def test_the_store_opens_a_file_in_wal_mode(tmp_path):
    store = Store.open(tmp_path / "drive.sqlite")

    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.open_item("enqueue", REPO, 1, HEAD, "supervisor", T0, timedelta(minutes=10))
    assert Store.open(tmp_path / "drive.sqlite").open_items(REPO, 1)[0].kind == "enqueue"


OLD_LANES = """CREATE TABLE lanes (
  name TEXT PRIMARY KEY, brief TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL, role TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('launching', 'desired', 'held', 'retired')),
  since TEXT NOT NULL, heard TEXT, handoff TEXT, prs TEXT NOT NULL DEFAULT '[]',
  predecessor TEXT, reason TEXT, strikes INTEGER NOT NULL DEFAULT 0, asked TEXT, flushed TEXT, retired_by TEXT
)"""


def test_a_store_file_from_an_older_schema_gains_the_columns_it_lacks(tmp_path):
    path = tmp_path / "lr.sqlite"
    old = sqlite3.connect(path)
    old.execute(OLD_LANES)
    old.execute("INSERT INTO lanes (name, brief, model, effort, role, state, since) VALUES ('l-1', '/b.md', 'opus', 'xhigh', 'lane', 'desired', ?)", (stamp(T0),))
    old.commit()
    old.close()

    store = Store.open(path)

    assert store.lane("l-1").waiting is None and store.lane("l-1").answered is None
    assert {"waiting", "answered"} <= {row["name"] for row in store.db.execute("PRAGMA table_info(lanes)")}


def test_opening_a_current_store_again_changes_nothing(tmp_path):
    path = tmp_path / "lr.sqlite"
    Store.open(path).want_lane("l-1", "/b.md", "opus", "xhigh", "lane", "desired", T0)

    assert Store.open(path).lane("l-1").name == "l-1"
