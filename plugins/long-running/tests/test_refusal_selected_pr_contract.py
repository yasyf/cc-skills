import json
import sqlite3

import pytest

from long_running import actions
from long_running.model import STATUSES
from test_desk_runner import config, landing_of, runner_module, shell


HEAD = "a" * 40
ROWS = {"1": {"head": HEAD, "state": "open"}}


def seeded(landing, entries):
    with landing.book.store.transaction(runner_module.LANDING, generation=0) as current:
        for key, target, status in entries:
            action = current.accept(key, "bus", target, "owner", landing.runner.now())
            action.status = status


def called(landing, route, members=None, rows=None):
    members = {"1"} if members is None else members
    rows = ROWS if rows is None else rows
    if route == "gate":
        return landing.refused_at_head(members, rows)
    landing.ledger_rows = {pr: rows[pr] for pr in members}
    landing.post_state(1.0)
    return "refused 1," in landing.runner.shell.states[-1]["text"]


@pytest.mark.parametrize("route", ["gate", "state"])
@pytest.mark.parametrize("status", STATUSES)
def test_actual_refusal_callers_preserve_every_action_status(shell, config, tmp_path, route, status):
    landing = landing_of(shell, config, tmp_path)
    seeded(landing, [("legacy-prefix:1:aaaaaaa:extra", '{"kind":"blocker","pr":"1"}', status)])
    assert called(landing, route)


@pytest.mark.parametrize("route", ["gate", "state"])
@pytest.mark.parametrize("target", [
    '{"kind":"blocker","pr":"1"}',
    '{"kind":"blocker","pr":1}',
    '{"kind":"note","kind":"blocker","pr":"1"}',
    '{"kind":"blocker","pr":"9","pr":"1"}',
    r'{"k\u0069nd":"blocker","p\u0072":"1"}',
    '{"kind":"blocker","pr":"1","text":"quote\\\" slash\\\\ snowman ☃"}',
    r'{"kind":"blocker","pr":"1","text":"NUL\u0000tail"}',
])
def test_actual_refusal_callers_keep_python_target_projection(shell, config, tmp_path, route, target):
    landing = landing_of(shell, config, tmp_path)
    seeded(landing, [("generic:1:aaaaaaa", target, "accepted")])
    assert called(landing, route)


@pytest.mark.parametrize("route", ["gate", "state"])
@pytest.mark.parametrize("target,error", [
    ("{", json.JSONDecodeError),
    ("null", AttributeError),
    ("[]", AttributeError),
    ('{"kind":"blocker"}', KeyError),
])
@pytest.mark.parametrize("first", [True, False], ids=["error-first", "matched-first"])
def test_actual_refusal_callers_preserve_ordinal_error_frontier(shell, config, tmp_path, route, target, error, first):
    landing = landing_of(shell, config, tmp_path)
    entries = [("invalid", target, "verified"), ("generic:1:aaaaaaa", '{"kind":"blocker","pr":"1"}', "failed")]
    seeded(landing, entries if first else list(reversed(entries)))
    if first:
        with pytest.raises(error):
            called(landing, route)
    else:
        assert called(landing, route)


@pytest.mark.parametrize("route", ["gate", "state"])
@pytest.mark.parametrize("length", [7, 12, 40])
def test_actual_refusal_callers_keep_generic_heads_and_ignore_stale_heads(shell, config, tmp_path, route, length):
    landing = landing_of(shell, config, tmp_path)
    seeded(landing, [
        ("generic:1:bbbbbbb", '{"kind":"blocker","pr":"1"}', "started"),
        (f"different-historical-prefix:1:{HEAD[:length]}:suffix", '{"kind":"blocker","pr":"1"}', "failed"),
    ])
    assert called(landing, route)
    assert not called(landing, route, rows={"1": {"head": "c" * 40, "state": "open"}})


