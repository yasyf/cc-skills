import json
import sqlite3

import pytest

from long_running import sqlite_store
from test_desk_runner import config, landing_of, runner_module, shell
from test_refusal_selected_pr_contract import HEAD, called, seeded


@pytest.mark.parametrize("route", ["gate", "state"])
def test_refusal_projection_indexless_then_manual_index_with_old_writer(shell, config, tmp_path, route):
    landing = landing_of(shell, config, tmp_path)
    seeded(landing, [("generic:1:aaaaaaa", '{"kind":"note","pr":"1"}', "accepted")])
    store = landing.book.store
    path = store.path(runner_module.LANDING)
    with sqlite3.connect(path) as writer:
        writer.execute("DROP INDEX action_refusal_pr")
    with store.reading():
        assert not called(landing, route)
        with sqlite3.connect(path) as writer:
            before = writer.execute("PRAGMA user_version").fetchone()[0]
            writer.execute(f"CREATE INDEX action_refusal_pr ON action((coalesce(({sqlite_store._REFUSAL_PR_KEY}), X'00')), ordinal) WHERE kind = 'bus'")
            record = json.loads(writer.execute("SELECT record FROM action WHERE action_key='generic:1:aaaaaaa'").fetchone()[0])
            record["target"] = '{"kind":"blocker","pr":"1"}'
            writer.execute("UPDATE action SET record=? WHERE action_key='generic:1:aaaaaaa'", (json.dumps(record),))
            assert writer.execute("PRAGMA user_version").fetchone()[0] == before
        assert called(landing, route)
        with sqlite3.connect(path) as writer:
            writer.execute("DELETE FROM action WHERE action_key='generic:1:aaaaaaa'")
        assert not called(landing, route)


@pytest.mark.parametrize("route", ["gate", "state"])
def test_indexless_refusal_read_preserves_schema(shell, config, tmp_path, route):
    landing = landing_of(shell, config, tmp_path)
    seeded(landing, [("generic:1:aaaaaaa", '{"kind":"blocker","pr":"1"}', "accepted")])
    path = landing.book.store.path(runner_module.LANDING)
    with sqlite3.connect(path) as writer:
        writer.execute("DROP INDEX action_refusal_pr")
        before = writer.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name").fetchall()
    assert called(landing, route)
    with sqlite3.connect(path) as reader:
        assert reader.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name").fetchall() == before


@pytest.mark.parametrize("target,member", [
    ('{"kind":"blocker","pr":""}', ""),
    ('{"kind":"blocker","pr":9223372036854775807}', "9223372036854775807"),
    ('{"kind":"blocker","pr":-9223372036854775808}', "-9223372036854775808"),
    ('{"kind":"blocker","pr":9223372036854775808}', "9223372036854775808"),
    ('{"kind":"blocker","pr":"1\\u0000tail"}', "1\x00tail"),
])
def test_refusal_projection_sentinel_and_numeric_boundaries(shell, config, tmp_path, target, member):
    landing = landing_of(shell, config, tmp_path)
    seeded(landing, [("generic:note:aaaaaaa", '{"kind":"note"}', "accepted"), (f"generic:{member}:aaaaaaa", target, "accepted")])
    assert landing.refused_at_head({member}, {member: {"head": HEAD}})


@pytest.mark.parametrize("history", [32, 10_000])
def test_refusal_projection_natural_plan_is_indexed(shell, config, tmp_path, monkeypatch, history):
    landing = landing_of(shell, config, tmp_path)
    seeded(landing, [(f"other:{i}:bbbbbbb", json.dumps({"kind": "blocker", "pr": str(i + 10)}), "accepted") for i in range(history)] + [("generic:1:aaaaaaa", '{"kind":"blocker","pr":"1"}', "accepted")])
    uri = landing.book.store.path(runner_module.LANDING).absolute().as_uri() + "?mode=ro"
    connect = sqlite3.connect
    queries = []

    def connected(database, *args, **kwargs):
        connection = connect(database, *args, **kwargs)
        if str(database) == uri:
            connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connected)
    assert called(landing, "gate")
    query, = [query for query in queries if query.startswith("SELECT action.record FROM (")]
    with connect(uri, uri=True) as reader:
        plan = [tuple(row) for row in reader.execute("EXPLAIN QUERY PLAN " + query)]
    print(json.dumps({"history": history, "plan": plan}))
    searches = [row[3] for row in plan if "SEARCH action USING INDEX action_refusal_pr" in row[3]]
    assert len(searches) == 1, plan
    assert not any("SCAN action" in row[3] for row in plan), plan
