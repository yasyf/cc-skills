import json
import sqlite3
from datetime import datetime, timezone

import pytest

from long_running import actions
from long_running.book import Book


NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
CONTAINER = "desk-landing"


def populated(root, targets, *, index_present=True):
    store = actions.Store(root)
    incident = actions.Incident(CONTAINER, "owner", 0, actions.stamp(NOW), {})
    for index, target in enumerate(targets):
        key = str(index)
        incident.accept(key, "bus", target, None, NOW)
        incident.start(key, NOW)
        incident.complete(key, {"posted": f"#{index + 41}"})
    store.create(incident)
    if not index_present:
        with sqlite3.connect(store.path(CONTAINER)) as writer:
            writer.execute("DROP INDEX action_blocker_candidates")
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
@pytest.mark.parametrize("index_present", [True, False], ids=["indexed", "indexless"])
def test_blocker_candidates_keep_python_kind_semantics(tmp_path, target, expected, index_present):
    store = populated(tmp_path, [target], index_present=index_present)
    found = Book(store, None).blocker_candidates(CONTAINER)
    matches = [action for action in found if json.loads(action.target).get("kind") == "blocker"]
    assert [action.action_id for action in matches] == (["0"] if expected else [])



@pytest.mark.parametrize("target,error", [("[", json.JSONDecodeError), ("null", AttributeError), ("7", AttributeError), ('"scalar"', AttributeError), ("[]", AttributeError)])
@pytest.mark.parametrize("index_present", [True, False], ids=["indexed", "indexless"])
def test_ambiguous_or_invalid_bus_target_keeps_python_validation(tmp_path, target, error, index_present):
    store = populated(tmp_path, [target], index_present=index_present)
    [action] = store.blocker_candidates(CONTAINER)
    with pytest.raises(error):
        json.loads(action.target).get("kind")


@pytest.mark.parametrize("index_present", [True, False], ids=["indexed", "indexless"])
def test_candidate_publication_order_and_legacy_sql_changes_are_immediate(tmp_path, index_present):
    store = populated(tmp_path, ['{"kind":"note"}', '{"kind":"blocker","pr":"30"}', '{"kind":"blocker","pr":"29"}'], index_present=index_present)
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


def test_indexless_reader_preserves_schema_and_publication_order(tmp_path):
    store = populated(tmp_path, ['{"kind":"blocker","pr":"30"}', '{"kind":"note"}', '{"kind":"blocker","pr":"29"}'], index_present=False)
    with sqlite3.connect(store.path(CONTAINER)) as observer:
        before = observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall()
    assert [action.action_id for action in Book(store, None).blocker_candidates(CONTAINER)] == ["0", "2"]
    with sqlite3.connect(store.path(CONTAINER)) as observer:
        assert observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall() == before
        assert not observer.execute("SELECT 1 FROM sqlite_master WHERE name='action_blocker_candidates'").fetchall()


@pytest.mark.parametrize("text", ["kind", "snowman ☃", "ordinary history"])
@pytest.mark.parametrize("index_present", [True, False], ids=["indexed", "indexless"])
def test_canonical_completed_notes_are_not_blocker_candidates(tmp_path, text, index_present):
    store = populated(tmp_path, [json.dumps({"lane": "archive", "pr": "29", "text": text, "kind": "note"})], index_present=index_present)
    assert store.blocker_candidates(CONTAINER) == []


@pytest.mark.parametrize("key", ['"target"', r'"t\u0061rget"'])
@pytest.mark.parametrize("index_present", [True, False], ids=["indexed", "indexless"])
def test_outer_record_duplicate_target_uses_python_last_value(tmp_path, key, index_present):
    store = populated(tmp_path, ['{"kind":"note"}'], index_present=index_present)
    with sqlite3.connect(store.path(CONTAINER)) as writer:
        encoded = writer.execute("SELECT record FROM action").fetchone()[0]
        last = json.dumps('{"kind":"blocker","pr":"29"}')
        writer.execute("UPDATE action SET record=?", (encoded[:-1] + "," + key + ":" + last + "}",))
    [found] = store.blocker_candidates(CONTAINER)
    assert json.loads(found.target)["kind"] == "blocker"


@pytest.mark.parametrize("history", [32, 10_000])
def test_actual_unforced_reader_uses_partial_index_with_bounded_work(tmp_path, monkeypatch, history):
    store = populated(tmp_path, ['{"kind":"note"}'] * history + ['{"kind":"blocker"}'])
    connect = sqlite3.connect
    construct = actions.Action.load.__func__
    costs = {"action_dtos": 0, "sql_vm": 0}
    statements = []

    def built(cls, record):
        costs["action_dtos"] += 1
        return construct(cls, record)

    def connected(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)

        def progress():
            costs["sql_vm"] += 1
            return 0

        connection.set_progress_handler(progress, 1)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connected)
    monkeypatch.setattr(actions.Action, "load", classmethod(built))
    assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == [str(history)]
    [query] = [sql for sql in statements if sql.startswith("SELECT record FROM action WHERE ")]
    assert "INDEXED BY" not in query
    assert costs["action_dtos"] == 1
    assert 0 < costs["sql_vm"] < 256
    with connect(store.path(CONTAINER)) as connection:
        plan = connection.execute("EXPLAIN QUERY PLAN " + query).fetchall()
    assert any("USING INDEX action_blocker_candidates" in row[3] for row in plan)
    print(json.dumps({"history": history, "costs": costs, "plan": plan, "query": query}))
