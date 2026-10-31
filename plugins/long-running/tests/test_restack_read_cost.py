from __future__ import annotations

import json
import threading

import pytest
from test_desk_runner import LANE, actions, config, landing_of, runner_module, shell


@pytest.mark.parametrize("history", [32, 10_000])
def test_restack_verification_avoids_terminal_history(shell, config, tmp_path, monkeypatch, history):
    landing = landing_of(shell, config, tmp_path)
    book = landing.book
    lane = runner_module.lane_container(LANE)
    book.ensure(lane, LANE)
    for container, kind in ((runner_module.LANDING, "bus"), (lane, "relay")):
        with book.store.transaction(container) as incident:
            for index in range(history):
                action = incident.accept(f"restack:{index + 1000}:100", kind, "unrelated terminal route", "history", shell.now())
                action.status = "verified" if index % 2 else "failed"
            incident.accept("restack:101:100", kind, "still on the landed branch", "current", shell.now())
            incident.accept("unrelated-active", kind, "not a restack", "current", shell.now())
    rows = {"100": {"branch": "old-branch"}, "101": {"state": "open", "base": "old-branch", "head": "abcdef1234"}}
    costs = {"action_dtos": 0, "sql_opcodes": 0, "read_connections": 0, "write_connections": 0}
    owner = threading.get_ident()
    construct, connect = actions.Action.load.__func__, book.store._connect
    statements = []

    def built(cls, record):
        if threading.get_ident() == owner:
            costs["action_dtos"] += 1
        return construct(cls, record)

    def opened(path, writable=False):
        connection = connect(path, writable)
        costs["write_connections" if writable else "read_connections"] += 1
        connection.set_trace_callback(statements.append)

        def progress():
            costs["sql_opcodes"] += 1
            return 0

        connection.set_progress_handler(progress, 1)
        return connection

    monkeypatch.setattr(actions.Action, "load", classmethod(built))
    monkeypatch.setattr(book.store, "_connect", opened)
    shell.calls.clear()
    with book.store.reading():
        landing.verify_restacks(rows)
        first = costs.copy()
        landing.verify_restacks(rows)
    print(json.dumps({"history_per_container": history, "first": first, "twice": costs}))
    assert first["action_dtos"] == 4, f"restack verification decoded {first['action_dtos']} actions instead of 4 active candidates"
    assert costs["action_dtos"] == 8
    assert first["sql_opcodes"] < 3000 and costs["sql_opcodes"] < 6000
    assert costs["read_connections"] == 2 and costs["write_connections"] == 0
    assert any("INDEXED BY action_kind_status" in statement for statement in statements)
    assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for statement in statements)
    assert not shell.calls


@pytest.mark.parametrize("status", actions.STATUSES)
@pytest.mark.parametrize("kind", ["bus", "relay"])
@pytest.mark.parametrize("change", ["push", "base", "landed"])
def test_restack_active_status_selection_preserves_settlement(shell, config, tmp_path, status, kind, change):
    landing = landing_of(shell, config, tmp_path)
    book = landing.book
    container = runner_module.LANDING if kind == "bus" else runner_module.lane_container(LANE)
    book.ensure(container, LANE)
    key = "restack:101:100"
    with book.store.transaction(container) as incident:
        action = incident.accept(key, kind, "the original route", "owner", shell.now())
        action.status = status
        action.response = {"original": "response"}
        action.verification_receipt = {"original": "receipt"}
    row = {"head": "abcdef1234", "state": "landed" if change == "landed" else "open", "base": "dev" if change == "base" else "old-branch"}
    landing.verify_restacks({"100": {"branch": "old-branch"}, "101": row})
    observed = book.action(container, key)
    settles = status in actions.OPEN and change != "push"
    assert observed.status == ("verified" if settles else status)
    assert observed.verification_receipt == ({"head": row["head"], "state": row["state"], "at": book.stamp()} if settles else {"original": "receipt"})
    assert observed.response == {"original": "response"} and observed.target == "the original route"


@pytest.mark.parametrize("kind", ["bus", "relay"])
def test_restack_selected_malformed_key_still_raises(shell, config, tmp_path, kind):
    landing = landing_of(shell, config, tmp_path)
    container = runner_module.LANDING if kind == "bus" else runner_module.lane_container(LANE)
    landing.book.ensure(container, LANE)
    with landing.book.store.transaction(container) as incident:
        incident.accept("restack:malformed", kind, "selected invalid route", "owner", shell.now())
    with pytest.raises(ValueError, match="not enough values"):
        landing.verify_restacks({})
