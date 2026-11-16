from datetime import UTC, datetime

import pytest
from conftest import edit

from drive import reactions, slack, state

ASKED = "2026-11-16T11:00:00-08:00"


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
