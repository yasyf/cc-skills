import json
import sqlite3
from datetime import datetime, timezone

import pytest

from long_running import actions
from long_running.book import Book


NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
CONTAINER = "desk-landing"


def populated(root, targets):
    store = actions.Store(root)
    incident = actions.Incident(CONTAINER, "owner", 0, actions.stamp(NOW), {})
    for index, target in enumerate(targets):
        key = str(index)
        incident.accept(key, "bus", target, None, NOW)
        incident.start(key, NOW)
        incident.complete(key, {"posted": f"#{index + 41}"})
    store.create(incident)
    return store


@pytest.mark.parametrize("target,expected", [
    ('{"kind":"blocker","pr":"29"}', True),
    ('{"kind":"note","text":"kind"}', False),
    ('{"kind":"note","text":"snowman \u2603"}', False),
    ('{"kind":"note","kind":"blocker"}', True),
    ('{"kind":"blocker","kind":"note"}', False),
    ('{"kind" : "note", "kind"\t: "blocker"}', True),
    (r'{"k\u0069nd":"blocker"}', True),
    (r'{"kind":"note","k\u0069nd":"blocker"}', True),
    (r'{"kind":"blocker","k\u0069nd":"note"}', False),
    (r'{"kind":"blocker","text":"NUL\u0000tail"}', True),
    ('{}', False),
])
def test_blocker_candidates_keep_python_kind_semantics(tmp_path, target, expected):
    store = populated(tmp_path, [target])
    found = Book(store, None).blocker_candidates(CONTAINER)
    matches = [action for action in found if json.loads(action.target).get("kind") == "blocker"]
    assert [action.action_id for action in matches] == (["0"] if expected else [])



@pytest.mark.parametrize("target,error", [("[", json.JSONDecodeError), ("null", AttributeError), ("7", AttributeError), ('"scalar"', AttributeError), ("[]", AttributeError)])
def test_ambiguous_or_invalid_bus_target_keeps_python_validation(tmp_path, target, error):
    store = populated(tmp_path, [target])
    [action] = store.blocker_candidates(CONTAINER)
    with pytest.raises(error):
        json.loads(action.target).get("kind")


def test_candidate_publication_order_and_legacy_sql_changes_are_immediate(tmp_path):
    store = populated(tmp_path, ['{"kind":"note"}', '{"kind":"blocker","pr":"30"}', '{"kind":"blocker","pr":"29"}'])
    with store.reading():
        assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == ["1", "2"]
        with sqlite3.connect(store.path(CONTAINER)) as writer:
            record = json.loads(writer.execute("SELECT record FROM action WHERE action_key='0'").fetchone()[0])
            record["target"] = '{"kind":"blocker","pr":"31"}'
            writer.execute("UPDATE action SET record=? WHERE action_key='0'", (json.dumps(record),))
        assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == ["0", "1", "2"]
        with sqlite3.connect(store.path(CONTAINER)) as writer:
            record["status"] = "verified"
            writer.execute("UPDATE action SET status='verified',record=? WHERE action_key='0'", (json.dumps(record),))
            writer.execute("DELETE FROM action WHERE action_key='1'")
        assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == ["2"]
        with pytest.raises(ValueError, match="private rollback"):
            with store.transaction(CONTAINER) as state:
                state.actions["2"].target = '{"kind":"note"}'
                raise ValueError("private rollback")
        assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == ["2"]
        with store.transaction(CONTAINER) as state:
            state.actions["2"].kind = "send"
            state.actions["2"].target = "["
        assert store.blocker_candidates(CONTAINER) == []
        assert store.action(CONTAINER, "2").target == "["


def test_old_creator_missing_index_is_not_silently_scanned(tmp_path):
    store = populated(tmp_path, ['{"kind":"blocker"}'])
    with sqlite3.connect(store.path(CONTAINER)) as writer:
        writer.execute("DROP INDEX action_blocker_candidates")
    with pytest.raises(sqlite3.OperationalError, match="action_blocker_candidates"):
        store.blocker_candidates(CONTAINER)


@pytest.mark.parametrize("text", ["kind", "snowman ☃", "ordinary history"])
def test_canonical_completed_notes_are_not_blocker_candidates(tmp_path, text):
    store = populated(tmp_path, [json.dumps({"lane": "archive", "pr": "29", "text": text, "kind": "note"})])
    assert store.blocker_candidates(CONTAINER) == []


@pytest.mark.parametrize("key", ['"target"', r'"t\u0061rget"'])
def test_outer_record_duplicate_target_uses_python_last_value(tmp_path, key):
    store = populated(tmp_path, ['{"kind":"note"}'])
    with sqlite3.connect(store.path(CONTAINER)) as writer:
        encoded = writer.execute("SELECT record FROM action").fetchone()[0]
        last = json.dumps('{"kind":"blocker","pr":"29"}')
        writer.execute("UPDATE action SET record=?", (encoded[:-1] + "," + key + ":" + last + "}",))
    [found] = store.blocker_candidates(CONTAINER)
    assert json.loads(found.target)["kind"] == "blocker"


def test_actual_reader_query_uses_the_pending_partial_index(tmp_path, monkeypatch):
    store = populated(tmp_path, ['{"kind":"note"}', '{"kind":"blocker"}'])
    connect = sqlite3.connect
    statements = []

    def connected(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connected)
    assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == ["1"]
    [query] = [sql for sql in statements if sql.startswith("SELECT record FROM action INDEXED BY action_blocker_candidates")]
    with connect(store.path(CONTAINER)) as connection:
        plan = connection.execute("EXPLAIN QUERY PLAN " + query).fetchall()
    assert any("USING INDEX action_blocker_candidates" in row[3] for row in plan)