def test_actual_refusal_state_collects_multiple_members_in_original_order(shell, config, tmp_path, monkeypatch):
    landing = landing_of(shell, config, tmp_path)
    seeded(landing, [
        ("generic:2:bbbbbbb", '{"kind":"blocker","pr":"2"}', "verified"),
        ("legacy-repeat:2:bbbbbbb", '{"kind":"blocker","pr":"2"}', "failed"),
        ("generic:1:aaaaaaa", '{"kind":"blocker","pr":"1"}', "accepted"),
        ("invalid-after-complete-members", "{", "completed"),
    ])
    landing.ledger_rows = ROWS | {"2": {"head": "b" * 40, "state": "open"}}
    original = runner_module.refused_members
    yielded = []

    def observed(*args):
        for pr in original(*args):
            yielded.append(pr)
            yield pr

    monkeypatch.setattr(runner_module, "refused_members", observed)
    landing.post_state(1.0)
    assert "refused 2," in shell.states[-1]["text"]
    assert yielded == ["2", "2", "1"]
    yielded.clear()
    assert landing.refused_at_head({"1", "2"}, landing.ledger_rows)
    assert yielded == ["2"]


@pytest.mark.parametrize("pr", [True, 1.5, -1, None, "0001", "1\u0000tail"])
def test_actual_refusal_gate_preserves_python_pr_string_identity(shell, config, tmp_path, pr):
    landing = landing_of(shell, config, tmp_path)
    text = str(pr)
    seeded(landing, [(f"generic:{text}:aaaaaaa", json.dumps({"kind": "blocker", "pr": pr}), "unverifiable")])
    assert landing.refused_at_head({text}, {text: {"head": HEAD}})


@pytest.mark.parametrize("route", ["gate", "state"])
def test_actual_refusal_callers_observe_old_sql_writer_without_status_filter(shell, config, tmp_path, route):
    landing = landing_of(shell, config, tmp_path)
    key = "historical:1:aaaaaaa"
    seeded(landing, [(key, '{"kind":"note","pr":"1"}', "completed")])
    store = landing.book.store
    with store.reading():
        assert not called(landing, route)
        with sqlite3.connect(store.path(runner_module.LANDING)) as writer:
            record = json.loads(writer.execute("SELECT record FROM action WHERE action_key=?", (key,)).fetchone()[0])
            record["target"] = '{"kind":"blocker","pr":"1"}'
            record["status"] = "verified"
            writer.execute("UPDATE action SET status=?,record=? WHERE action_key=?", ("verified", json.dumps(record), key))
        assert called(landing, route)
        with pytest.raises(ValueError, match="rollback"):
            with store.transaction(runner_module.LANDING) as current:
                current.actions[key].target = '{"kind":"note"}'
                raise ValueError("rollback")
        assert called(landing, route)
        with sqlite3.connect(store.path(runner_module.LANDING)) as writer:
            writer.execute("DELETE FROM action WHERE action_key=?", (key,))
        assert not called(landing, route)


@pytest.mark.parametrize("route", ["gate", "state"])
@pytest.mark.parametrize("history", [32, 10_000])
def test_actual_refusal_callers_do_not_load_unrelated_pr_history(shell, config, tmp_path, monkeypatch, route, history):
    landing = landing_of(shell, config, tmp_path)
    entries = [(f"other:{index + 10}:bbbbbbb", json.dumps({"kind": "note" if index % 2 else "blocker", "pr": str(index + 10)}), STATUSES[index % len(STATUSES)]) for index in range(history)]
    seeded(landing, entries + [("generic:1:aaaaaaa", '{"kind":"blocker","pr":"1"}', "unverifiable")])
    load = actions.Action.load.__func__
    connect = sqlite3.connect
    uri = landing.book.store.path(runner_module.LANDING).absolute().as_uri() + "?mode=ro"
    costs = {"dtos": 0, "vm": 0}

    def loaded(cls, record):
        costs["dtos"] += 1
        return load(cls, record)

    def connected(database, *args, **kwargs):
        connection = connect(database, *args, **kwargs)
        if str(database) == uri:
            def progress():
                costs["vm"] += 1
                return 0
            connection.set_progress_handler(progress, 1)
        return connection

    monkeypatch.setattr(actions.Action, "load", classmethod(loaded))
    monkeypatch.setattr(sqlite3, "connect", connected)
    assert called(landing, route)
    print(json.dumps({"route": route, "history": history, "costs": costs, "scope": "selected PR, fixed matching history and ambiguity; no physical IO claim"}))
    assert costs["dtos"] <= 2, f"{route} loaded {costs['dtos']} refusal Action records for {history} unrelated records"
    assert costs["vm"] < 1000, costs
