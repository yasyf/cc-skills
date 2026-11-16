from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from conftest import edit

from drive import reactions, slack, state

ASKED = "2026-11-16T11:00:00-08:00"


@pytest.mark.parametrize("operation", ["moved", "answered"])
def test_reaction_calculation_uses_indexed_rows_without_loading_the_drive(demo: str, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    with edit(demo) as data:
        for order_id in ("o1", "o2", "o3"):
            data["orders"][order_id] = state.new_order(f"order {order_id}", "terminal", None, {}, ASKED) | {"thread": ["C1", "1.1"], "last_line": ASKED}
        data["jobs"]["j9"] = state.new_job("o3", "builder", "build it", [], ASKED) | {"state": "running"}
    connect = state.connect
    statements = []

    def traced(path, seconds=None):
        db = connect(path, seconds)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(state, "connect", traced)
    monkeypatch.setattr(state, "load", lambda *args: pytest.fail("reaction calculation must not load the drive"))
    monkeypatch.setattr(state, "stored", lambda *args: pytest.fail("reaction calculation must not read whole collections"))
    monkeypatch.setattr(slack, "cc_slack", lambda *args: {})
    if operation == "moved":
        assert reactions.moved(demo, datetime.now(UTC)) == ["his message C1/1.1 now carries construction"]
    else:
        assert reactions.answered(demo, "C1", "1.1") == "construction"
    with closing(connect(state.database_path(demo))) as db:
        plans = [row[3] for statement in statements if statement.startswith("SELECT") for row in db.execute("EXPLAIN QUERY PLAN " + statement)]
    expected = ("running_reaction_jobs", "open_reaction_orders", "closed_reaction_orders") if operation == "moved" else ("reaction_threads", "running_reaction_jobs")
    for index in expected:
        assert any(f"USING INDEX {index}" in step for step in plans), plans


def test_reaction_snapshot_keeps_only_eligible_orders_and_current_work_in_stored_order(demo: str) -> None:
    moment = datetime.now(UTC)
    with edit(demo) as data:
        for order in data["orders"].values():
            order["thread"] = None
        for order_id, status, updated in (("o1", "done", moment - timedelta(days=2)), ("o2", "working", moment - timedelta(days=2)), ("o3", "done", moment), ("o4", "dropped", moment)):
            data["orders"][order_id] = state.new_order(f"order {order_id}", "terminal", None, {}, ASKED) | {"thread": ["C1", order_id], "state": status, "updated": state.stamp(updated)}
        for job in data["jobs"].values():
            job["state"] = "done"
        data["jobs"]["j9"] = state.new_job("o2", "builder", "build it", [], ASKED) | {"state": "running"}
    orders, running = state.reaction_snapshot(demo, moment - reactions.RECENT)
    assert list(orders) == ["o2", "o3", "o4"]
    assert running == {"o2"}
    assert state.reaction_thread(demo, "C1", "o1") == ({"o1": {"state": "done", "last_line": None}}, {"o2"})
    assert state.reaction_thread(demo, "C1", "missing") == ({}, {"o2"})


def test_his_messages_reaction_follows_its_orders_state_and_moves_once_each_change(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: calls.append(argv) or {})
    with edit(demo) as data:
        for order_id, thread, order_state, line in (("o1", "1.1", "working", ASKED), ("o2", "2.2", "done", ASKED), ("o3", "3.3", "open", ASKED), ("o4", "4.4", "open", None)):
            data["orders"][order_id] = state.new_order(f"order {order_id}", "terminal", None, {}, ASKED) | {"thread": ["C1", thread], "state": order_state, "last_line": line, "updated": state.stamp()}
        data["jobs"]["j9"] = state.new_job("o1", "builder", "build it", [], ASKED) | {"state": "running"}
    moved = reactions.moved(demo, datetime.now(UTC))
    assert sorted(moved) == ["his message C1/1.1 now carries construction", "his message C1/2.2 now carries white_check_mark", "his message C1/3.3 now carries no reaction"]
    reacted = {argv[4]: argv[6] for argv in calls if argv[0] == "react"}
    assert reacted == {"1.1": "construction", "2.2": "white_check_mark"}
    assert ("unreact", "--channel", "C1", "--ts", "1.1", "--name", "eyes") in calls and not any(argv[4] == "4.4" for argv in calls)
    calls.clear()
    assert reactions.moved(demo, datetime.now(UTC)) == [] and calls == []
    with edit(demo) as data:
        data["jobs"]["j9"]["state"] = "done"
    assert reactions.moved(demo, datetime.now(UTC)) == ["his message C1/1.1 now carries no reaction"]
    assert ("unreact", "--channel", "C1", "--ts", "1.1", "--name", "construction") in calls


def test_a_post_answering_his_message_without_ending_it_takes_its_construction_off_unless_work_keeps_running(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: calls.append(argv) or {})
    assert reactions.answered(demo, "C0CJ378LSBT", "1794857634.488009") == "no reaction"
    assert ("unreact", "--channel", "C0CJ378LSBT", "--ts", "1794857634.488009", "--name", "construction") in calls and not any(argv[0] == "react" for argv in calls)
    calls.clear()
    assert reactions.answered(demo, "C0CJ378LSBT", "1794857634.488009", working=True) == "construction"
    assert ("react", "--channel", "C0CJ378LSBT", "--ts", "1794857634.488009", "--name", "construction") in calls


def test_a_post_closing_his_order_leaves_the_check_in_the_same_call_and_a_known_step_is_the_only_one_taken_away(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(slack, "cc_slack", lambda *argv: calls.append(argv) or {})
    with edit(demo) as data:
        data["orders"]["o5"] = state.new_order("order o5", "terminal", None, {}, ASKED) | {"thread": ["C1", "5.5"], "state": "working", "last_line": ASKED, "updated": state.stamp()}
        data["jobs"]["j5"] = state.new_job("o5", "builder", "build it", [], ASKED) | {"state": "running"}
    assert reactions.answered(demo, "C1", "5.5") == "construction"
    with edit(demo) as data:
        data["jobs"]["j5"]["state"] = "done"
        data["orders"]["o5"]["state"] = "done"
    calls.clear()
    assert reactions.answered(demo, "C1", "5.5") == "white_check_mark"
    assert calls == [("unreact", "--channel", "C1", "--ts", "5.5", "--name", "construction"), ("react", "--channel", "C1", "--ts", "5.5", "--name", "white_check_mark")]
    calls.clear()
    assert reactions.moved(demo, datetime.now(UTC)) == [] and calls == []
