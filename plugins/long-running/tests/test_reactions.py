from datetime import UTC, datetime

import pytest
from conftest import edit

from drive import reactions, slack, state

ASKED = "2026-11-16T11:00:00-08:00"


@pytest.mark.parametrize("operation", ["moved", "answered"])
def test_reaction_calculation_visits_each_job_once(demo: str, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    data = state.load(demo)
    for order_id in ("o1", "o2", "o3"):
        data["orders"][order_id] = state.new_order(f"order {order_id}", "terminal", None, {}, ASKED) | {"thread": ["C1", "1.1"], "last_line": ASKED}
    visited = []

    class Jobs(dict):
        def values(self):
            for key, value in self.items():
                visited.append(key)
                assert len(visited) <= len(self)
                yield value

    data["jobs"] = Jobs(j1={"order": "o1", "state": "done"}, j2={"order": "o3", "state": "running"})
    monkeypatch.setattr(state, "load", lambda *args: data)
    monkeypatch.setattr(slack, "cc_slack", lambda *args: {})
    if operation == "moved":
        assert reactions.moved(demo, datetime.now(UTC)) == ["his message C1/1.1 now carries construction"]
    else:
        assert reactions.answered(demo, "C1", "1.1") == "construction"
    assert visited == ["j1", "j2"]


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
